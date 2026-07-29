"""m06 — fact persistence and provenance enforcement.

Responsibility
    The write gate. Every fact in the system passes through here, and this is
    where provenance is enforced in code:

    - A fact missing tier, source_type, source_url, accession_no or filed_date
      is discarded, with the rejection logged. It is never stored blank.
    - A Tier 3 or Tier 4 fact offered for a section 3 metric is hard-blocked
      and the rejection is logged.

Public interface
    store_facts(report_id, facts) -> StoreResult
    load_facts(report_id) -> list[Fact]
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.models import Fact


class StoreResult(BaseModel):
    """Outcome of a write. Rejections are reported, never silently swallowed."""

    model_config = ConfigDict(frozen=True)

    stored: int
    rejected: int
    rejection_reasons: list[str]


async def store_facts(report_id: str, facts: list[Fact]) -> StoreResult:
    """Persists facts that carry full provenance. Implemented in phase 1."""
    raise NotImplementedError


async def load_facts(report_id: str) -> list[Fact]:
    """Loads stored facts for a report. Implemented in phase 1."""
    raise NotImplementedError
