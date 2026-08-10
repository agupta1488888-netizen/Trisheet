"""Tests for the valuation endpoint and the response it assembles.

Two things are under test and they are different in kind.

The route's gates are ordinary API behaviour: an unknown report, a run still
in flight, a fact store that cannot be read.

The compliance boundary is not ordinary. Everything this endpoint returns is a
projection, and the one rule that keeps the product honest is that a projection
never becomes a fact. `Fact` cannot exist without an accession number and a
value computed from a discount rate names no filing — so the separation is
structural rather than a matter of labelling, and these assert it holds at the
wire, where a mistake would be visible to a reader.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import (
    MARKET_CAP_METRIC,
    VALUATION_NO_RECOMMENDATION_NOTE,
    VALUATION_PROJECTION_NOTE,
    Settings,
)
from app.main import create_app
from app.models import (
    ExtractionMethod,
    Fact,
    Report,
    ReportStatus,
    SourceTier,
    SourceType,
    ValuationQuery,
)
from app.modules import m06_factstore
from app.services import runlog
from tests.conftest import make_fact

REPORT_ID = "r-valuation"


def _client() -> TestClient:
    settings = Settings(
        environment="development",
        edgar_contact_email="tests@example.com",
        cors_allowed_origins="http://localhost:3000",
    )
    return TestClient(create_app(settings))


def _facts() -> list[Fact]:
    """The three real figures a discounted cash flow rests on, plus a quote."""
    return [
        make_fact(
            metric="cashflow.free_cash_flow",
            label="Free cash flow",
            value=100_000.0,
            display_value="100,000",
            period_start=None,
            period_end=dt.date(2025, 9, 27),
            fiscal_year=2025,
            is_calculated=True,
            formula="net cash from operating activities − capital expenditure",
        ),
        make_fact(
            metric="derived.net_debt",
            label="Net debt",
            value=20_000.0,
            display_value="20,000",
            period_start=None,
            period_end=dt.date(2025, 9, 27),
            fiscal_year=2025,
            is_calculated=True,
            formula="total debt FY2025 − cash and equivalents FY2025",
        ),
        make_fact(
            metric="income.shares_diluted",
            label="Diluted weighted average shares",
            value=15_000.0,
            display_value="15,000",
            period_start=dt.date(2024, 9, 29),
            period_end=dt.date(2025, 9, 27),
            fiscal_year=2025,
        ),
        make_fact(
            metric=MARKET_CAP_METRIC,
            label="Market capitalisation",
            value=2_000_000.0,
            display_value="2,000,000 USD",
            period_start=None,
            period_end=dt.date(2026, 8, 5),
            fiscal_year=None,
            tier=SourceTier.MARKET,
            source_type=SourceType.MARKET_DATA,
            source_url="https://query1.finance.yahoo.com/v8/finance/chart/X",
            accession_no="MARKET-YAHOO-20260805T084611Z",
            filed_date=dt.date(2026, 8, 5),
            extraction_method=ExtractionMethod.CALCULATED,
            resolved_tag=None,
            taxonomy=None,
            is_calculated=True,
            formula="share price × shares outstanding",
        ),
    ]


@pytest.fixture
def completed_report(monkeypatch: pytest.MonkeyPatch) -> list[Fact]:
    """A complete report whose facts load without a database."""
    facts = _facts()
    report = Report(
        id=REPORT_ID,
        ticker="AAPL",
        cik="0000320193",
        status=ReportStatus.COMPLETE,
        created_at=dt.datetime(2026, 8, 5, tzinfo=dt.UTC),
        completed_at=dt.datetime(2026, 8, 5, tzinfo=dt.UTC),
    )
    monkeypatch.setattr(
        runlog,
        "get_report",
        lambda report_id: report if report_id == REPORT_ID else None,
    )

    async def load(report_id: str) -> list[Fact]:
        return list(facts)

    monkeypatch.setattr(m06_factstore, "load_facts", load)
    return facts


# --- The route's gates --------------------------------------------------------


def test_an_unknown_report_is_a_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runlog, "get_report", lambda report_id: None)

    with _client() as client:
        response = client.get("/reports/nope/valuation")

    assert response.status_code == 404


def test_a_run_still_in_flight_is_refused_in_the_interface_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = Report(
        id=REPORT_ID,
        ticker="AAPL",
        cik="0000320193",
        status=ReportStatus.ANALYSING,
        created_at=dt.datetime(2026, 8, 5, tzinfo=dt.UTC),
    )
    monkeypatch.setattr(runlog, "get_report", lambda report_id: report)

    with _client() as client:
        response = client.get(f"/reports/{REPORT_ID}/valuation")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "valuation_unavailable"
    assert "still being generated" in body["message"]


def test_unreadable_facts_are_a_410_not_a_stack_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = Report(
        id=REPORT_ID,
        ticker="AAPL",
        cik="0000320193",
        status=ReportStatus.COMPLETE,
        created_at=dt.datetime(2026, 8, 5, tzinfo=dt.UTC),
    )
    monkeypatch.setattr(runlog, "get_report", lambda report_id: report)

    async def fail(report_id: str) -> list[Fact]:
        raise m06_factstore.FactStoreError("no database")

    monkeypatch.setattr(m06_factstore, "load_facts", fail)

    with _client() as client:
        response = client.get(f"/reports/{REPORT_ID}/valuation")

    assert response.status_code == 410
    assert response.json()["code"] == "facts_unavailable"


# --- The default view ---------------------------------------------------------


def test_a_clean_url_solves_for_the_implied_growth_rate(
    completed_report: list[Fact],
) -> None:
    """No query string is the reverse view, which assumes no forecast."""
    with _client() as client:
        response = client.get(f"/reports/{REPORT_ID}/valuation")

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["mode"] == "reverse"
    assert body["estimate"] is None
    implied = body["implied"]
    assert implied["unavailableReason"] is None
    assert implied["converged"] is True
    assert implied["impliedGrowthRate"]["source"] == "solved"
    assert implied["impliedGrowthRate"]["display"].endswith("%")


def test_the_forward_mode_projects_from_the_supplied_assumptions(
    completed_report: list[Fact],
) -> None:
    with _client() as client:
        response = client.get(
            f"/reports/{REPORT_ID}/valuation",
            params={"mode": "forward", "r": 9, "g": 6, "tg": 2.5, "n": 5},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "forward"
    assert body["implied"] is None
    estimate = body["estimate"]
    assert estimate["unavailableReason"] is None
    assert estimate["equityValue"]["display"]
    assert estimate["projectionYears"] == 5
    # The rate the reader chose is labelled as theirs, not as a default.
    assert estimate["discountRate"]["source"] == "user_supplied"
    assert estimate["fcfGrowthRate"]["source"] == "user_supplied"


def test_an_out_of_range_assumption_is_refused_rather_than_clamped_silently(
    completed_report: list[Fact],
) -> None:
    with _client() as client:
        response = client.get(
            f"/reports/{REPORT_ID}/valuation", params={"r": 500}
        )

    assert response.status_code == 422


def test_a_malformed_assumption_does_not_500(
    completed_report: list[Fact],
) -> None:
    with _client() as client:
        response = client.get(
            f"/reports/{REPORT_ID}/valuation", params={"g": "banana"}
        )

    assert response.status_code == 422


# --- The compliance boundary --------------------------------------------------


def test_the_response_carries_the_real_facts_it_rested_on(
    completed_report: list[Fact],
) -> None:
    """So the interface can cite an input exactly as it cites a filed figure."""
    with _client() as client:
        body = client.get(f"/reports/{REPORT_ID}/valuation").json()

    inputs = body["inputs"]
    assert inputs
    metrics = {fact["metric"] for fact in inputs}
    assert "cashflow.free_cash_flow" in metrics
    assert "derived.net_debt" in metrics
    # Each is a real fact and says so: accession number, filing date, tier.
    for fact in inputs:
        assert fact["accessionNo"]
        assert fact["filedDate"]
        assert fact["tier"] in {1, 2, 3, 4}


def test_only_the_facts_used_are_returned(completed_report: list[Fact]) -> None:
    """Returning everything would put cards in the rail for untouched figures."""
    completed_report.append(
        make_fact(
            metric="income.revenue",
            value=999.0,
            display_value="999",
            fiscal_year=2025,
            period_end=dt.date(2025, 9, 27),
        )
    )

    with _client() as client:
        body = client.get(f"/reports/{REPORT_ID}/valuation").json()

    assert "income.revenue" not in {fact["metric"] for fact in body["inputs"]}


def test_no_projected_figure_claims_a_source(
    completed_report: list[Fact],
) -> None:
    """The absence of a source card is what marks a projection as one.

    A ValuationFigure has a value, a rendering and a unit — and deliberately no
    accession number, no filing date and no source url. If one ever gained
    them it would render in the rail beside figures read off a filing.
    """
    with _client() as client:
        body = client.get(
            f"/reports/{REPORT_ID}/valuation",
            params={"mode": "forward", "g": 6},
        ).json()

    projected = body["estimate"]["equityValue"]
    assert set(projected) == {"value", "display", "unit"}
    for cell in body["sensitivity"]["cells"]:
        for key in ("valuePerShare", "equityValue"):
            if cell[key] is not None:
                assert "accessionNo" not in cell[key]


def test_the_standing_statements_travel_with_the_figures(
    completed_report: list[Fact],
) -> None:
    """Worded in config so the assistant and the workbench cannot differ."""
    with _client() as client:
        body = client.get(f"/reports/{REPORT_ID}/valuation").json()

    assert VALUATION_PROJECTION_NOTE in body["notes"]
    assert VALUATION_NO_RECOMMENDATION_NOTE in body["notes"]


def test_a_valuation_request_writes_nothing_back(
    completed_report: list[Fact], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A projection must never reach the fact store.

    The report's compliance was settled before this question was asked, and
    counting a projected figure toward it would make the coverage score a
    statement about arithmetic rather than about filings.
    """
    called: list[object] = []

    async def store(*args: object, **kwargs: object) -> None:
        called.append(args)

    monkeypatch.setattr(m06_factstore, "store_facts", store)

    with _client() as client:
        assert client.get(f"/reports/{REPORT_ID}/valuation").status_code == 200

    assert called == []


# --- The query contract -------------------------------------------------------


def test_percentages_are_converted_once_at_the_boundary() -> None:
    """The query string is user-facing, so it carries 9 rather than 0.09."""
    query = ValuationQuery.model_validate({"r": 9, "g": 6, "tg": 2.5})

    assert query.as_rates() == (0.09, 0.06, 0.025)


def test_an_absent_parameter_means_the_default() -> None:
    """Which is what keeps a clean URL the default view."""
    assert ValuationQuery().as_rates() == (None, None, None)
