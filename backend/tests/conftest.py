"""Shared fixtures and builders for the backend test suite.

Tests never reach the network. `stub_edgar_client` replaces the shared EDGAR
client with one backed by an in-memory URL map, so extraction is exercised
against the exact shapes EDGAR returns without depending on EDGAR being up.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest

from app.models import (
    Company,
    ExtractionMethod,
    Fact,
    FilerType,
    Filing,
    SourceTier,
    SourceType,
)
from app.services import edgar

APPLE_CIK = "0000320193"
APPLE_ACCESSION = "0000320193-24-000123"


class StubEdgarClient:
    """An EdgarClient stand-in serving canned bodies from a URL map.

    Records every URL requested so a test can assert that company facts are
    fetched once per company rather than once per metric.
    """

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self._bodies = bodies
        self.requested: list[str] = []

    async def get_bytes(self, url: str, ttl: float | None = None) -> bytes:
        self.requested.append(url)
        try:
            return self._bodies[url]
        except KeyError as cause:
            message = f"No stub body registered for {url}"
            raise edgar.EdgarNotFoundError(message) from cause

    async def get_text(self, url: str, ttl: float | None = None) -> str:
        return (await self.get_bytes(url, ttl)).decode("utf-8")

    async def get_json(self, url: str, ttl: float | None = None) -> dict[str, Any]:
        parsed: Any = json.loads(await self.get_bytes(url, ttl))
        if not isinstance(parsed, dict):
            message = f"Expected a JSON object from {url}"
            raise edgar.EdgarUnavailableError(message)
        return parsed

    def register(self, url: str, body: bytes | str | dict[str, Any]) -> None:
        """Registers a canned body, accepting the three shapes tests hold."""
        if isinstance(body, dict):
            self._bodies[url] = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            self._bodies[url] = body.encode("utf-8")
        else:
            self._bodies[url] = body


@pytest.fixture
def stub_edgar(monkeypatch: pytest.MonkeyPatch) -> StubEdgarClient:
    """Installs a stub EDGAR client. Register bodies on the client it returns."""
    client = StubEdgarClient({})
    monkeypatch.setattr(edgar, "get_client", lambda: client)
    return client


def make_company(
    *,
    cik: str = APPLE_CIK,
    ticker: str = "AAPL",
    filer_type: FilerType = FilerType.DOMESTIC,
) -> Company:
    return Company(
        cik=cik,
        ticker=ticker,
        name="Test Filer, Inc.",
        filer_type=filer_type,
        sic_code="3571",
        sector="Electronic computers",
        fiscal_year_end="0928",
        reporting_currency="USD",
    )


def make_filing(
    *,
    accession_no: str = APPLE_ACCESSION,
    form: str = "10-K",
    filed_date: dt.date = dt.date(2024, 11, 1),
    period_of_report: dt.date | None = dt.date(2024, 9, 28),
    cik: str = APPLE_CIK,
) -> Filing:
    return Filing(
        accession_no=accession_no,
        cik=cik,
        form=form,
        filed_date=filed_date,
        period_of_report=period_of_report,
        primary_doc_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019324000123/aapl-20240928.htm"
        ),
    )


def make_fact(**overrides: Any) -> Fact:
    """A valid, fully sourced fact. Override one field to make it invalid."""
    fields: dict[str, Any] = {
        "metric": "income.revenue",
        "label": "Revenue",
        "value": 391_035_000_000.0,
        "display_value": "391,035,000,000",
        "unit": "USD",
        "period_start": dt.date(2023, 10, 1),
        "period_end": dt.date(2024, 9, 28),
        "fiscal_year": 2024,
        "tier": SourceTier.FILING,
        "source_type": SourceType.SEC_XBRL,
        "source_url": "https://www.sec.gov/Archives/edgar/data/320193/x.htm",
        "accession_no": APPLE_ACCESSION,
        "filed_date": dt.date(2024, 11, 1),
        "extraction_method": ExtractionMethod.XBRL_COMPANY_FACTS,
        "confidence": 1.0,
        "resolved_tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
        "taxonomy": "us-gaap",
    }
    fields.update(overrides)
    return Fact(**fields)


def company_facts(
    *,
    taxonomy: str = "us-gaap",
    tag: str = "RevenueFromContractWithCustomerExcludingAssessedTax",
    unit: str = "USD",
    rows: list[dict[str, Any]] | None = None,
    extra: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Builds a company-facts document around one concept.

    `extra` adds further concepts in the same taxonomy, keyed by tag, so a test
    can put a second tag on the fallback ladder without restating the shape.
    """
    concepts: dict[str, Any] = {
        tag: {"units": {unit: rows if rows is not None else []}}
    }
    for extra_tag, concept in (extra or {}).items():
        concepts[extra_tag] = concept
    return {"cik": 320193, "entityName": "Test Filer", "facts": {taxonomy: concepts}}


def annual_row(
    *,
    start: str,
    end: str,
    val: float,
    accn: str,
    form: str = "10-K",
    filed: str,
    fy: int | None = None,
    fp: str = "FY",
) -> dict[str, Any]:
    """One company-facts row, in EDGAR's exact shape."""
    row: dict[str, Any] = {
        "start": start,
        "end": end,
        "val": val,
        "accn": accn,
        "form": form,
        "filed": filed,
        "fp": fp,
    }
    if fy is not None:
        row["fy"] = fy
    return row
