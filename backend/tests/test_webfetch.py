"""Tests for webfetch — the SSRF guard matters more here than the fetch
itself. Every test below is a way this module could be tricked into
reaching somewhere it must not, and confirms it refuses.
"""

from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest

from app.services import webfetch
from app.services.webfetch import (
    WebfetchBlockedError,
    WebfetchError,
    fetch_page,
)

PUBLIC_ADDRESS = "93.184.216.34"  # example.com's real, stable public address


def _addrinfo(address: str) -> list[tuple[Any, ...]]:
    """One `socket.getaddrinfo` result naming a single address."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 0))]


def _stub_resolver(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    async def _fake_getaddrinfo(host: str, port: object) -> list[tuple[Any, ...]]:
        return _addrinfo(address)

    monkeypatch.setattr(
        webfetch.asyncio, "to_thread", lambda fn, *a: _fake_getaddrinfo(*a)
    )


# --- Scheme -------------------------------------------------------------


async def test_refuses_a_non_http_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_resolver(monkeypatch, PUBLIC_ADDRESS)

    with pytest.raises(WebfetchBlockedError):
        await fetch_page("file:///etc/passwd")


async def test_refuses_ftp(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_resolver(monkeypatch, PUBLIC_ADDRESS)

    with pytest.raises(WebfetchBlockedError):
        await fetch_page("ftp://example.com/x")


# --- Host resolution ------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # private
        "192.168.1.1",  # private
        "169.254.169.254",  # link-local — the cloud metadata endpoint
        "0.0.0.0",  # unspecified
        "224.0.0.1",  # multicast
        "::1",  # loopback, IPv6
        "fc00::1",  # unique local, IPv6 (private)
    ],
)
async def test_refuses_a_host_resolving_to_a_non_public_address(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    _stub_resolver(monkeypatch, address)

    with pytest.raises(WebfetchBlockedError):
        await fetch_page("https://looks-ordinary.example.com/")


async def test_refuses_a_host_that_does_not_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raise(host: str, port: object) -> None:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(
        webfetch.asyncio, "to_thread", lambda fn, *a: _raise(*a)
    )

    with pytest.raises(WebfetchBlockedError):
        await fetch_page("https://nowhere.invalid/")


async def test_allows_a_host_resolving_to_a_public_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_resolver(monkeypatch, PUBLIC_ADDRESS)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>Hello</body></html>")

    page = await fetch_page(
        "https://example.com/", transport=httpx.MockTransport(handler)
    )

    assert "Hello" in page.text
    assert page.url == "https://example.com/"


# --- Redirects --------------------------------------------------------------


async def test_follows_a_redirect_to_a_public_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_resolver(monkeypatch, PUBLIC_ADDRESS)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com/":
            return httpx.Response(
                302, headers={"location": "https://example.com/final"}
            )
        return httpx.Response(200, text="<html><body>Final page</body></html>")

    page = await fetch_page(
        "https://example.com/", transport=httpx.MockTransport(handler)
    )

    assert page.url == "https://example.com/final"
    assert "Final page" in page.text


async def test_refuses_a_redirect_to_a_private_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The core SSRF case: an ordinary-looking URL that redirects to an
    internal address. The guard must run again on the redirect target, not
    only on the URL the caller originally supplied."""
    calls = {"n": 0}

    async def _resolver(host: str, port: object) -> list[tuple[Any, ...]]:
        calls["n"] += 1
        # First hop resolves safely; the redirect target does not.
        return _addrinfo(PUBLIC_ADDRESS if calls["n"] == 1 else "169.254.169.254")

    monkeypatch.setattr(
        webfetch.asyncio, "to_thread", lambda fn, *a: _resolver(*a)
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data"}
        )

    with pytest.raises(WebfetchBlockedError):
        await fetch_page(
            "https://example.com/", transport=httpx.MockTransport(handler)
        )


async def test_refuses_more_than_the_redirect_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_resolver(monkeypatch, PUBLIC_ADDRESS)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/next"})

    with pytest.raises(WebfetchBlockedError):
        await fetch_page(
            "https://example.com/", transport=httpx.MockTransport(handler)
        )


# --- Response size ------------------------------------------------------


async def test_refuses_a_response_over_the_size_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_resolver(monkeypatch, PUBLIC_ADDRESS)
    monkeypatch.setattr(webfetch, "WEBFETCH_MAX_RESPONSE_BYTES", 10)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="x" * 1000)

    with pytest.raises(WebfetchBlockedError):
        await fetch_page(
            "https://example.com/", transport=httpx.MockTransport(handler)
        )


# --- Ordinary failures, not blocked, just failed -----------------------------


async def test_a_4xx_status_is_a_plain_error_not_a_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_resolver(monkeypatch, PUBLIC_ADDRESS)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with pytest.raises(WebfetchError) as excinfo:
        await fetch_page(
            "https://example.com/", transport=httpx.MockTransport(handler)
        )
    assert not isinstance(excinfo.value, WebfetchBlockedError)
