"""m01 — ticker resolution.

Responsibility
    Turn a user-supplied ticker into a `Company`: CIK (zero-padded to 10),
    filer type (domestic vs foreign private issuer), SIC code and sector.

    Nothing downstream may assume a domestic filer. Filer type is decided here
    from the forms the company actually files, not from a hardcoded list.

Public interface
    resolve_ticker(ticker) -> Company
"""

from __future__ import annotations

from app.models import Company


async def resolve_ticker(ticker: str) -> Company:
    """Resolves a ticker to a company identity. Implemented in phase 1."""
    raise NotImplementedError
