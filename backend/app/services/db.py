"""Supabase client.

The only module that opens a database connection. Uses the service role key,
which bypasses row level security — which is exactly why nothing reachable from
the browser may import this module.

Public interface
    get_client() -> Client
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supabase import Client


def get_client() -> Client:
    """Returns the shared Supabase client. Implemented in phase 1."""
    raise NotImplementedError
