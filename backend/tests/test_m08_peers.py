"""Tests for m08: peer name extraction and peer comparison.

`build_peer_comparison` is covered here directly — the pure row-building
arithmetic, and the orchestration that reads a peer's own filings the way the
subject's are read. `find_company_names` and `_capitalised_runs` (the ladder's
name-extraction step, public per the module's own docstring precisely so it
can be exercised without a network) are covered directly too: this is the
step that decides who ends up on the comparables table, and a false positive
there — a compensation consultant mistaken for a peer, a generic word
colliding with an obscure ticker — reaches the report with no further check.

Network calls are never exercised here. `build_peer_comparison` delegates to
m01/m02/m03/m05, which are monkeypatched with fakes, so what is under test is
m08's own logic: relabelling, the valuation formulas, and degrading one peer
at a time rather than the whole table.
"""

from __future__ import annotations

import datetime as dt
from typing import cast

import pytest

from app.config import (
    COMPETITION_MARKERS,
    PEER_COMPARISON_MAX_COUNT,
    PEER_CONTEXT_WINDOW_CHARS,
)
from app.models import (
    Company,
    ExtractionMethod,
    Fact,
    FilerType,
    Peer,
    PeerSelectionMethod,
    PeerSet,
    SourceTier,
    SourceType,
)
from app.modules import m02_discovery, m03_financials, m05_market
from app.modules import m08_peers as m08
from app.modules.m01_resolver import IndexEntry
from app.modules.m08_peers import PeerComparison, PeerComparisonRow
from app.services import document
from app.services.edgar import EdgarClient
from tests.conftest import APPLE_ACCESSION, make_company, make_fact

PERIOD_END = dt.date(2024, 9, 28)


def _peer(*, cik: str, ticker: str, name: str) -> Peer:
    return Peer(
        cik=cik,
        ticker=ticker,
        name=name,
        selection_method=PeerSelectionMethod.SIC_MATCH,
        selection_note=f"Selected because it shares a SIC code with {name}.",
        sic_code="3571",
        filer_type=FilerType.DOMESTIC,
    )


def _market_fact(**overrides: object) -> Fact:
    fields: dict[str, object] = {
        "metric": "market.market_cap",
        "label": "Market capitalisation",
        "value": 50_000.0,
        "display_value": "50,000 USD",
        "unit": "USD",
        "period_end": PERIOD_END,
        "tier": SourceTier.MARKET,
        "source_type": SourceType.MARKET_DATA,
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/X",
        "accession_no": "YAHOO-20240928T000000Z",
        "filed_date": PERIOD_END,
        "extraction_method": ExtractionMethod.MARKET_DATA,
        "confidence": 1.0,
    }
    fields.update(overrides)
    return make_fact(**fields)


def _subject_facts() -> list[Fact]:
    """A subject's own facts: exactly the metrics a comparison row reads."""
    return [
        make_fact(
            metric="income.revenue",
            value=1_000.0,
            display_value="1,000",
            period_end=PERIOD_END,
            fiscal_year=2024,
        ),
        make_fact(
            metric="margin.net",
            label="Net margin",
            value=10.0,
            display_value="10.0%",
            unit="percent",
            period_end=PERIOD_END,
            fiscal_year=2024,
            is_calculated=True,
            formula="net income FY2024 ÷ revenue FY2024",
        ),
        make_fact(
            metric="margin.operating",
            label="Operating margin",
            value=15.0,
            display_value="15.0%",
            unit="percent",
            period_end=PERIOD_END,
            fiscal_year=2024,
            is_calculated=True,
            formula="operating income FY2024 ÷ revenue FY2024",
        ),
        make_fact(
            metric="growth.income.revenue.yoy",
            label="Revenue growth, year on year",
            value=5.0,
            display_value="5.0%",
            unit="percent",
            period_end=PERIOD_END,
            fiscal_year=2024,
            is_calculated=True,
            formula="(revenue FY2024 − revenue FY2023) ÷ revenue FY2023",
        ),
        make_fact(
            metric="income.net_income",
            value=100.0,
            display_value="100",
            period_end=PERIOD_END,
            fiscal_year=2024,
        ),
        make_fact(
            metric="derived.ebitda",
            label="EBITDA",
            value=200.0,
            display_value="200",
            period_end=PERIOD_END,
            fiscal_year=2024,
            is_calculated=True,
            formula="operating income FY2024 + depreciation and amortisation FY2024",
        ),
        make_fact(
            metric="derived.total_debt",
            label="Total debt",
            value=300.0,
            display_value="300",
            period_end=PERIOD_END,
            fiscal_year=2024,
            is_calculated=True,
            formula="short-term debt FY2024 + long-term debt FY2024",
        ),
        make_fact(
            metric="balance.cash_and_equivalents",
            value=50.0,
            display_value="50",
            period_end=PERIOD_END,
            fiscal_year=2024,
        ),
        _market_fact(),
    ]


# --- Peer name extraction ------------------------------------------------
#
# These target the precision fix for false-positive peers: a proxy's
# compensation consultant mistaken for a named peer, and a generic
# capitalised word colliding with an obscure ticker (both observed live for
# a NIKE report, which is what motivated the fix).

_NAME_INDEX_ENTRIES = [
    IndexEntry(cik="0000320193", ticker="AAPL", name="Apple Inc"),
    IndexEntry(cik="0000789019", ticker="MSFT", name="Microsoft Corp"),
    IndexEntry(cik="0000105620", ticker="PAYD", name="Paid Inc"),
    IndexEntry(cik="0000320187", ticker="NKE", name="Nike Inc"),
]


def _name_index() -> m08._NameIndex:
    return m08._NameIndex(_NAME_INDEX_ENTRIES)


def test_find_company_names_resolves_a_capitalised_run() -> None:
    text = (
        "Our compensation peer group consists of Apple Inc and Microsoft Corp."
    )

    found = m08.find_company_names(text, _name_index())

    assert [entry.ticker for entry in found] == ["AAPL", "MSFT"]


def test_find_company_names_rejects_stopword_collisions() -> None:
    # "Paid Inc" is a real ticker in the index, but the word "paid" collides
    # constantly with ordinary prose ("employees are paid competitively")
    # that names no company at all.
    text = "Paid Inc is a stopword collision, not a company mentioned here."

    found = m08.find_company_names(text, _name_index())

    assert found == []


def test_find_company_names_ignores_unresolved_capitalised_phrases() -> None:
    text = "The Emerging Markets Group discussed strategy with People Analytics."

    found = m08.find_company_names(text, _name_index())

    assert found == []


def test_competition_markers_exclude_the_bare_word() -> None:
    # "competition" alone is ordinary risk-factor boilerplate ("competition
    # for talent") and previously opened a window anywhere it appeared, with
    # no named competitor nearby — that false-positive source is removed by
    # keeping only phrases that actually precede a named list.
    assert "competition" not in COMPETITION_MARKERS
    assert "we compete" in COMPETITION_MARKERS


def test_peer_context_window_stays_close_to_the_marker() -> None:
    # A filer's named peer/competitor list sits next to the marker phrase.
    # Apple Inc, mentioned a thousand characters away from "peer group" in
    # unrelated prose, must not be swept into the same window.
    filler = "This paragraph discusses unrelated matters. " * 30
    text = (
        f"Our compensation peer group consists of Microsoft Corp. {filler}"
        "Apple Inc is also discussed far below."
    )

    windows = document.windows_around(
        text, ("peer group",), PEER_CONTEXT_WINDOW_CHARS
    )

    assert windows
    joined = " ".join(windows)
    assert "Microsoft Corp" in joined
    assert "Apple Inc" not in joined


# --- Pure helpers -------------------------------------------------------


def test_latest_by_metric_picks_the_more_recent_period() -> None:
    older = make_fact(period_end=dt.date(2023, 9, 30), fiscal_year=2023, value=1.0)
    newer = make_fact(period_end=PERIOD_END, fiscal_year=2024, value=2.0)

    latest = m08._latest_by_metric([older, newer])

    assert latest["income.revenue"] is newer


def test_latest_by_metric_ignores_not_disclosed_and_segment_facts() -> None:
    not_disclosed = make_fact(
        value=None,
        display_value="Not disclosed",
        extraction_method=ExtractionMethod.NOT_DISCLOSED,
        confidence=0.0,
    )
    segment = make_fact(
        value=10.0,
        segment_axis="us-gaap:StatementBusinessSegmentsAxis",
        segment_member="us-gaap:FootwearMember",
        segment_label="Footwear",
    )

    latest = m08._latest_by_metric([not_disclosed, segment])

    assert latest == {}


def test_relabel_embeds_the_ticker_and_keeps_provenance() -> None:
    fact = make_fact(metric="income.revenue", label="Revenue")

    relabelled = m08._relabel(fact, "NKE", "income.revenue")

    assert relabelled is not None
    assert relabelled.metric == "peer.comparison.NKE.income.revenue"
    assert relabelled.label == "NKE — Revenue"
    assert relabelled.source_url == fact.source_url
    assert relabelled.accession_no == fact.accession_no
    assert relabelled.tier == fact.tier
    assert relabelled.value == fact.value


def test_relabel_passes_through_none() -> None:
    assert m08._relabel(None, "NKE", "income.revenue") is None


def test_ratio_guards_a_near_zero_denominator() -> None:
    assert m08._ratio(10.0, 0.0) is None
    assert m08._ratio(10.0, 1e-12) is None
    assert m08._ratio(10.0, 2.0) == 5.0


def test_price_to_earnings_computes_from_market_cap_and_net_income() -> None:
    latest = m08._latest_by_metric(_subject_facts())

    fact = m08._price_to_earnings(latest, "NKE")

    assert fact is not None
    assert fact.value == pytest.approx(500.0)  # 50,000 / 100
    assert fact.tier == SourceTier.MARKET
    assert fact.is_calculated is True
    assert "net income" in (fact.formula or "")


def test_price_to_earnings_is_none_without_net_income() -> None:
    facts = [f for f in _subject_facts() if f.metric != "income.net_income"]
    latest = m08._latest_by_metric(facts)

    assert m08._price_to_earnings(latest, "NKE") is None


def test_ev_to_ebitda_computes_enterprise_value_over_ebitda() -> None:
    latest = m08._latest_by_metric(_subject_facts())

    fact = m08._ev_to_ebitda(latest, "NKE")

    # (50,000 market cap + 300 debt - 50 cash) / 200 ebitda
    assert fact is not None
    assert fact.value == pytest.approx(251.25)
    assert fact.tier == SourceTier.MARKET


@pytest.mark.parametrize(
    "missing_metric",
    ["derived.total_debt", "balance.cash_and_equivalents", "derived.ebitda"],
)
def test_ev_to_ebitda_is_none_without_every_input(missing_metric: str) -> None:
    """Rather than treating an undisclosed input as zero, and understating it."""
    facts = [f for f in _subject_facts() if f.metric != missing_metric]
    latest = m08._latest_by_metric(facts)

    assert m08._ev_to_ebitda(latest, "NKE") is None


def test_row_from_facts_builds_every_cell() -> None:
    row = m08._row_from_facts(
        ticker="NKE", name="NIKE, Inc.", is_subject=True, facts=_subject_facts()
    )

    assert row.is_subject is True
    assert row.revenue is not None and row.revenue.value == 1_000.0
    assert row.net_margin is not None and row.net_margin.value == 10.0
    assert row.operating_margin is not None
    assert row.revenue_growth is not None
    assert row.price_to_earnings is not None
    assert row.ev_to_ebitda is not None
    # Every fact on the row was relabelled into this row's namespace.
    assert all(fact.metric.startswith("peer.comparison.NKE.") for fact in row.facts)


def test_row_from_facts_degrades_gracefully_when_some_metrics_are_absent() -> None:
    """A company that discloses less still gets a row, just with fewer cells."""
    facts = [
        f
        for f in _subject_facts()
        if f.metric not in {"margin.operating", "derived.total_debt"}
    ]

    row = m08._row_from_facts(
        ticker="NKE", name="NIKE, Inc.", is_subject=True, facts=facts
    )

    assert row.revenue is not None
    assert row.operating_margin is None
    assert row.ev_to_ebitda is None  # missing debt makes EV unsound, not zero


def test_peer_comparison_facts_flattens_every_row() -> None:
    row_a = m08._row_from_facts(
        ticker="NKE", name="NIKE, Inc.", is_subject=True, facts=_subject_facts()
    )
    row_b = PeerComparisonRow(ticker="ADS", name="adidas AG", is_subject=False)

    comparison = PeerComparison(rows=(row_a, row_b))

    assert len(comparison.facts) == len(row_a.facts)


# --- Orchestration: build_peer_comparison --------------------------------


class _DummyClient:
    """Never actually used: every network-touching call is monkeypatched."""


def _company(ticker: str, cik: str) -> Company:
    return make_company(cik=cik, ticker=ticker)


@pytest.fixture
def patched_pipeline(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Fakes m01/m02/m03/m05/m07 so build_peer_comparison never hits a network.

    Every peer except one named "FAIL" resolves and reports one revenue fact;
    "FAIL" cannot even build a manifest. Returns the call log so a test can
    assert how many peers were actually attempted.
    """
    calls: dict[str, list[str]] = {"manifest": []}

    async def fake_load_company(
        client: object, cik: str, ticker: str | None = None
    ) -> Company:
        return _company(ticker or cik, cik)

    async def fake_build_manifest(
        company: Company, client: object, *, include_exhibits: bool = True
    ) -> list[object]:
        calls["manifest"].append(company.ticker)
        if company.ticker == "FAIL":
            raise m02_discovery.DiscoveryError("no filings for this peer")
        return []

    async def fake_extract_financials(
        company: Company, manifest: list[object]
    ) -> list[object]:
        return [
            make_fact(
                metric="income.revenue",
                value=500.0,
                display_value="500",
                period_end=PERIOD_END,
                fiscal_year=2024,
                accession_no=f"{company.ticker}-{APPLE_ACCESSION}",
            )
        ]

    def fake_compute_derived_metrics(
        facts: list[object], *, sic_code: str | None = None
    ) -> list[object]:
        return [
            make_fact(
                metric="margin.net",
                value=8.0,
                display_value="8.0%",
                unit="percent",
                period_end=PERIOD_END,
                fiscal_year=2024,
                is_calculated=True,
                formula="net income FY2024 ÷ revenue FY2024",
            )
        ]

    async def fake_fetch_market_data(company: Company) -> list[object]:
        return [_market_fact(accession_no=f"{company.ticker}-YAHOO")]

    monkeypatch.setattr(m08, "load_company", fake_load_company)
    monkeypatch.setattr(m02_discovery, "build_manifest", fake_build_manifest)
    monkeypatch.setattr(m03_financials, "extract_financials", fake_extract_financials)
    monkeypatch.setattr(m08, "compute_derived_metrics", fake_compute_derived_metrics)
    monkeypatch.setattr(m05_market, "fetch_market_data", fake_fetch_market_data)

    return calls


async def test_build_peer_comparison_includes_the_subject_first(
    patched_pipeline: dict[str, list[str]],
) -> None:
    subject = _company("NKE", "0000320187")
    peer_set = PeerSet(
        subject_cik=subject.cik,
        peers=(_peer(cik="1", ticker="ADS", name="adidas AG"),),
    )

    result = await m08.build_peer_comparison(
        subject, peer_set, _subject_facts(), cast(EdgarClient, _DummyClient())
    )

    assert result.rows[0].ticker == "NKE"
    assert result.rows[0].is_subject is True
    assert len(result.rows) == 2
    assert result.rows[1].ticker == "ADS"
    assert result.rows[1].is_subject is False
    assert not result.notes


async def test_build_peer_comparison_degrades_one_peer_without_losing_the_rest(
    patched_pipeline: dict[str, list[str]],
) -> None:
    subject = _company("NKE", "0000320187")
    peer_set = PeerSet(
        subject_cik=subject.cik,
        peers=(
            _peer(cik="1", ticker="ADS", name="adidas AG"),
            _peer(cik="2", ticker="FAIL", name="Unreadable Filer Inc."),
        ),
    )

    result = await m08.build_peer_comparison(
        subject, peer_set, _subject_facts(), cast(EdgarClient, _DummyClient())
    )

    tickers = {row.ticker for row in result.rows}
    assert tickers == {"NKE", "ADS"}
    assert len(result.notes) == 1
    assert "FAIL" in result.notes[0]


async def test_build_peer_comparison_respects_max_peers(
    patched_pipeline: dict[str, list[str]],
) -> None:
    subject = _company("NKE", "0000320187")
    peer_set = PeerSet(
        subject_cik=subject.cik,
        peers=tuple(
            _peer(cik=str(n), ticker=f"P{n}", name=f"Peer {n}") for n in range(4)
        ),
    )

    await m08.build_peer_comparison(
        subject,
        peer_set,
        _subject_facts(),
        cast(EdgarClient, _DummyClient()),
        max_peers=2,
    )

    assert patched_pipeline["manifest"] == ["P0", "P1"]


def test_peer_comparison_max_count_is_smaller_than_the_selection_ladder() -> None:
    """A comparison costs a full extraction per name; the ladder just a mention."""
    from app.config import PEER_MAX_COUNT

    assert PEER_COMPARISON_MAX_COUNT < PEER_MAX_COUNT


# --- The rest of the valuation set ------------------------------------------
# Enterprise value, EV/sales and dividend yield joined P/E and EV/EBITDA so a
# reader can see what the market pays and what it pays back, not only what it
# pays per unit of earnings. All five share one definition of enterprise
# value, which is the point of `_enterprise_value_of`.


def test_enterprise_value_is_market_cap_plus_debt_less_cash() -> None:
    latest = m08._latest_by_metric(_subject_facts())

    fact = m08._enterprise_value(latest, "NKE")

    # 50,000 market cap + 300 debt - 50 cash
    assert fact is not None
    assert fact.value == pytest.approx(50_250.0)
    assert fact.tier == SourceTier.MARKET
    assert fact.is_calculated is True
    # A count of currency, not a multiple: it must not render as "50250.00x".
    assert not fact.display_value.endswith("x")


def test_ev_to_sales_divides_enterprise_value_by_revenue() -> None:
    latest = m08._latest_by_metric(_subject_facts())

    fact = m08._ev_to_sales(latest, "NKE")

    # 50,250 enterprise value / 1,000 revenue
    assert fact is not None
    assert fact.value == pytest.approx(50.25)
    assert fact.display_value.endswith("x")
    assert "revenue" in (fact.formula or "")


@pytest.mark.parametrize(
    "missing_metric",
    ["derived.total_debt", "balance.cash_and_equivalents", "income.revenue"],
)
def test_ev_to_sales_is_none_without_every_input(missing_metric: str) -> None:
    """An undisclosed debt balance is not zero, here or anywhere else."""
    facts = [f for f in _subject_facts() if f.metric != missing_metric]
    latest = m08._latest_by_metric(facts)

    assert m08._ev_to_sales(latest, "NKE") is None


def test_dividend_yield_is_none_when_the_filer_reports_no_dividend_line() -> None:
    # The fixture has no dividends paid. Absent is absent — never inferred as
    # zero, which would render a 0.0% yield the filing never stated.
    latest = m08._latest_by_metric(_subject_facts())

    assert m08._dividend_yield(latest, "NKE") is None


def test_dividend_yield_is_a_percentage_of_market_capitalisation() -> None:
    facts = [
        *_subject_facts(),
        make_fact(
            metric="cashflow.dividends_paid",
            label="Dividends paid",
            value=2_500.0,
            display_value="2,500",
            period_end=PERIOD_END,
            fiscal_year=2024,
        ),
    ]
    latest = m08._latest_by_metric(facts)

    fact = m08._dividend_yield(latest, "NKE")

    # 2,500 paid / 50,000 market cap
    assert fact is not None
    assert fact.value == pytest.approx(5.0)
    assert fact.unit == "percent"
    assert "trailing" in (fact.formula or "")


def test_dividend_yield_uses_the_magnitude_of_an_outflow() -> None:
    # A filer tagging the raw negative would otherwise produce a negative
    # yield, which is not what a yield means.
    facts = [
        *_subject_facts(),
        make_fact(
            metric="cashflow.dividends_paid",
            label="Dividends paid",
            value=-2_500.0,
            display_value="(2,500)",
            period_end=PERIOD_END,
            fiscal_year=2024,
        ),
    ]
    latest = m08._latest_by_metric(facts)

    fact = m08._dividend_yield(latest, "NKE")

    assert fact is not None
    assert fact.value == pytest.approx(5.0)
