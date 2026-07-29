"""m02 — filing discovery.

Responsibility
    Build the manifest of filings a report will be sourced from: the latest
    annual report (10-K, 20-F or 40-F depending on filer type), recent
    quarterlies, current reports and the proxy.

    Amendments are surfaced alongside originals; m03 decides which wins.

Public interface
    build_manifest(company) -> list[Filing]
"""

from __future__ import annotations

from app.models import Company, Filing


async def build_manifest(company: Company) -> list[Filing]:
    """Returns the filings this report may draw on. Implemented in phase 1."""
    raise NotImplementedError
