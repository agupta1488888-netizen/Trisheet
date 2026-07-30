"""Tests for m06 — the write gate.

The gate is the point of this module: what it refuses matters more than what
it stores. Every rejection path below is a rule from CLAUDE.md that would
otherwise depend on an upstream module remembering to behave.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import pytest

from app.models import ExtractionMethod, SourceTier, SourceType
from app.modules import m06_factstore as m06
from app.modules.m06_factstore import RejectionCode
from app.services import db
from tests.conftest import APPLE_ACCESSION, make_fact


class FakeTable:
    """Records upserts and serves canned rows, in the supabase-py shape."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.upserted: list[list[dict[str, Any]]] = []
        self.conflict: str | None = None
        self.filters: dict[str, Any] = {}
        self.fail: Exception | None = None

    def upsert(self, rows: list[dict[str, Any]], on_conflict: str) -> FakeTable:
        if self.fail is not None:
            raise self.fail
        self.upserted.append(rows)
        self.conflict = on_conflict
        return self

    def select(self, columns: str) -> FakeTable:
        return self

    def eq(self, column: str, value: Any) -> FakeTable:
        self.filters[column] = value
        return self

    def order(self, column: str, desc: bool = False) -> FakeTable:
        return self

    def execute(self) -> Any:
        if self.fail is not None:
            raise self.fail
        rows = [
            # report_id is filtered on but not selected, exactly as in the
            # real query — the Fact model forbids unknown keys.
            {k: v for k, v in row.items() if k != "report_id"}
            for row in self.rows
            if all(row.get(k) == v for k, v in self.filters.items())
        ]
        return type("Response", (), {"data": rows})()


class FakeClient:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.table_obj = FakeTable(rows or [])

    def table(self, name: str) -> FakeTable:
        return self.table_obj


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    """A configured database that records what it was asked to write.

    `is_configured` is patched alongside the client because the write path
    consults it before opening a connection: an unconfigured database is a
    skip, not a failure, so without this the gate would run and write nothing.
    """
    client = FakeClient()
    monkeypatch.setattr(db, "is_configured", lambda: True)
    monkeypatch.setattr(db, "get_client", lambda: client)
    return client


REPORT_ID = "11111111-1111-1111-1111-111111111111"


def _row(**overrides: Any) -> dict[str, Any]:
    """A fact as a mapping, the shape an upstream module hands the gate."""
    row: dict[str, Any] = {
        "metric": "income.revenue",
        "label": "Revenue",
        "value": 391_035_000_000.0,
        "display_value": "391,035,000,000",
        "unit": "USD",
        "period_start": "2023-10-01",
        "period_end": "2024-09-28",
        "fiscal_year": 2024,
        "tier": 1,
        "source_type": "sec_xbrl",
        "source_url": "https://www.sec.gov/Archives/edgar/data/320193/x.htm",
        "accession_no": APPLE_ACCESSION,
        "filed_date": "2024-11-01",
        "extraction_method": "xbrl_company_facts",
        "confidence": 1.0,
    }
    row.update(overrides)
    return {k: v for k, v in row.items() if v is not _OMIT}


_OMIT = object()


# --- The happy path ---------------------------------------------------------


async def test_a_fully_sourced_fact_is_stored(fake_db: FakeClient) -> None:
    result = await m06.store_facts(REPORT_ID, [make_fact()])

    assert result.stored == 1
    assert result.rejected == 0
    assert fake_db.table_obj.upserted[0][0]["metric"] == "income.revenue"
    assert fake_db.table_obj.upserted[0][0]["report_id"] == REPORT_ID


async def test_the_upsert_targets_the_unique_constraint(
    fake_db: FakeClient,
) -> None:
    """Re-running a report must converge, not collide with its own last write."""
    await m06.store_facts(REPORT_ID, [make_fact()])

    assert fake_db.table_obj.conflict == (
        "report_id,metric,period_end,period_start,segment_axis,segment_member"
    )


async def test_a_mapping_with_full_provenance_is_accepted(
    fake_db: FakeClient,
) -> None:
    result = await m06.store_facts(REPORT_ID, [_row()])

    assert result.stored == 1
    assert result.rejected == 0


# --- Missing provenance -----------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["tier", "source_type", "source_url", "accession_no", "filed_date"],
)
async def test_a_fact_missing_any_provenance_field_is_rejected(
    fake_db: FakeClient, field: str
) -> None:
    """Each of the five required fields is individually load-bearing."""
    result = await m06.store_facts(REPORT_ID, [_row(**{field: _OMIT})])

    assert result.stored == 0
    assert result.rejected == 1
    assert result.rejections[0].code == RejectionCode.MISSING_PROVENANCE
    assert field in result.rejections[0].reason
    assert fake_db.table_obj.upserted == []


async def test_a_blank_accession_number_is_not_provenance(
    fake_db: FakeClient,
) -> None:
    """Whitespace satisfies the type but names no source."""
    result = await m06.store_facts(REPORT_ID, [_row(accession_no="   ")])

    assert result.rejected == 1
    assert result.stored == 0


async def test_a_rejection_is_logged(
    fake_db: FakeClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Rejections are never silent — that is what makes this a gate."""
    with caplog.at_level(logging.WARNING, logger="app.modules.m06_factstore"):
        await m06.store_facts(REPORT_ID, [_row(source_url=_OMIT)])

    assert any(
        "Fact rejected at write time" in record.message
        for record in caplog.records
    )
    assert any(
        getattr(record, "rejection_code", None) == RejectionCode.MISSING_PROVENANCE
        for record in caplog.records
    )


async def test_one_bad_fact_does_not_stop_the_good_ones(
    fake_db: FakeClient,
) -> None:
    """A malformed candidate is refused; the sound ones are still written."""
    result = await m06.store_facts(
        REPORT_ID,
        [_row(), _row(metric="income.net_income", accession_no=_OMIT), _row(
            metric="balance.total_assets", period_start=None
        )],
    )

    assert result.stored == 2
    assert result.rejected == 1


async def test_something_that_is_not_a_fact_at_all_is_refused(
    fake_db: FakeClient,
) -> None:
    result = await m06.store_facts(REPORT_ID, ["391 billion"])  # type: ignore[list-item]

    assert result.stored == 0
    assert result.rejections[0].code == RejectionCode.MALFORMED


# --- Tier enforcement -------------------------------------------------------


async def test_a_market_figure_cannot_claim_tier_one(
    fake_db: FakeClient,
) -> None:
    """The tier is not a label a caller gets to choose freely."""
    result = await m06.store_facts(
        REPORT_ID,
        [_row(metric="market.price", source_type="market_data", tier=1)],
    )

    assert result.stored == 0
    assert result.rejections[0].code == RejectionCode.TIER_MISMATCH


async def test_tier_three_is_hard_blocked_from_section_three(
    fake_db: FakeClient,
) -> None:
    """Market data may not appear in the financial highlights, ever."""
    result = await m06.store_facts(
        REPORT_ID,
        [
            _row(
                metric="income.revenue",
                source_type="market_data",
                tier=3,
                extraction_method="market_data",
            )
        ],
    )

    assert result.stored == 0
    assert result.rejections[0].code == RejectionCode.SECTION_TIER_BLOCKED
    assert "section 3" in result.rejections[0].reason


async def test_tier_four_is_hard_blocked_from_section_three(
    fake_db: FakeClient,
) -> None:
    result = await m06.store_facts(
        REPORT_ID,
        [
            _row(
                metric="balance.total_assets",
                source_type="news",
                tier=4,
                extraction_method="narrative",
            )
        ],
    )

    assert result.stored == 0
    assert result.rejections[0].code == RejectionCode.SECTION_TIER_BLOCKED


async def test_tier_two_is_allowed_in_section_three(
    fake_db: FakeClient,
) -> None:
    """Section 3 accepts tiers 1 and 2; a press release figure is admissible."""
    result = await m06.store_facts(
        REPORT_ID,
        [
            _row(
                source_type="press_release",
                tier=2,
                extraction_method="narrative",
            )
        ],
    )

    assert result.stored == 1
    assert result.rejected == 0


async def test_tier_three_is_allowed_outside_section_three(
    fake_db: FakeClient,
) -> None:
    """The block is on section 3, not on Tier 3 existing."""
    result = await m06.store_facts(
        REPORT_ID,
        [
            _row(
                metric="market.share_price",
                source_type="market_data",
                tier=3,
                extraction_method="market_data",
            )
        ],
    )

    assert result.stored == 1


# --- Model-level invariants -------------------------------------------------


async def test_a_calculated_fact_without_its_formula_is_refused(
    fake_db: FakeClient,
) -> None:
    """A derived figure is rendered with its working, or not at all."""
    result = await m06.store_facts(
        REPORT_ID,
        [
            _row(
                metric="ratio.gross_margin",
                is_calculated=True,
                extraction_method="calculated",
            )
        ],
    )

    assert result.stored == 0
    assert result.rejections[0].code == RejectionCode.MALFORMED


async def test_a_not_disclosed_marker_cannot_carry_a_value(
    fake_db: FakeClient,
) -> None:
    """Nothing is estimated, including under a "not disclosed" label."""
    result = await m06.store_facts(
        REPORT_ID,
        [
            _row(
                extraction_method="not_disclosed",
                value=391_035_000_000.0,
                display_value="Not disclosed",
            )
        ],
    )

    assert result.stored == 0
    assert result.rejections[0].code == RejectionCode.MALFORMED


async def test_a_blank_display_value_is_refused(fake_db: FakeClient) -> None:
    """A missing figure reads "Not disclosed", never an empty cell."""
    result = await m06.store_facts(REPORT_ID, [_row(display_value="  ")])

    assert result.stored == 0
    assert result.rejections[0].code == RejectionCode.MALFORMED


def test_a_segment_member_without_its_axis_will_not_construct() -> None:
    """Half a segment reference is unattributable, so it cannot exist."""
    with pytest.raises(ValueError, match="segment_axis and segment_member"):
        make_fact(segment_member="aapl:AmericasSegmentMember")


# --- Typed queries ----------------------------------------------------------


def _stored_row(**overrides: Any) -> dict[str, Any]:
    row = _row()
    row.update(
        {
            "report_id": REPORT_ID,
            "segment_axis": None,
            "segment_member": None,
            "segment_label": None,
            "resolved_tag": None,
            "taxonomy": None,
            "is_calculated": False,
            "formula": None,
        }
    )
    row.update(overrides)
    return row


async def test_load_by_section_returns_only_that_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        [
            _stored_row(metric="income.revenue"),
            _stored_row(metric="cashflow.operating"),
            _stored_row(
                metric="market.share_price",
                source_type="market_data",
                tier=3,
                extraction_method="market_data",
            ),
        ]
    )
    monkeypatch.setattr(db, "get_client", lambda: client)

    facts = await m06.load_facts_by_section(REPORT_ID, 3)

    assert {fact.metric for fact in facts} == {
        "income.revenue",
        "cashflow.operating",
    }


async def test_load_by_metric_filters_on_the_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient([_stored_row(), _stored_row(metric="income.net_income")])
    monkeypatch.setattr(db, "get_client", lambda: client)

    facts = await m06.load_facts_by_metric(REPORT_ID, "income.revenue")

    assert [fact.metric for fact in facts] == ["income.revenue"]


async def test_load_by_period_requires_a_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returning everything for an unspecified period would be silently wrong."""
    monkeypatch.setattr(db, "get_client", lambda: FakeClient([]))

    with pytest.raises(ValueError, match="fiscal_year or period_end"):
        await m06.load_facts_by_period(REPORT_ID)


async def test_load_by_period_selects_one_fiscal_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        [
            _stored_row(fiscal_year=2024),
            _stored_row(metric="income.net_income", fiscal_year=2023),
        ]
    )
    monkeypatch.setattr(db, "get_client", lambda: client)

    facts = await m06.load_facts_by_period(REPORT_ID, fiscal_year=2024)

    assert [fact.fiscal_year for fact in facts] == [2024]


async def test_a_row_that_no_longer_validates_is_dropped_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schema drift costs one row, not the whole read."""
    client = FakeClient([_stored_row(), _stored_row(display_value="")])
    monkeypatch.setattr(db, "get_client", lambda: client)

    facts = await m06.load_facts(REPORT_ID)

    assert len(facts) == 1


def test_section_for_metric_maps_the_financial_metrics_to_section_three() -> None:
    """m06 and m11 must not be able to disagree about what section 3 holds."""
    assert m06.section_for_metric("income.revenue") == 3
    assert m06.section_for_metric("balance.total_assets") == 3
    assert m06.section_for_metric("cashflow.operating") == 3
    assert m06.section_for_metric("segment.revenue") == 4
    assert m06.section_for_metric("market.share_price") == 5
    assert m06.section_for_metric("nothing.mapped") is None


# --- Failure handling -------------------------------------------------------


async def test_a_database_failure_is_a_typed_error(
    fake_db: FakeClient,
) -> None:
    """Callers catch FactStoreError, never a raw driver exception."""
    fake_db.table_obj.fail = RuntimeError("connection reset")

    with pytest.raises(m06.FactStoreError, match="Could not store facts"):
        await m06.store_facts(REPORT_ID, [make_fact()])


async def test_an_unconfigured_database_admits_without_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persistence is a sink, not a source. Only EDGAR is a hard dependency.

    The gate still ran and the fact is still usable — it was simply not
    written down, which the result says outright rather than implying.
    """
    monkeypatch.setattr(db, "is_configured", lambda: False)
    monkeypatch.setattr(db, "get_client", _unconfigured)

    result = await m06.store_facts(REPORT_ID, [make_fact()])

    assert result.stored == 1
    assert result.persisted is False
    assert len(result.accepted) == 1


async def test_reading_from_an_unconfigured_database_is_a_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read cannot degrade the way a write can — there is nothing to return."""
    monkeypatch.setattr(db, "get_client", _unconfigured)

    with pytest.raises(m06.FactStoreError, match="not configured"):
        await m06.load_facts(REPORT_ID)


def _unconfigured() -> Any:
    message = "Supabase is not configured."
    raise db.DatabaseNotConfiguredError(message)


async def test_nothing_to_store_touches_no_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An all-rejected batch must not open a connection it does not need.

    The database is configured here, so reaching for the client would succeed
    in production and raise in this test — which is the point.
    """
    monkeypatch.setattr(db, "is_configured", lambda: True)
    monkeypatch.setattr(db, "get_client", _unconfigured)

    result = await m06.store_facts(REPORT_ID, [_row(accession_no=_OMIT)])

    assert result.stored == 0
    assert result.rejected == 1


# --- Serialisation ----------------------------------------------------------


async def test_a_fact_round_trips_through_its_row(
    fake_db: FakeClient,
) -> None:
    """Every field survives the write, including the extraction trail."""
    fact = make_fact(
        segment_axis="us-gaap:StatementBusinessSegmentsAxis",
        segment_member="aapl:AmericasSegmentMember",
        segment_label="Americas",
        metric="segment.revenue",
        confidence=0.95,
    )

    await m06.store_facts(REPORT_ID, [fact])
    row = fake_db.table_obj.upserted[0][0]

    assert row["segment_axis"] == "us-gaap:StatementBusinessSegmentsAxis"
    assert row["segment_member"] == "aapl:AmericasSegmentMember"
    assert row["segment_label"] == "Americas"
    assert row["extraction_method"] == "xbrl_company_facts"
    assert row["confidence"] == 0.95
    assert row["tier"] == 1
    assert row["period_end"] == "2024-09-28"
    assert row["filed_date"] == "2024-11-01"
    assert row["taxonomy"] == "us-gaap"


def test_the_fact_model_refuses_provenance_free_construction() -> None:
    """The type is the first wall; the gate is the second."""
    with pytest.raises(ValueError, match="accession_no"):
        make_fact(accession_no="")


def test_enums_used_in_rows_match_the_database_vocabulary() -> None:
    """These strings are the enum labels in db/schema.sql."""
    assert str(SourceType.SEC_XBRL) == "sec_xbrl"
    assert int(SourceTier.FILING) == 1
    assert str(ExtractionMethod.XBRL_COMPANY_FACTS) == "xbrl_company_facts"
    assert str(ExtractionMethod.NOT_DISCLOSED) == "not_disclosed"
    assert dt.date(2024, 9, 28).isoformat() == "2024-09-28"
