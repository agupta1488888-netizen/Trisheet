"""m09 — recent developments.

Responsibility
    Build a dated timeline from current reports (8-K, or 6-K for foreign
    filers), each entry sourced to the filing it came from.

Public interface
    build_timeline(company, manifest) -> list[Fact]
"""

from __future__ import annotations

from app.models import Company, Fact, Filing


async def build_timeline(
    company: Company, manifest: list[Filing]
) -> list[Fact]:
    """Builds the developments timeline. Implemented in phase 1."""
    raise NotImplementedError
