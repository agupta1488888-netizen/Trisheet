"""m08 — peer selection.

Responsibility
    Choose comparable companies via a ladder: same SIC code first, widening to
    the SIC group, then the sector, stopping as soon as enough peers are found.

    Selection is derived from the filer's own SIC code — no company-specific
    peer lists are hardcoded anywhere.

Public interface
    select_peers(company) -> list[Company]
"""

from __future__ import annotations

from app.models import Company


async def select_peers(company: Company) -> list[Company]:
    """Selects comparable companies. Implemented in phase 1."""
    raise NotImplementedError
