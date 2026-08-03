"""webfetch — fetches one user-supplied web page for the chat assistant.

Responsibility
    The one module that opens an outbound connection to a URL a user typed
    in, rather than to a fixed, known host. Every other network client in
    this codebase talks to one place — sec.gov/data.sec.gov (edgar.py), the
    configured market provider (m05_market.py), Anthropic (llm.py), Supabase
    (db.py) — and none of them need to defend against where they are
    pointed, because they are never pointed anywhere. This module is pointed
    somewhere on every call, so it carries its own guard.

Guards, all enforced here, never left to a caller
    - Scheme: http/https only.
    - Every address the hostname resolves to is checked against private,
      loopback, link-local, multicast, reserved and unspecified ranges, and
      refused if any of them match. Resolving rather than pattern-matching
      the hostname is the point: a name can be chosen so it looks ordinary
      while its DNS answer is a private address.
    - A redirect is followed one hop at a time, with the same scheme and host
      checks applied to each hop's target — httpx's automatic redirect
      handling does not re-run a caller's own guard on where a redirect
      actually leads, so redirects are followed manually here instead.
    - The response body is bounded while it streams; a response that exceeds
      the cap is abandoned, never truncated and used anyway.
    - A short, fixed timeout — this runs inside a chat turn, not a background
      job.

What this is not
    Not a fact source. What comes back is raw page text; nothing here
    produces a `Fact` or a `ChatClaim` — the caller attaches its own tier,
    source type and citation, because only the caller knows this was reached
    as a fallback and must be labelled as self-reported, not filed.

Public interface
    fetch_page(url) -> FetchedPage
    WebfetchError / WebfetchBlockedError
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from dataclasses import dataclass

import httpx

from app.config import (
    WEBFETCH_MAX_REDIRECTS,
    WEBFETCH_MAX_RESPONSE_BYTES,
    WEBFETCH_TIMEOUT_SECONDS,
)
from app.services.document import html_to_text

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


class WebfetchError(Exception):
    """A page could not be fetched. Callers degrade to a not-found claim."""


class WebfetchBlockedError(WebfetchError):
    """The URL, or a redirect target, is not a safe, public http(s) address."""


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """A fetched page, HTML already stripped to plain text."""

    #: The URL actually reached, after any redirects — what a citation should
    #: point at, not necessarily what the caller originally supplied.
    url: str
    text: str


def _check_scheme(url: httpx.URL) -> None:
    if url.scheme not in _ALLOWED_SCHEMES:
        message = f"Refused: scheme {url.scheme!r} is not http or https."
        raise WebfetchBlockedError(message)


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


async def _check_host_is_public(host: str) -> None:
    """Resolves the host and refuses unless every resolved address is public.

    Runs the (blocking) resolver off the event loop rather than skipping the
    check — a name that resolves to even one private address is refused,
    since a caller reading the page cannot know which address answered it.
    """
    if not host:
        message = "Refused: the URL names no host."
        raise WebfetchBlockedError(message)

    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except socket.gaierror as cause:
        message = f"Refused: could not resolve {host!r}."
        raise WebfetchBlockedError(message) from cause

    for info in infos:
        raw_address = str(info[4][0]).split("%")[0]
        address = ipaddress.ip_address(raw_address)
        if not _is_public_address(address):
            message = (
                f"Refused: {host!r} resolves to {address}, which is not a "
                "public address."
            )
            raise WebfetchBlockedError(message)


async def _read_bounded_body(response: httpx.Response) -> str:
    total = 0
    chunks: list[bytes] = []
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > WEBFETCH_MAX_RESPONSE_BYTES:
            message = (
                f"Refused: response exceeded {WEBFETCH_MAX_RESPONSE_BYTES} bytes."
            )
            raise WebfetchBlockedError(message)
        chunks.append(chunk)
    return b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")


async def _fetch_one_hop(
    client: httpx.AsyncClient, url: str
) -> tuple[str | None, str | None]:
    """One request. Returns `(body, redirect_location)`, exactly one set."""
    try:
        async with client.stream("GET", url) as response:
            if response.status_code in _REDIRECT_STATUS_CODES:
                return None, response.headers.get("location")
            if response.status_code >= 400:
                message = f"The page answered with status {response.status_code}."
                raise WebfetchError(message)
            return await _read_bounded_body(response), None
    except httpx.TimeoutException as cause:
        message = "The page did not respond in time."
        raise WebfetchError(message) from cause
    except httpx.HTTPError as cause:
        if isinstance(cause, WebfetchError):
            raise
        message = f"The page could not be reached: {cause}"
        raise WebfetchError(message) from cause


async def fetch_page(
    url: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> FetchedPage:
    """Fetches one page, refusing anything that is not a safe, public URL.

    Args:
        url: The page to fetch.
        transport: Overridable for tests, the same way `EdgarClient` accepts
            one — production code never passes this, so a request always
            travels a real connection there.

    Raises:
        WebfetchBlockedError: the URL, or a redirect target, is not a safe,
            public http(s) address.
        WebfetchError: the request failed for any other reason — timeout,
            connection refused, a non-2xx status, a body too large.
    """
    current = httpx.URL(url)
    _check_scheme(current)
    await _check_host_is_public(current.host)

    async with httpx.AsyncClient(
        timeout=WEBFETCH_TIMEOUT_SECONDS,
        follow_redirects=False,
        transport=transport,
    ) as client:
        for _hop in range(WEBFETCH_MAX_REDIRECTS + 1):
            body, location = await _fetch_one_hop(client, str(current))

            if body is not None:
                logger.info(
                    "Page fetched", extra={"url": str(current), "bytes": len(body)}
                )
                return FetchedPage(url=str(current), text=html_to_text(body))

            if not location:
                message = "Refused: redirect with no Location header."
                raise WebfetchBlockedError(message)

            current = current.join(location)
            _check_scheme(current)
            await _check_host_is_public(current.host)

    message = f"Refused: more than {WEBFETCH_MAX_REDIRECTS} redirects."
    raise WebfetchBlockedError(message)
