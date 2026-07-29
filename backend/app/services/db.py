"""Supabase client.

The only module that opens a database connection. Uses the service role key,
which is why it must never be reachable from the browser.

Public interface
    get_client() -> Client
"""

from __future__ import annotations

from typing import Any


def get_client() -> Any:
    """Returns the shared Supabase client. Implemented in phase 1."""
    raise NotImplementedError
