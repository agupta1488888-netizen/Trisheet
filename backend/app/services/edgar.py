"""Rate-limited, caching SEC EDGAR client.

Every request to sec.gov and data.sec.gov goes through this module. Nothing
else in the codebase constructs an EDGAR URL or issues an EDGAR request.

Requirements, all enforced here:
    - Send `User-Agent: Tearsheet <contact-email>`; SEC returns 403 without it.
    - Respect a global ceiling of 10 requests/second, via a shared token bucket
      rather than a per-process sleep.
    - Retry 429 and 5xx with exponential backoff, at most 3 attempts, honouring
      Retry-After when the response carries it.
    - Cache filing responses permanently — filings are immutable.

Limitation
    The token bucket is shared across every coroutine and every client instance
    in a process. It is not shared between operating-system processes. Running
    more than one backend process against EDGAR needs a shared limiter backend;
    until then, deploy the report pipeline as a single process.

Public interface
    pad_cik(cik) -> str
    unpad_cik(cik) -> str
    submissions_url(cik) / company_facts_url(cik) / company_concept_url(...)
    filing_index_url(cik, accession_no) / filing_document_url(...)
    EdgarClient.get_json(url, ttl) / .get_text(url, ttl) / .get_bytes(url, ttl)
    get_client() -> EdgarClient
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx

from app.config import (
    CIK_PAD_WIDTH,
    EDGAR_BACKOFF_BASE_SECONDS,
    EDGAR_CACHE_DIR_NAME,
    EDGAR_MAX_REQUESTS_PER_SECOND,
    EDGAR_MAX_RETRY_ATTEMPTS,
    EDGAR_REQUEST_TIMEOUT_SECONDS,
    SEC_COMPANY_CONCEPT_URL_TEMPLATE,
    SEC_COMPANY_FACTS_URL_TEMPLATE,
    SEC_COMPANY_LIST_BY_SIC_URL_TEMPLATE,
    SEC_FILING_DOCUMENT_URL_TEMPLATE,
    SEC_FILING_INDEX_URL_TEMPLATE,
    SEC_SUBMISSIONS_URL_TEMPLATE,
    get_settings,
)

logger = logging.getLogger(__name__)

HTTP_TOO_MANY_REQUESTS = 429
HTTP_NOT_FOUND = 404
HTTP_SERVER_ERROR_FLOOR = 500
HTTP_CLIENT_ERROR_FLOOR = 400

#: Cap on a Retry-After we will actually wait out before giving up.
MAX_RETRY_AFTER_SECONDS = 60.0


# --- Typed failures ---------------------------------------------------------


class EdgarError(Exception):
    """Base class for every EDGAR failure. Callers catch this, not httpx."""


class EdgarNotConfiguredError(EdgarError):
    """No contact email is set, so SEC would reject every request with 403."""


class EdgarNotFoundError(EdgarError):
    """EDGAR has no document at this URL. Not retried — it will not appear."""


class EdgarUnavailableError(EdgarError):
    """EDGAR could not be reached, or kept failing across every attempt."""


# --- CIK and URL helpers ----------------------------------------------------


def pad_cik(cik: str | int) -> str:
    """Zero-pads a CIK to the 10 digits the JSON APIs require."""
    digits = str(cik).strip().removeprefix("CIK").lstrip("0")
    if not digits.isdigit():
        message = f"CIK is not numeric: {cik!r}"
        raise ValueError(message)
    return digits.zfill(CIK_PAD_WIDTH)


def unpad_cik(cik: str | int) -> str:
    """Strips leading zeros. Archive paths address CIKs in integer form."""
    return str(int(pad_cik(cik)))


def submissions_url(cik: str | int) -> str:
    return SEC_SUBMISSIONS_URL_TEMPLATE.format(cik=pad_cik(cik))


def company_facts_url(cik: str | int) -> str:
    return SEC_COMPANY_FACTS_URL_TEMPLATE.format(cik=pad_cik(cik))


def company_concept_url(cik: str | int, taxonomy: str, tag: str) -> str:
    return SEC_COMPANY_CONCEPT_URL_TEMPLATE.format(
        cik=pad_cik(cik), taxonomy=taxonomy, tag=tag
    )


def company_list_by_sic_url(sic_code: str, form: str, count: int) -> str:
    """Filers sharing a SIC code, as EDGAR's company browser lists them.

    Answers in Atom, which carries each filer's CIK — the only EDGAR endpoint
    that will enumerate an industry. Used by m08's third rung.

    Raises:
        ValueError: the SIC code is not numeric. EDGAR would answer a
            free-text search instead, silently returning the wrong industry.
    """
    cleaned = sic_code.strip()
    if not cleaned.isdigit():
        message = f"SIC code is not numeric: {sic_code!r}"
        raise ValueError(message)
    return SEC_COMPANY_LIST_BY_SIC_URL_TEMPLATE.format(
        sic=cleaned, form=form, count=count
    )


def _accession_forms(accession_no: str) -> tuple[str, str]:
    """Returns (dashed, undashed) forms of an accession number."""
    undashed = accession_no.replace("-", "")
    expected_length = 18
    if len(undashed) != expected_length or not undashed.isdigit():
        message = f"Malformed accession number: {accession_no!r}"
        raise ValueError(message)
    dashed = f"{undashed[:10]}-{undashed[10:12]}-{undashed[12:]}"
    return dashed, undashed


def filing_index_url(cik: str | int, accession_no: str) -> str:
    dashed, undashed = _accession_forms(accession_no)
    return SEC_FILING_INDEX_URL_TEMPLATE.format(
        cik_int=unpad_cik(cik), accession_nodash=undashed, accession_dashed=dashed
    )


def filing_document_url(cik: str | int, accession_no: str, document: str) -> str:
    _, undashed = _accession_forms(accession_no)
    return SEC_FILING_DOCUMENT_URL_TEMPLATE.format(
        cik_int=unpad_cik(cik), accession_nodash=undashed, document=document
    )


# --- Rate limiting ----------------------------------------------------------


class TokenBucket:
    """Async token bucket. Shared by every caller in the process.

    A per-request sleep cannot hold a global ceiling once requests are issued
    concurrently, which is why this is a bucket and not a delay.
    """

    def __init__(self, rate_per_second: float) -> None:
        if rate_per_second <= 0:
            message = "Rate must be positive"
            raise ValueError(message)
        self._rate = float(rate_per_second)
        self._capacity = float(rate_per_second)
        self._tokens = float(rate_per_second)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Blocks until a token is available, then consumes it."""
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity,
                    self._tokens + (now - self._updated) * self._rate,
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait_for = (1.0 - self._tokens) / self._rate
            # Released outside the lock so waiters do not serialise on it.
            await asyncio.sleep(wait_for)


#: The process-wide limiter. Every EdgarClient shares it by default.
_SHARED_LIMITER = TokenBucket(EDGAR_MAX_REQUESTS_PER_SECOND)


# --- Response cache ---------------------------------------------------------


class ResponseCache:
    """On-disk cache keyed by URL.

    Filings never change, so their entries have no expiry. Entries that do age
    — the ticker index, XBRL concepts — pass a ttl and are refetched past it.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path_for(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        # Two-level fan-out; a flat directory of 100k files is slow on Windows.
        return self._root / digest[:2] / f"{digest}.bin"

    def read(self, url: str, ttl: float | None) -> bytes | None:
        path = self._path_for(url)
        try:
            stat = path.stat()
        except OSError:
            return None
        if ttl is not None and (time.time() - stat.st_mtime) > ttl:
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None

    def write(self, url: str, body: bytes) -> None:
        path = self._path_for(url)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename so a crash cannot leave a truncated entry.
            temporary = path.with_suffix(".part")
            temporary.write_bytes(body)
            temporary.replace(path)
        except OSError:
            # A cache that cannot be written is a slowdown, not a failure.
            logger.warning("Could not write cache entry", extra={"url": url})


def default_cache_root() -> Path:
    return Path(__file__).resolve().parents[2] / EDGAR_CACHE_DIR_NAME


# --- Client -----------------------------------------------------------------


class EdgarClient:
    """Async EDGAR client. Construct once and reuse; it holds a connection pool.

    `transport` exists so tests can drive the client without network access.
    """

    def __init__(
        self,
        *,
        user_agent: str,
        cache: ResponseCache | None = None,
        limiter: TokenBucket | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._cache = (
            cache if cache is not None else ResponseCache(default_cache_root())
        )
        self._limiter = limiter if limiter is not None else _SHARED_LIMITER
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=EDGAR_REQUEST_TIMEOUT_SECONDS,
            transport=transport,
            follow_redirects=True,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_bytes(self, url: str, ttl: float | None = None) -> bytes:
        """Fetches a document, serving from cache when an entry is valid."""
        cached = self._cache.read(url, ttl)
        if cached is not None:
            logger.debug("EDGAR cache hit", extra={"url": url})
            return cached

        body = await self._fetch(url)
        self._cache.write(url, body)
        return body

    async def get_text(self, url: str, ttl: float | None = None) -> str:
        return (await self.get_bytes(url, ttl)).decode("utf-8", errors="replace")

    async def get_json(self, url: str, ttl: float | None = None) -> dict[str, Any]:
        body = await self.get_bytes(url, ttl)
        try:
            parsed: Any = json.loads(body)
        except json.JSONDecodeError as cause:
            message = f"EDGAR returned malformed JSON for {url}"
            raise EdgarUnavailableError(message) from cause
        if not isinstance(parsed, dict):
            message = f"Expected a JSON object from {url}"
            raise EdgarUnavailableError(message)
        return parsed

    async def _fetch(self, url: str) -> bytes:
        if not self._user_agent.strip().removeprefix("Tearsheet").strip():
            message = (
                "No SEC contact email is configured. Set EDGAR_CONTACT_EMAIL; "
                "SEC refuses requests that do not identify a contact."
            )
            raise EdgarNotConfiguredError(message)

        last_error: str = "no attempt was made"

        for attempt in range(EDGAR_MAX_RETRY_ATTEMPTS):
            await self._limiter.acquire()

            try:
                response = await self._client.get(url)
            except httpx.HTTPError as cause:
                last_error = str(cause)
                logger.warning(
                    "EDGAR request failed",
                    extra={"url": url, "attempt": attempt + 1, "error": last_error},
                )
                await self._backoff(attempt, retry_after=None)
                continue

            status = response.status_code

            if status == HTTP_NOT_FOUND:
                message = f"EDGAR has no document at {url}"
                raise EdgarNotFoundError(message)

            if status == HTTP_TOO_MANY_REQUESTS or status >= HTTP_SERVER_ERROR_FLOOR:
                last_error = f"HTTP {status}"
                logger.warning(
                    "EDGAR throttled or unavailable",
                    extra={"url": url, "status": status, "attempt": attempt + 1},
                )
                await self._backoff(
                    attempt, retry_after=_parse_retry_after(response)
                )
                continue

            if status >= HTTP_CLIENT_ERROR_FLOOR:
                message = f"EDGAR rejected the request for {url}: HTTP {status}"
                raise EdgarError(message)

            return response.content

        message = (
            f"EDGAR did not answer for {url} after "
            f"{EDGAR_MAX_RETRY_ATTEMPTS} attempts ({last_error})."
        )
        raise EdgarUnavailableError(message)

    @staticmethod
    async def _backoff(attempt: int, retry_after: float | None) -> None:
        """Waits before the next attempt. Retry-After always wins when present."""
        if attempt >= EDGAR_MAX_RETRY_ATTEMPTS - 1:
            return
        delay = (
            retry_after
            if retry_after is not None
            else EDGAR_BACKOFF_BASE_SECONDS * (2.0**attempt)
        )
        await asyncio.sleep(min(delay, MAX_RETRY_AFTER_SECONDS))


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Reads Retry-After as seconds. HTTP-date form is ignored deliberately."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


_client: EdgarClient | None = None


def get_client() -> EdgarClient:
    """Returns the shared client, building it from settings on first use."""
    global _client  # noqa: PLW0603 — one process-wide pooled client by design
    if _client is None:
        settings = get_settings()
        if not settings.edgar_configured:
            message = (
                "No SEC contact email is configured. Set EDGAR_CONTACT_EMAIL "
                "before requesting anything from EDGAR."
            )
            raise EdgarNotConfiguredError(message)
        _client = EdgarClient(user_agent=settings.edgar_user_agent)
    return _client


async def close_client() -> None:
    """Closes the shared client. Called on application shutdown."""
    global _client  # noqa: PLW0603 — mirrors get_client
    if _client is not None:
        await _client.aclose()
        _client = None
