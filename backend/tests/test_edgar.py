"""Tests for the EDGAR client: headers, rate limiting, retries and caching."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import pytest

from app.services.edgar import (
    EdgarClient,
    EdgarNotConfiguredError,
    EdgarNotFoundError,
    EdgarUnavailableError,
    ResponseCache,
    TokenBucket,
    filing_index_url,
    pad_cik,
    submissions_url,
    unpad_cik,
)

USER_AGENT = "Trisheet tests@example.com"


def _client(
    handler: object, tmp_path: Path, limiter: TokenBucket | None = None
) -> EdgarClient:
    return EdgarClient(
        user_agent=USER_AGENT,
        cache=ResponseCache(tmp_path),
        limiter=limiter if limiter is not None else TokenBucket(1_000),
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


# --- CIK and URL helpers ----------------------------------------------------


def test_pad_cik_accepts_int_and_padded_string() -> None:
    assert pad_cik(320193) == "0000320193"
    assert pad_cik("0000320193") == "0000320193"
    assert pad_cik("CIK0000320193") == "0000320193"


def test_unpad_cik_strips_leading_zeros() -> None:
    assert unpad_cik("0000320193") == "320193"


def test_pad_cik_rejects_non_numeric() -> None:
    with pytest.raises(ValueError, match="not numeric"):
        pad_cik("NKE")


def test_submissions_url_is_zero_padded() -> None:
    assert submissions_url(320187).endswith("CIK0000320187.json")


def test_filing_index_url_uses_integer_cik_and_both_accession_forms() -> None:
    url = filing_index_url("0000320187", "0000320187-24-000045")
    assert "/data/320187/000032018724000045/" in url
    assert url.endswith("0000320187-24-000045-index.htm")


def test_filing_index_url_rejects_malformed_accession() -> None:
    with pytest.raises(ValueError, match="Malformed accession"):
        filing_index_url(320187, "123")


# --- Headers ----------------------------------------------------------------


async def test_user_agent_is_sent_on_every_request(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["User-Agent"])
        return httpx.Response(200, json={"ok": True})

    async with _client(handler, tmp_path) as client:
        await client.get_json("https://data.sec.gov/a.json")
        await client.get_json("https://data.sec.gov/b.json")

    assert seen == [USER_AGENT, USER_AGENT]


async def test_missing_contact_email_is_refused_before_the_request(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        message = "no request should be made"
        raise AssertionError(message)

    client = EdgarClient(
        user_agent="Trisheet ",
        cache=ResponseCache(tmp_path),
        limiter=TokenBucket(1_000),
        transport=httpx.MockTransport(handler),
    )
    async with client:
        with pytest.raises(EdgarNotConfiguredError, match="EDGAR_CONTACT_EMAIL"):
            await client.get_json("https://data.sec.gov/a.json")


# --- Retries ----------------------------------------------------------------


async def test_429_is_retried_and_then_succeeds(tmp_path: Path) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    async with _client(handler, tmp_path) as client:
        body = await client.get_json("https://data.sec.gov/a.json")

    assert attempts == 2
    assert body == {"ok": True}


async def test_5xx_is_retried_then_gives_up_after_three_attempts(
    tmp_path: Path,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, headers={"Retry-After": "0"})

    async with _client(handler, tmp_path) as client:
        with pytest.raises(EdgarUnavailableError, match="after 3 attempts"):
            await client.get_json("https://data.sec.gov/a.json")

    assert attempts == 3


async def test_404_is_not_retried(tmp_path: Path) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(404)

    async with _client(handler, tmp_path) as client:
        with pytest.raises(EdgarNotFoundError):
            await client.get_json("https://data.sec.gov/missing.json")

    assert attempts == 1


async def test_retry_after_header_is_honoured(tmp_path: Path) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0.4"})
        return httpx.Response(200, json={"ok": True})

    started = time.monotonic()
    async with _client(handler, tmp_path) as client:
        await client.get_json("https://data.sec.gov/a.json")
    elapsed = time.monotonic() - started

    # Backoff for attempt 0 would be 1.0s; Retry-After of 0.4s must win.
    assert 0.3 < elapsed < 0.9


# --- Caching ----------------------------------------------------------------


async def test_filing_responses_are_cached_permanently(tmp_path: Path) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"n": attempts})

    async with _client(handler, tmp_path) as client:
        first = await client.get_json("https://data.sec.gov/filing.json")
        second = await client.get_json("https://data.sec.gov/filing.json")

    assert attempts == 1
    assert first == second == {"n": 1}


async def test_expired_ttl_entries_are_refetched(tmp_path: Path) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"n": attempts})

    async with _client(handler, tmp_path) as client:
        await client.get_json("https://www.sec.gov/tickers.json", ttl=86_400)
        # A ttl of zero makes every stored entry immediately stale.
        await client.get_json("https://www.sec.gov/tickers.json", ttl=0)

    assert attempts == 2


async def test_malformed_json_is_a_typed_failure(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    async with _client(handler, tmp_path) as client:
        with pytest.raises(EdgarUnavailableError, match="malformed JSON"):
            await client.get_json("https://data.sec.gov/a.json")


# --- Rate limiting ----------------------------------------------------------


async def test_token_bucket_holds_the_ceiling_under_concurrency() -> None:
    """A burst larger than the bucket must be paced, not let through at once."""
    rate = 50.0
    requests = 125
    limiter = TokenBucket(rate)

    started = time.monotonic()
    await asyncio.gather(*(limiter.acquire() for _ in range(requests)))
    elapsed = time.monotonic() - started

    # The bucket starts full, so `rate` go immediately and the remaining
    # (125 - 50) are paced at 50/s: at least 1.5 seconds.
    expected = (requests - rate) / rate
    assert elapsed >= expected * 0.9
    # Sanity: pacing, not stalling.
    assert elapsed < expected * 3


async def test_token_bucket_is_shared_across_concurrent_clients(
    tmp_path: Path,
) -> None:
    """One ceiling for the whole process, not one per client instance."""
    rate = 40.0
    limiter = TokenBucket(rate)
    per_client = 40

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client_a = _client(handler, tmp_path / "a", limiter)
    client_b = _client(handler, tmp_path / "b", limiter)

    started = time.monotonic()
    async with client_a, client_b:
        await asyncio.gather(
            *(
                client.get_json(f"https://data.sec.gov/{index}.json")
                for client in (client_a, client_b)
                for index in range(per_client)
            )
        )
    elapsed = time.monotonic() - started

    # 80 requests through a 40/s bucket that starts with 40 tokens: ~1 second.
    assert elapsed >= ((2 * per_client) - rate) / rate * 0.9


async def test_cache_hits_do_not_consume_rate_limit_tokens(tmp_path: Path) -> None:
    """Cached reads bypass the limiter; only real requests are paced.

    Driven by a deliberately punitive 1 req/s bucket: were cache hits taking
    tokens, twenty of them would take nineteen seconds.
    """
    hits = 20
    limiter = TokenBucket(1.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    async with _client(handler, tmp_path, limiter) as client:
        await client.get_json("https://data.sec.gov/a.json")

        started = time.monotonic()
        for _ in range(hits):
            await client.get_json("https://data.sec.gov/a.json")
        elapsed = time.monotonic() - started

    assert elapsed < 2.0
