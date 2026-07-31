"""Tests for m03 — XBRL extraction.

The four behaviours that decide whether a figure is right:
tag fallback, deduplication, restatement and amendment precedence, and the
refusal to invent anything for a metric that is not reported.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.config import MAX_ANNUAL_PERIODS
from app.models import (
    Company,
    ExtractionMethod,
    Fact,
    FilerType,
    Filing,
    SourceTier,
    Taxonomy,
)
from app.modules import m03_financials as m03
from app.services import edgar
from tests.conftest import (
    APPLE_CIK,
    StubEdgarClient,
    annual_row,
    company_facts,
    make_company,
    make_filing,
)

REVENUE = "income.revenue"
PRIMARY_TAG = "RevenueFromContractWithCustomerExcludingAssessedTax"


def _facts_url(cik: str = APPLE_CIK) -> str:
    return edgar.company_facts_url(cik)


async def _extract(
    stub_edgar: StubEdgarClient,
    payload: dict[str, Any],
    *,
    company: Company | None = None,
    manifest: list[Filing] | None = None,
) -> list[Fact]:
    stub_edgar.register(_facts_url(), payload)
    return await m03.extract_financials(
        company or make_company(), manifest if manifest is not None else []
    )


def _revenue(facts: list[Fact]) -> list[Fact]:
    return [fact for fact in facts if fact.metric == REVENUE]


# --- Tag fallback -----------------------------------------------------------


async def test_first_tag_on_the_ladder_wins(stub_edgar: StubEdgarClient) -> None:
    """The preferred tag resolves, and is the one recorded on the fact."""
    payload = company_facts(
        rows=[
            annual_row(
                start="2023-10-01",
                end="2024-09-28",
                val=391_035_000_000,
                accn="0000320193-24-000123",
                filed="2024-11-01",
                fy=2024,
            )
        ],
        extra={
            "Revenues": {
                "units": {
                    "USD": [
                        annual_row(
                            start="2023-10-01",
                            end="2024-09-28",
                            val=1,
                            accn="0000320193-24-000123",
                            filed="2024-11-01",
                        )
                    ]
                }
            }
        },
    )

    facts = _revenue(await _extract(stub_edgar, payload))

    assert len(facts) == 1
    assert facts[0].value == 391_035_000_000
    assert facts[0].resolved_tag == PRIMARY_TAG
    assert facts[0].confidence == 1.0


async def test_falls_through_to_the_next_tag_when_the_first_is_absent(
    stub_edgar: StubEdgarClient,
) -> None:
    """A filer that tags revenue as Revenues still resolves, at lower confidence."""
    payload = company_facts(
        tag="Revenues",
        rows=[
            annual_row(
                start="2023-01-01",
                end="2023-12-31",
                val=500_000,
                accn="0000320193-24-000001",
                filed="2024-02-01",
                fy=2023,
            )
        ],
    )

    facts = _revenue(await _extract(stub_edgar, payload))

    assert len(facts) == 1
    assert facts[0].resolved_tag == "Revenues"
    # Second rung of the ladder: exactly one fallback step has been charged.
    assert facts[0].confidence == pytest.approx(0.95)


async def test_a_present_but_empty_tag_does_not_end_the_search(
    stub_edgar: StubEdgarClient,
) -> None:
    """A tag with no usable rows falls through instead of resolving empty."""
    payload = company_facts(
        rows=[],
        extra={
            "Revenues": {
                "units": {
                    "USD": [
                        annual_row(
                            start="2023-01-01",
                            end="2023-12-31",
                            val=42,
                            accn="0000320193-24-000001",
                            filed="2024-02-01",
                        )
                    ]
                }
            }
        },
    )

    facts = _revenue(await _extract(stub_edgar, payload))

    assert len(facts) == 1
    assert facts[0].resolved_tag == "Revenues"


async def test_a_stale_preferred_tag_loses_to_one_covering_today(
    stub_edgar: StubEdgarClient,
) -> None:
    """A filer that migrated tags must not be reported from the abandoned one.

    NIKE tagged cost of sales as CostOfRevenue until 2011 and as
    CostOfGoodsAndServicesSold afterwards. Both stay populated in company facts
    forever, so first-on-the-ladder-wins would print a 2011 figure.
    """
    payload = company_facts(
        tag="CostOfRevenue",
        rows=[
            annual_row(
                start="2010-06-01",
                end="2011-05-31",
                val=11_354_000_000,
                accn="0000320187-11-000001",
                filed="2011-07-20",
                fy=2011,
            )
        ],
        extra={
            "CostOfGoodsAndServicesSold": {
                "units": {
                    "USD": [
                        annual_row(
                            start="2023-06-01",
                            end="2024-05-31",
                            val=28_925_000_000,
                            accn="0000320187-24-000044",
                            filed="2024-07-25",
                            fy=2024,
                        )
                    ]
                }
            }
        },
    )

    facts = [
        fact
        for fact in await _extract(stub_edgar, payload)
        if fact.metric == "income.cost_of_revenue"
    ]

    assert len(facts) == 1
    assert facts[0].resolved_tag == "CostOfGoodsAndServicesSold"
    assert facts[0].value == 28_925_000_000
    assert facts[0].period_end == dt.date(2024, 5, 31)


async def test_ladder_order_still_decides_between_equally_current_tags(
    stub_edgar: StubEdgarClient,
) -> None:
    """Preferring the current tag must not turn the ladder into a free-for-all."""
    row = {
        "start": "2023-10-01",
        "end": "2024-09-28",
        "accn": "0000320193-24-000123",
        "form": "10-K",
        "filed": "2024-11-01",
        "fy": 2024,
        "fp": "FY",
    }
    payload = company_facts(
        rows=[{**row, "val": 391_035_000_000}],
        extra={"Revenues": {"units": {"USD": [{**row, "val": 1}]}}},
    )

    facts = _revenue(await _extract(stub_edgar, payload))

    assert facts[0].resolved_tag == PRIMARY_TAG
    assert facts[0].value == 391_035_000_000


async def test_an_instant_metric_ignores_quarter_end_balance_dates(
    stub_edgar: StubEdgarClient,
) -> None:
    """A 10-Q balance sheet date is not a year end and must not sit in the table.

    A duration is filtered by length; an instant has no length, so without this
    the most recent quarter would head the annual balance sheet column.
    """
    payload = company_facts(
        tag="Assets",
        rows=[
            # Establishes 2024-09-28 as a fiscal year end, from the filer's own
            # annual filing.
            annual_row(
                start="2023-10-01",
                end="2024-09-28",
                val=391_035_000_000,
                accn="0000320193-24-000123",
                filed="2024-11-01",
                fy=2024,
            ),
            {
                "end": "2024-09-28",
                "val": 364_980_000_000,
                "accn": "0000320193-24-000123",
                "form": "10-K",
                "filed": "2024-11-01",
                "fy": 2024,
                "fp": "FY",
            },
            {
                "end": "2024-12-28",
                "val": 344_085_000_000,
                "accn": "0000320193-25-000008",
                "form": "10-Q",
                "filed": "2025-01-31",
                "fy": 2025,
                "fp": "Q1",
            },
        ],
    )

    facts = [
        fact
        for fact in await _extract(stub_edgar, payload)
        if fact.metric == "balance.total_assets"
    ]

    assert [fact.period_end for fact in facts] == [dt.date(2024, 9, 28)]
    assert facts[0].value == 364_980_000_000


async def test_instant_metrics_survive_an_unknown_fiscal_calendar(
    stub_edgar: StubEdgarClient,
) -> None:
    """Not knowing the filer's year ends is a reason to show dates, not drop them."""
    payload = company_facts(
        tag="Assets",
        rows=[
            {
                "end": "2024-09-28",
                "val": 364_980_000_000,
                "accn": "0000320193-24-000123",
                "form": "10-K",
                "filed": "2024-11-01",
                "fp": "Q4",
            }
        ],
    )

    facts = [
        fact
        for fact in await _extract(stub_edgar, payload)
        if fact.metric == "balance.total_assets"
    ]

    assert len(facts) == 1
    assert facts[0].value == 364_980_000_000


async def test_foreign_filer_resolves_against_ifrs_first(
    stub_edgar: StubEdgarClient,
) -> None:
    """A 20-F filer's IFRS tags are tried before us-gaap, and at full confidence."""
    payload = company_facts(
        taxonomy="ifrs-full",
        tag="Revenue",
        unit="EUR",
        rows=[
            annual_row(
                start="2023-01-01",
                end="2023-12-31",
                val=80_000_000,
                accn="0000320193-24-000001",
                form="20-F",
                filed="2024-03-01",
                fy=2023,
            )
        ],
    )
    company = make_company(filer_type=FilerType.FOREIGN)

    facts = _revenue(await _extract(stub_edgar, payload, company=company))

    assert len(facts) == 1
    assert facts[0].taxonomy is Taxonomy.IFRS_FULL
    assert facts[0].resolved_tag == "Revenue"
    assert facts[0].unit == "EUR"
    assert facts[0].confidence == 1.0


async def test_domestic_filer_falls_back_to_ifrs_at_a_confidence_cost(
    stub_edgar: StubEdgarClient,
) -> None:
    """Falling to the non-preferred taxonomy is charged, but still resolves."""
    payload = company_facts(
        taxonomy="ifrs-full",
        tag="Revenue",
        rows=[
            annual_row(
                start="2023-01-01",
                end="2023-12-31",
                val=1_000,
                accn="0000320193-24-000001",
                filed="2024-02-01",
            )
        ],
    )

    facts = _revenue(await _extract(stub_edgar, payload))

    assert len(facts) == 1
    assert facts[0].taxonomy is Taxonomy.IFRS_FULL
    assert facts[0].confidence == pytest.approx(0.95)


async def test_a_metric_is_read_in_one_currency_only(
    stub_edgar: StubEdgarClient,
) -> None:
    """A filer reporting in two currencies must not produce a mixed column.

    SAP files revenue under both EUR and USD. Reading both would put two
    currencies in one column and let deduplication choose between them by
    accident of ordering.
    """
    row = {
        "start": "2025-01-01",
        "end": "2025-12-31",
        "accn": "0001104659-26-020058",
        "form": "20-F",
        "filed": "2026-02-26",
        "fy": 2025,
        "fp": "FY",
    }
    payload = {
        "facts": {
            "ifrs-full": {
                "Revenue": {
                    "units": {
                        "EUR": [{**row, "val": 36_800_000_000}],
                        "USD": [{**row, "val": 40_100_000_000}],
                    }
                }
            }
        }
    }
    # Currency unknown, so the bucket must be chosen by rule, not by luck.
    company = make_company(filer_type=FilerType.FOREIGN).model_copy(
        update={"reporting_currency": None}
    )

    facts = _revenue(await _extract(stub_edgar, payload, company=company))

    assert len(facts) == 1
    assert {fact.unit for fact in facts} == {"EUR"}


async def test_the_filers_reporting_currency_decides_the_unit(
    stub_edgar: StubEdgarClient,
) -> None:
    """When the filer's currency is known it is not a matter of inference."""
    row = {
        "start": "2025-01-01",
        "end": "2025-12-31",
        "accn": "0001104659-26-020058",
        "form": "20-F",
        "filed": "2026-02-26",
        "fy": 2025,
        "fp": "FY",
    }
    payload = {
        "facts": {
            "ifrs-full": {
                "Revenue": {
                    "units": {
                        "EUR": [{**row, "val": 36_800_000_000}],
                        "USD": [{**row, "val": 40_100_000_000}],
                    }
                }
            }
        }
    }
    company = make_company(filer_type=FilerType.FOREIGN).model_copy(
        update={"reporting_currency": "USD"}
    )

    facts = _revenue(await _extract(stub_edgar, payload, company=company))

    assert [fact.unit for fact in facts] == ["USD"]
    assert facts[0].value == 40_100_000_000


async def test_company_facts_is_fetched_once_for_every_metric(
    stub_edgar: StubEdgarClient,
) -> None:
    """One request per company, not one per metric."""
    payload = company_facts(
        rows=[
            annual_row(
                start="2023-10-01",
                end="2024-09-28",
                val=391_035_000_000,
                accn="0000320193-24-000123",
                filed="2024-11-01",
                fy=2024,
            )
        ]
    )

    await _extract(stub_edgar, payload)

    assert stub_edgar.requested == [_facts_url()]


# --- Deduplication ----------------------------------------------------------


async def test_the_same_period_and_form_keeps_the_latest_filed(
    stub_edgar: StubEdgarClient,
) -> None:
    """A period re-reported as a comparative resolves to the newest filing."""
    payload = company_facts(
        rows=[
            annual_row(
                start="2023-10-01",
                end="2024-09-28",
                val=391_035_000_000,
                accn="0000320193-24-000123",
                filed="2024-11-01",
                fy=2024,
            ),
            annual_row(
                start="2023-10-01",
                end="2024-09-28",
                val=391_035_000_000,
                accn="0000320193-25-000079",
                filed="2025-10-31",
                fy=2025,
            ),
        ]
    )

    facts = _revenue(await _extract(stub_edgar, payload))

    assert len(facts) == 1
    assert facts[0].accession_no == "0000320193-25-000079"
    assert facts[0].filed_date == dt.date(2025, 10, 31)


async def test_a_restated_figure_supersedes_the_original(
    stub_edgar: StubEdgarClient,
) -> None:
    """When a later filing reports a different number, the later one wins."""
    payload = company_facts(
        rows=[
            annual_row(
                start="2022-01-01",
                end="2022-12-31",
                val=100_000,
                accn="0000320193-23-000001",
                filed="2023-02-01",
                fy=2022,
            ),
            annual_row(
                start="2022-01-01",
                end="2022-12-31",
                val=97_500,
                accn="0000320193-24-000001",
                filed="2024-02-01",
                fy=2023,
            ),
        ]
    )

    facts = _revenue(await _extract(stub_edgar, payload))

    assert len(facts) == 1
    assert facts[0].value == 97_500
    assert facts[0].accession_no == "0000320193-24-000001"


async def test_quarterly_periods_are_not_mistaken_for_annual(
    stub_edgar: StubEdgarClient,
) -> None:
    """A 10-Q's three-month period is not a year and is not extracted as one."""
    payload = company_facts(
        rows=[
            annual_row(
                start="2024-01-01",
                end="2024-03-31",
                val=25_000,
                accn="0000320193-24-000050",
                form="10-Q",
                filed="2024-05-01",
                fp="Q1",
            ),
            annual_row(
                start="2024-01-01",
                end="2024-12-31",
                val=100_000,
                accn="0000320193-25-000001",
                filed="2025-02-01",
                fy=2024,
            ),
        ]
    )

    facts = _revenue(await _extract(stub_edgar, payload))

    assert len(facts) == 1
    assert facts[0].value == 100_000
    assert facts[0].period_start == dt.date(2024, 1, 1)


async def test_only_the_most_recent_periods_are_kept(
    stub_edgar: StubEdgarClient,
) -> None:
    """The table is bounded, and bounded from the newest end."""
    rows = [
        annual_row(
            start=f"{year}-01-01",
            end=f"{year}-12-31",
            val=year,
            accn=f"0000320193-{year % 100:02d}-000001",
            filed=f"{year + 1}-02-01",
            fy=year,
        )
        for year in range(2012, 2025)
    ]

    facts = _revenue(await _extract(stub_edgar, company_facts(rows=rows)))

    assert len(facts) == MAX_ANNUAL_PERIODS
    assert facts[0].period_end == dt.date(2024, 12, 31)
    assert [fact.period_end.year for fact in facts] == [
        2024,
        2023,
        2022,
        2021,
        2020,
    ]


# --- Amendment precedence ---------------------------------------------------


async def test_an_amendment_beats_the_original_it_amends(
    stub_edgar: StubEdgarClient,
) -> None:
    """10-K/A wins over 10-K for the same period, because that is what it is for."""
    payload = company_facts(
        rows=[
            annual_row(
                start="2023-01-01",
                end="2023-12-31",
                val=100_000,
                accn="0000320193-24-000001",
                form="10-K",
                filed="2024-02-01",
                fy=2023,
            ),
            annual_row(
                start="2023-01-01",
                end="2023-12-31",
                val=98_000,
                accn="0000320193-24-000009",
                form="10-K/A",
                filed="2024-06-01",
                fy=2023,
            ),
        ]
    )

    facts = _revenue(await _extract(stub_edgar, payload))

    assert len(facts) == 1
    assert facts[0].value == 98_000
    assert facts[0].accession_no == "0000320193-24-000009"


async def test_an_amendment_wins_even_when_filed_before_a_later_original(
    stub_edgar: StubEdgarClient,
) -> None:
    """Amendment precedence is not just a proxy for the later filing date.

    The original is re-reported by a later annual filing, so on filing date
    alone it would win. It must not: the amendment is the corrected figure.
    """
    payload = company_facts(
        rows=[
            annual_row(
                start="2023-01-01",
                end="2023-12-31",
                val=98_000,
                accn="0000320193-24-000009",
                form="10-K/A",
                filed="2024-06-01",
                fy=2023,
            ),
            annual_row(
                start="2023-01-01",
                end="2023-12-31",
                val=100_000,
                accn="0000320193-25-000001",
                form="10-K",
                filed="2025-02-01",
                fy=2024,
            ),
        ]
    )

    facts = _revenue(await _extract(stub_edgar, payload))

    assert len(facts) == 1
    assert facts[0].value == 98_000
    assert facts[0].accession_no == "0000320193-24-000009"


# --- Fiscal year labelling --------------------------------------------------


async def test_fiscal_year_comes_from_the_filer_not_from_the_reporting_year(
    stub_edgar: StubEdgarClient,
) -> None:
    """EDGAR's `fy` is the filing's year; a comparative must not inherit it.

    Both rows below are reported by the FY2025 10-K with fy=2025. The FY2024
    period must still be labelled 2024.
    """
    payload = company_facts(
        rows=[
            annual_row(
                start="2023-10-01",
                end="2024-09-28",
                val=391_035_000_000,
                accn="0000320193-25-000079",
                filed="2025-10-31",
                fy=2025,
            ),
            annual_row(
                start="2024-09-29",
                end="2025-09-27",
                val=416_161_000_000,
                accn="0000320193-25-000079",
                filed="2025-10-31",
                fy=2025,
            ),
            annual_row(
                start="2023-10-01",
                end="2024-09-28",
                val=391_035_000_000,
                accn="0000320193-24-000123",
                filed="2024-11-01",
                fy=2024,
            ),
        ]
    )

    facts = _revenue(await _extract(stub_edgar, payload))
    by_year = {fact.period_end: fact.fiscal_year for fact in facts}

    assert by_year[dt.date(2025, 9, 27)] == 2025
    assert by_year[dt.date(2024, 9, 28)] == 2024


async def test_a_fiscal_year_the_filer_never_labelled_is_left_unset(
    stub_edgar: StubEdgarClient,
) -> None:
    """An unlabelled period reads as unlabelled. The year is not inferred."""
    payload = company_facts(
        rows=[
            annual_row(
                start="2023-01-01",
                end="2023-12-31",
                val=100_000,
                accn="0000320193-24-000001",
                filed="2024-02-01",
                fp="Q4",
            )
        ]
    )

    facts = _revenue(await _extract(stub_edgar, payload))

    assert len(facts) == 1
    assert facts[0].fiscal_year is None


# --- Missing metrics --------------------------------------------------------


async def test_a_metric_no_tag_answers_for_is_marked_not_disclosed(
    stub_edgar: StubEdgarClient,
) -> None:
    """Nothing is estimated. The gap is stated, with the filing that was searched."""
    payload = company_facts(
        rows=[
            annual_row(
                start="2023-10-01",
                end="2024-09-28",
                val=391_035_000_000,
                accn="0000320193-24-000123",
                filed="2024-11-01",
                fy=2024,
            )
        ]
    )
    filing = make_filing()

    facts = await _extract(stub_edgar, payload, manifest=[filing])
    markers = [
        fact
        for fact in facts
        if fact.extraction_method is ExtractionMethod.NOT_DISCLOSED
    ]

    assert markers, "metrics with no tag must produce a marker"
    for marker in markers:
        assert marker.value is None
        assert marker.display_value == "Not disclosed"
        assert marker.confidence == 0.0
        # A marker still names the filing that was searched.
        assert marker.accession_no == filing.accession_no
        assert marker.filed_date == filing.filed_date


async def test_no_marker_is_invented_without_a_filing_to_point_at(
    stub_edgar: StubEdgarClient,
) -> None:
    """With no annual filing there is nothing truthful to say, so nothing is said."""
    payload = company_facts(
        rows=[
            annual_row(
                start="2023-10-01",
                end="2024-09-28",
                val=391_035_000_000,
                accn="0000320193-24-000123",
                filed="2024-11-01",
                fy=2024,
            )
        ]
    )

    facts = await _extract(stub_edgar, payload, manifest=[])

    assert all(
        fact.extraction_method is not ExtractionMethod.NOT_DISCLOSED
        for fact in facts
    )


async def test_every_extracted_fact_carries_tier_one_filing_provenance(
    stub_edgar: StubEdgarClient,
) -> None:
    """Extraction cannot produce a fact that section 3 would have to refuse."""
    payload = company_facts(
        rows=[
            annual_row(
                start="2023-10-01",
                end="2024-09-28",
                val=391_035_000_000,
                accn="0000320193-24-000123",
                filed="2024-11-01",
                fy=2024,
            )
        ]
    )

    facts = await _extract(stub_edgar, payload, manifest=[make_filing()])

    assert facts
    for fact in facts:
        assert fact.tier is SourceTier.FILING
        assert str(fact.source_url).startswith("https://www.sec.gov/")
        assert fact.accession_no.strip()
        assert fact.display_value.strip()


async def test_rows_missing_provenance_are_skipped_not_repaired(
    stub_edgar: StubEdgarClient,
) -> None:
    """A row with no accession number cannot carry provenance, so it is dropped."""
    payload = company_facts(
        rows=[
            {
                "start": "2023-01-01",
                "end": "2023-12-31",
                "val": 100_000,
                "form": "10-K",
                "filed": "2024-02-01",
            },
            annual_row(
                start="2022-01-01",
                end="2022-12-31",
                val=90_000,
                accn="0000320193-23-000001",
                filed="2023-02-01",
                fy=2022,
            ),
        ]
    )

    facts = _revenue(await _extract(stub_edgar, payload))

    assert len(facts) == 1
    assert facts[0].value == 90_000


# --- Source URL -------------------------------------------------------------


async def test_provenance_prefers_the_manifest_document(
    stub_edgar: StubEdgarClient,
) -> None:
    """When m02 supplies the filing, the fact links to the filing itself."""
    payload = company_facts(
        rows=[
            annual_row(
                start="2023-10-01",
                end="2024-09-28",
                val=391_035_000_000,
                accn="0000320193-24-000123",
                filed="2024-11-01",
                fy=2024,
            )
        ]
    )
    filing = make_filing()

    facts = _revenue(
        await _extract(stub_edgar, payload, manifest=[filing])
    )

    assert str(facts[0].source_url) == str(filing.primary_doc_url)


async def test_provenance_falls_back_to_the_filing_index(
    stub_edgar: StubEdgarClient,
) -> None:
    """Without a manifest the reader still lands on the right filing."""
    payload = company_facts(
        rows=[
            annual_row(
                start="2023-10-01",
                end="2024-09-28",
                val=391_035_000_000,
                accn="0000320193-24-000123",
                filed="2024-11-01",
                fy=2024,
            )
        ]
    )

    facts = _revenue(await _extract(stub_edgar, payload, manifest=[]))

    assert "0000320193-24-000123-index.htm" in str(facts[0].source_url)


# --- Segment extraction -----------------------------------------------------


INSTANCE = """<?xml version="1.0" encoding="UTF-8"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:us-gaap="http://fasb.org/us-gaap/2024"
      xmlns:srt="http://fasb.org/srt/2024">
  <unit id="usd"><measure>iso4217:USD</measure></unit>

  <context id="fy">
    <entity><identifier scheme="s">320193</identifier></entity>
    <period><startDate>2023-10-01</startDate><endDate>2024-09-28</endDate></period>
  </context>

  <context id="americas">
    <entity><identifier scheme="s">320193</identifier>
      <segment>
        <xbrldi:explicitMember dimension="us-gaap:ConsolidationItemsAxis"
          >us-gaap:OperatingSegmentsMember</xbrldi:explicitMember>
        <xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis"
          >aapl:AmericasSegmentMember</xbrldi:explicitMember>
      </segment>
    </entity>
    <period><startDate>2023-10-01</startDate><endDate>2024-09-28</endDate></period>
  </context>

  <context id="europe">
    <entity><identifier scheme="s">320193</identifier>
      <segment>
        <xbrldi:explicitMember dimension="us-gaap:ConsolidationItemsAxis"
          >us-gaap:OperatingSegmentsMember</xbrldi:explicitMember>
        <xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis"
          >aapl:EuropeSegmentMember</xbrldi:explicitMember>
      </segment>
    </entity>
    <period><startDate>2023-10-01</startDate><endDate>2024-09-28</endDate></period>
  </context>

  <context id="crosstab">
    <entity><identifier scheme="s">320193</identifier>
      <segment>
        <xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis"
          >aapl:AmericasSegmentMember</xbrldi:explicitMember>
        <xbrldi:explicitMember
          dimension="us-gaap:FairValueByFairValueHierarchyLevelAxis"
          >us-gaap:FairValueInputsLevel1Member</xbrldi:explicitMember>
      </segment>
    </entity>
    <period><startDate>2023-10-01</startDate><endDate>2024-09-28</endDate></period>
  </context>

  <context id="quarter">
    <entity><identifier scheme="s">320193</identifier>
      <segment>
        <xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis"
          >aapl:AmericasSegmentMember</xbrldi:explicitMember>
      </segment>
    </entity>
    <period><startDate>2024-07-01</startDate><endDate>2024-09-28</endDate></period>
  </context>

  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax
    contextRef="fy" unitRef="usd" decimals="-6"
    >391035000000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax
    contextRef="americas" unitRef="usd" decimals="-6"
    >167045000000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax
    contextRef="europe" unitRef="usd" decimals="-6"
    >101328000000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax
    contextRef="crosstab" unitRef="usd" decimals="-6"
    >1000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax
    contextRef="quarter" unitRef="usd" decimals="-6"
    >2000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
</xbrl>
"""

_FILING_DIR = (
    "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123"
)


def _register_instance(
    stub_edgar: StubEdgarClient, *, body: str = INSTANCE
) -> None:
    stub_edgar.register(
        f"{_FILING_DIR}/index.json",
        {"directory": {"item": [
            {"name": "aapl-20240928_htm.xml"},
            {"name": "aapl-20240928_cal.xml"},
            {"name": "aapl-20240928_lab.xml"},
            {"name": "FilingSummary.xml"},
            {"name": "R2.htm"},
        ]}},
    )
    stub_edgar.register(f"{_FILING_DIR}/aapl-20240928_htm.xml", body)


async def test_segments_are_extracted_from_dimensional_facts(
    stub_edgar: StubEdgarClient,
) -> None:
    """Reportable segments are read, and carry their axis and member."""
    _register_instance(stub_edgar)

    facts = await m03.extract_segments(make_company(), [make_filing()])
    by_label = {fact.segment_label: fact.value for fact in facts}

    assert by_label == {"Americas": 167_045_000_000, "Europe": 101_328_000_000}
    for fact in facts:
        assert fact.metric == "segment.revenue"
        assert fact.extraction_method is ExtractionMethod.XBRL_DIMENSIONAL
        assert fact.segment_axis == "us-gaap:StatementBusinessSegmentsAxis"
        assert fact.segment_member is not None
        assert fact.segment_member.endswith("SegmentMember")
        assert fact.unit == "USD"
        assert fact.tier is SourceTier.FILING


async def test_a_cross_tabulated_context_is_not_a_segment(
    stub_edgar: StubEdgarClient,
) -> None:
    """Segment by fair-value level is not a segmentation of revenue."""
    _register_instance(stub_edgar)

    facts = await m03.extract_segments(make_company(), [make_filing()])

    assert all(fact.value != 1000 for fact in facts)


async def test_a_quarterly_segment_period_is_not_extracted(
    stub_edgar: StubEdgarClient,
) -> None:
    """The segment table is annual, matching the consolidated total it sums to."""
    _register_instance(stub_edgar)

    facts = await m03.extract_segments(make_company(), [make_filing()])

    assert all(fact.value != 2000 for fact in facts)


async def test_the_consolidated_figure_is_not_emitted_as_a_segment(
    stub_edgar: StubEdgarClient,
) -> None:
    """A context with no dimensions is the total, not a component of it."""
    _register_instance(stub_edgar)

    facts = await m03.extract_segments(make_company(), [make_filing()])

    assert all(fact.value != 391_035_000_000 for fact in facts)
    assert all(fact.segment_member is not None for fact in facts)


async def test_segment_failure_degrades_to_an_empty_list(
    stub_edgar: StubEdgarClient,
) -> None:
    """A malformed instance costs the segment table, not the report."""
    _register_instance(stub_edgar, body="<not-xml")

    facts = await m03.extract_segments(make_company(), [make_filing()])

    assert facts == []


async def test_a_missing_instance_document_degrades_quietly(
    stub_edgar: StubEdgarClient,
) -> None:
    """EDGAR returning nothing for the filing directory is not fatal."""
    facts = await m03.extract_segments(make_company(), [make_filing()])

    assert facts == []


async def test_segments_need_an_annual_filing_to_read(
    stub_edgar: StubEdgarClient,
) -> None:
    """With no annual report in the manifest there is nothing to parse."""
    quarterly = make_filing(form="10-Q", accession_no="0000320193-24-000050")

    facts = await m03.extract_segments(make_company(), [quarterly])

    assert facts == []


def test_currency_figures_display_in_millions() -> None:
    """The financials tables promise "USD millions" in their footnote, so the

    figure itself has to be divided by a million to match — not just carry
    the label.
    """
    assert m03._format_value(46_710_000_000, "USD") == "46,710"
    assert m03._format_value(605_000_000, "USD") == "605"
    assert m03._format_value(450_000, "USD") == "0.45"


def test_per_share_figures_are_not_scaled_to_millions() -> None:
    """EPS and dividends-per-share are read in SEC's "USD/shares" unit, which

    must stay off the millions conversion the same way share counts do.
    """
    assert m03._format_value(3.75, "USD/shares") == "3.75"
    assert m03._format_value(1_610_800_000, "shares") == "1,610,800,000"
