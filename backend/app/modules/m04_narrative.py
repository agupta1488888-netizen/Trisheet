"""m04 — narrative sections.

Responsibility
    Extract the text sections of the annual report: business description, risk
    factors, MD&A. Emits facts whose value is textual rather than numeric.

Degradation
    Narrative parsing is not a hard dependency. If it fails, the report is
    still generated from XBRL alone.

Public interface
    extract_narrative(company, manifest) -> list[Fact]
"""

from __future__ import annotations

from app.models import Company, Fact, Filing


async def extract_narrative(
    company: Company, manifest: list[Filing]
) -> list[Fact]:
    """Extracts narrative sections. Implemented in phase 1."""
    raise NotImplementedError
