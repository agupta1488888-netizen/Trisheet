"""Supabase client.

The only module that opens a database connection. Uses the service role key,
which bypasses row level security — which is exactly why nothing reachable from
the browser may import this module.

Public interface
    get_client() -> Client
    is_configured() -> bool
    reset_client() -> None
    DatabaseNotConfiguredError / DatabaseUnavailableError
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.config import get_settings

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    """Base class for every persistence failure. Callers catch this."""


class DatabaseNotConfiguredError(DatabaseError):
    """No Supabase URL or service role key is set, so there is nowhere to write."""


class DatabaseUnavailableError(DatabaseError):
    """Supabase is configured but could not be reached or refused the request."""


_client: Client | None = None


def _credentials() -> tuple[str, str]:
    """The URL and key, with surrounding whitespace removed.

    A platform's own environment-variable entry UI is a surprisingly common
    source of a stray leading or trailing space — Anthropic's key carried one
    on this deployment (see llm._api_key for the fuller account) and every
    call failed opaquely because the value used to check "is this configured"
    was stripped while the value actually sent was not, letting a
    whitespace-mangled credential look configured and then fail anyway. Both
    credentials here are exposed to the same environment-variable UI, so both
    are stripped the same way, from the one place that reads them.
    """
    settings = get_settings()
    return (
        settings.supabase_url.strip(),
        settings.supabase_service_role_key.get_secret_value().strip(),
    )


def is_configured() -> bool:
    """True when both Supabase settings are present.

    Callers use this to degrade rather than crash: persistence is a sink, not
    a source, and only EDGAR is a hard dependency.
    """
    url, key = _credentials()
    return bool(url and key)


def get_client() -> Client:
    """Returns the shared Supabase client, building it on first use.

    Raises:
        DatabaseNotConfiguredError: neither URL nor key was supplied.
        DatabaseUnavailableError: the client could not be constructed.
    """
    global _client  # noqa: PLW0603 — one process-wide pooled client by design
    if _client is not None:
        return _client

    if not is_configured():
        message = (
            "Supabase is not configured. Set SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY before storing facts."
        )
        raise DatabaseNotConfiguredError(message)

    url, key = _credentials()
    try:
        from supabase import create_client

        _client = create_client(url, key)
    except ImportError as cause:
        message = "The supabase package is not installed."
        raise DatabaseUnavailableError(message) from cause
    except Exception as cause:  # noqa: BLE001 — surfaced as a typed failure
        # The URL and key are never logged; only that construction failed.
        logger.error("Could not build the Supabase client", exc_info=cause)
        message = f"Could not connect to Supabase: {cause}"
        raise DatabaseUnavailableError(message) from cause

    return _client


def reset_client() -> None:
    """Drops the cached client. Used by tests and on configuration reload."""
    global _client  # noqa: PLW0603 — mirrors get_client
    _client = None
