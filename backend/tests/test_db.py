"""Tests for services.db — credential handling.

`db.py` had no direct unit tests before this. Worth pinning down: the URL and
key are stripped before use, not only before the configured check. A
platform's own environment-variable entry UI is a surprisingly common source
of a stray leading or trailing space, and the sibling bug in `llm.py` shows
exactly what that costs when the two disagree — `is_configured` stripped
before checking truthiness, the value actually handed to the client did not,
and a whitespace-mangled credential looked configured and then failed anyway,
opaquely. `db.py`'s Supabase URL and service role key go through the same
environment-variable UI, so the same defence applies here.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.services import db


def _settings(*, url: str, key: str) -> Settings:
    return Settings(supabase_url=url, supabase_service_role_key=SecretStr(key))


def test_is_configured_true_despite_surrounding_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(url=" https://example.supabase.co ", key=" service-key ")
    monkeypatch.setattr(db, "get_settings", lambda: settings)

    assert db.is_configured() is True


def test_is_configured_false_when_only_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(url="   ", key="   ")
    monkeypatch.setattr(db, "get_settings", lambda: settings)

    assert db.is_configured() is False


def test_get_client_passes_the_stripped_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        url=" https://example.supabase.co ", key=" service-role-key "
    )
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    monkeypatch.setattr(db, "_client", None)

    captured: dict[str, Any] = {}

    def _fake_create_client(url: str, key: str) -> object:
        captured["url"] = url
        captured["key"] = key
        return object()

    import supabase

    monkeypatch.setattr(supabase, "create_client", _fake_create_client)

    db.get_client()

    assert captured["url"] == "https://example.supabase.co"
    assert captured["key"] == "service-role-key"


def test_get_client_raises_not_configured_when_credentials_are_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(url="", key="")
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    monkeypatch.setattr(db, "_client", None)

    with pytest.raises(db.DatabaseNotConfiguredError):
        db.get_client()
