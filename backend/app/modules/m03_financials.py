"""m03 — XBRL extraction.

Responsibility
    Pull reported figures out of the XBRL company facts API and emit `Fact`
    objects carrying full provenance and the tag that actually resolved.

Rules
    - Never hardcode a single tag. Each metric has an ordered fallback list.
    - Try us-gaap first, fall back to ifrs-full for foreign filers.
    - Deduplicate on (start, end, form); keep the latest filed date.
    - Prefer amendments over originals for the same period.

Public interface
    extract_financials(company, manifest) -> list[Fact]
"""

from __future__ import annotations

from app.models import Company, Fact, Filing

#: Ordered fallbacks per metric. First tag that resolves wins, and the winner
#: is recorded on the Fact. Extend this map; never inline a tag at a call site.
TAG_FALLBACKS: dict[str, tuple[str, ...]] = {
    "income.revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ),
}


async def extract_financials(
    company: Company, manifest: list[Filing]
) -> list[Fact]:
    """Extracts reported figures as facts. Implemented in phase 1."""
    raise NotImplementedError
