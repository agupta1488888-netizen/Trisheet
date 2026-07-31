"""Tests for m07 analysis: the arithmetic, its guards and its provenance.

Every expected figure here is hand-computed from the fixtures below and
written out as a literal. Nothing asserts a value by recomputing it with the
same expression the module uses, which would only prove the code agrees with
itself.

The general fixture is a geometric revenue series growing at exactly 25% a
year, with FY2024 and FY2025 fully populated. That makes every growth rate and
compound rate exactly 25.0, and every ratio a figure that can be checked
mentally against the balances.
"""

from __future__ import annotations

import datetime as dt
import random

import pytest

from app.config import CONFIDENCE_EXACT, SectorTemplate
from app.models import ExtractionMethod, Fact, SourceTier, SourceType
from app.modules.m07_analysis import (
    SECTOR_METRIC_GROUPS,
    AnalysisResult,
    MetricGroup,
    analyse,
    compute_derived_metrics,
    fact_id,
)

GEOGRAPHIC_AXIS = "srt:StatementGeographicalAxis"
BUSINESS_AXIS = "us-gaap:StatementBusinessSegmentsAxis"

#: Start and end of each fiscal year. A September year end is used so that a
#: fiscal year never equals the calendar year of its end date, which would let
#: a bug that reads the calendar year pass unnoticed.
_FISCAL_PERIODS: dict[int, tuple[str, str]] = {
    2020: ("2019-10-01", "2020-09-30"),
    2021: ("2020-10-01", "2021-09-30"),
    2022: ("2021-10-01", "2022-09-30"),
    2023: ("2022-10-01", "2023-09-30"),
    2024: ("2023-10-01", "2024-09-30"),
    2025: ("2024-10-01", "2025-09-30"),
}


# --- Fixture construction ---------------------------------------------------


def _date(raw: str) -> dt.date:
    return dt.date.fromisoformat(raw)


def _filed_for(year: int) -> dt.date:
    """A plausible filing date: six weeks after the year end."""
    return _date(_FISCAL_PERIODS[year][1]) + dt.timedelta(days=42)


def _accession_for(year: int) -> str:
    return f"0000320193-{year % 100:02d}-000001"


def _fact(
    metric: str,
    year: int,
    value: float,
    *,
    instant: bool,
    label: str | None = None,
    unit: str = "USD",
    segment_axis: str | None = None,
    segment_member: str | None = None,
    segment_label: str | None = None,
    tier: SourceTier = SourceTier.FILING,
    confidence: float = CONFIDENCE_EXACT,
    filed: dt.date | None = None,
    accession: str | None = None,
) -> Fact:
    """One reported figure for one fiscal year."""
    start, end = _FISCAL_PERIODS[year]
    return Fact(
        metric=metric,
        label=label or metric.rsplit(".", 1)[-1].replace("_", " ").capitalize(),
        value=value,
        display_value=f"{value:,.2f}",
        unit=unit,
        period_start=None if instant else _date(start),
        period_end=_date(end),
        fiscal_year=None if segment_member is not None else year,
        segment_axis=segment_axis,
        segment_member=segment_member,
        segment_label=segment_label,
        tier=tier,
        source_type=SourceType.SEC_XBRL,
        source_url="https://www.sec.gov/Archives/edgar/data/320193/x.htm",
        accession_no=accession or _accession_for(year),
        filed_date=filed or _filed_for(year),
        extraction_method=ExtractionMethod.XBRL_COMPANY_FACTS,
        confidence=confidence,
    )


def _flow(metric: str, year: int, value: float, **kwargs: object) -> Fact:
    return _fact(metric, year, value, instant=False, **kwargs)  # type: ignore[arg-type]


def _stock(metric: str, year: int, value: float, **kwargs: object) -> Fact:
    return _fact(metric, year, value, instant=True, **kwargs)  # type: ignore[arg-type]


#: Revenue compounding at exactly 25% a year, so that every year-on-year rate
#: and every compound rate over any window is exactly 25.0.
_REVENUE_BY_YEAR = {
    2020: 40_960.0,
    2021: 51_200.0,
    2022: 64_000.0,
    2023: 80_000.0,
    2024: 100_000.0,
    2025: 125_000.0,
}


def _general_facts() -> list[Fact]:
    """A complete non-financial filer, FY2024 and FY2025 fully populated."""
    facts = [
        _flow("income.revenue", year, value, label="Revenue")
        for year, value in _REVENUE_BY_YEAR.items()
    ]

    income = {
        2024: {
            "income.cost_of_revenue": 62_000.0,
            "income.gross_profit": 38_000.0,
            "income.operating_expenses": 26_000.0,
            "income.operating_income": 12_000.0,
            "income.depreciation_amortisation": 4_000.0,
            "income.interest_expense": 1_800.0,
            "income.pretax_income": 10_200.0,
            "income.income_tax_expense": 2_550.0,
            "income.net_income": 7_650.0,
            "income.shares_basic": 9_500.0,
            "income.shares_diluted": 10_500.0,
        },
        2025: {
            "income.cost_of_revenue": 75_000.0,
            "income.gross_profit": 50_000.0,
            "income.operating_expenses": 30_000.0,
            "income.operating_income": 20_000.0,
            "income.depreciation_amortisation": 5_000.0,
            "income.interest_expense": 2_000.0,
            "income.pretax_income": 18_000.0,
            "income.income_tax_expense": 4_500.0,
            "income.net_income": 13_500.0,
            "income.shares_basic": 9_000.0,
            "income.shares_diluted": 10_000.0,
        },
    }
    balance = {
        2024: {
            "balance.cash_and_equivalents": 8_000.0,
            "balance.accounts_receivable": 16_000.0,
            "balance.inventory": 12_000.0,
            "balance.current_assets": 40_000.0,
            "balance.total_assets": 130_000.0,
            "balance.accounts_payable": 10_000.0,
            "balance.current_liabilities": 20_000.0,
            "balance.short_term_debt": 4_000.0,
            "balance.long_term_debt": 36_000.0,
            "balance.total_liabilities": 80_000.0,
            "balance.total_equity": 50_000.0,
            "balance.shares_outstanding": 9_500.0,
        },
        2025: {
            "balance.cash_and_equivalents": 10_000.0,
            "balance.accounts_receivable": 20_000.0,
            "balance.inventory": 15_000.0,
            "balance.current_assets": 50_000.0,
            "balance.total_assets": 150_000.0,
            "balance.accounts_payable": 12_000.0,
            "balance.current_liabilities": 25_000.0,
            "balance.short_term_debt": 5_000.0,
            "balance.long_term_debt": 45_000.0,
            "balance.total_liabilities": 90_000.0,
            "balance.total_equity": 60_000.0,
            "balance.shares_outstanding": 9_000.0,
        },
    }
    cash_flow = {
        2024: {
            "cashflow.operating": 18_000.0,
            "cashflow.capital_expenditure": 6_000.0,
            "cashflow.dividends_paid": 3_000.0,
            "cashflow.share_repurchases": 2_000.0,
        },
        2025: {
            "cashflow.operating": 25_000.0,
            "cashflow.capital_expenditure": 8_000.0,
            "cashflow.dividends_paid": 4_500.0,
            "cashflow.share_repurchases": 3_000.0,
        },
    }

    for year, lines in income.items():
        facts.extend(_flow(metric, year, value) for metric, value in lines.items())
    for year, lines in balance.items():
        facts.extend(_stock(metric, year, value) for metric, value in lines.items())
    for year, lines in cash_flow.items():
        facts.extend(_flow(metric, year, value) for metric, value in lines.items())

    return facts


def _segment_facts(
    axis: str = GEOGRAPHIC_AXIS, *, include_asia: bool = True
) -> list[Fact]:
    """Revenue by geography for FY2024 and FY2025, summing to the total."""
    breakdown = {
        "Americas": {2024: 55_000.0, 2025: 70_000.0},
        "Europe": {2024: 30_000.0, 2025: 35_000.0},
        "Asia": {2024: 15_000.0, 2025: 20_000.0},
    }
    if not include_asia:
        del breakdown["Asia"]

    return [
        _flow(
            "segment.revenue",
            year,
            value,
            label="Revenue by segment",
            segment_axis=axis,
            segment_member=f"ex:{name}Member",
            segment_label=name,
        )
        for name, by_year in breakdown.items()
        for year, value in by_year.items()
    ]


# --- Result helpers ---------------------------------------------------------


def _find(result: AnalysisResult, metric: str, year: int | None = None) -> Fact:
    """The single derived fact for a metric, failing loudly when absent."""
    matches = [
        fact
        for fact in result.facts
        if fact.metric == metric and (year is None or fact.fiscal_year == year)
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one {metric!r} for {year}, found {len(matches)}"
        )
    return matches[0]


def _value_of(result: AnalysisResult, metric: str, year: int | None = None) -> float:
    value = _find(result, metric, year).value
    assert value is not None
    return value


def _has(result: AnalysisResult, metric: str, year: int | None = None) -> bool:
    return any(
        fact.metric == metric and (year is None or fact.fiscal_year == year)
        for fact in result.facts
    )


def _metrics(result: AnalysisResult) -> set[str]:
    return {fact.metric for fact in result.facts}


# --- Growth -----------------------------------------------------------------


def test_year_on_year_growth_is_the_change_over_the_prior_year() -> None:
    result = analyse(_general_facts())

    # Revenue compounds at exactly 25% a year across the whole series.
    for year in (2021, 2022, 2023, 2024, 2025):
        assert _value_of(result, "growth.income.revenue.yoy", year) == 25.0


def test_compound_growth_uses_the_endpoints_of_its_window() -> None:
    result = analyse(_general_facts())

    # FY2022 64,000 -> FY2025 125,000 is 1.25^3; FY2020 40,960 is 1.25^5.
    assert _value_of(result, "growth.income.revenue.cagr_3y", 2025) == 25.0
    assert _value_of(result, "growth.income.revenue.cagr_5y", 2025) == 25.0


def test_growth_is_computed_for_every_configured_metric_that_has_two_years() -> None:
    result = analyse(_general_facts())

    # 5,850 / 7,650 = 76.470588...%
    assert _value_of(result, "growth.income.net_income.yoy", 2025) == 76.4706
    # 25,000 / 18,000 - 1 = 38.888...%
    assert _value_of(result, "growth.cashflow.operating.yoy", 2025) == 38.8889


def test_growth_from_a_loss_is_not_reported_as_a_percentage() -> None:
    """A base at or below zero has no meaningful growth rate, so none is given."""
    facts = [
        _flow("income.revenue", 2024, 100_000.0),
        _flow("income.revenue", 2025, 125_000.0),
        _flow("income.net_income", 2024, -5_000.0),
        _flow("income.net_income", 2025, 10_000.0),
    ]
    result = analyse(facts)

    assert _has(result, "growth.income.revenue.yoy", 2025)
    assert not _has(result, "growth.income.net_income.yoy", 2025)


def test_compound_growth_is_not_reported_when_an_endpoint_is_not_positive() -> None:
    facts = [
        _flow("income.net_income", 2022, -1_000.0),
        _flow("income.net_income", 2025, 10_000.0),
    ]
    result = analyse(facts)

    assert not _has(result, "growth.income.net_income.cagr_3y", 2025)


# --- Margins ----------------------------------------------------------------


def test_margins_are_each_line_over_revenue() -> None:
    result = analyse(_general_facts())

    assert _value_of(result, "margin.gross", 2025) == 40.0  # 50,000 / 125,000
    assert _value_of(result, "margin.operating", 2025) == 16.0  # 20,000 / 125,000
    assert _value_of(result, "margin.pretax", 2025) == 14.4  # 18,000 / 125,000
    assert _value_of(result, "margin.net", 2025) == 10.8  # 13,500 / 125,000
    # EBITDA is operating income plus depreciation: 20,000 + 5,000.
    assert _value_of(result, "derived.ebitda", 2025) == 25_000.0
    assert _value_of(result, "margin.ebitda", 2025) == 20.0


def test_margins_are_not_computed_without_revenue() -> None:
    facts = [_flow("income.gross_profit", 2025, 50_000.0)]
    result = analyse(facts)

    assert not _has(result, "margin.gross", 2025)


# --- Returns ----------------------------------------------------------------


def test_returns_divide_the_flow_by_the_average_of_the_two_year_ends() -> None:
    result = analyse(_general_facts())

    # 13,500 / ((60,000 + 50,000) / 2) = 24.5454...%
    assert _value_of(result, "return.roe", 2025) == 24.5455
    # 13,500 / ((150,000 + 130,000) / 2) = 9.642857...%
    assert _value_of(result, "return.roa", 2025) == 9.6429


def test_return_on_invested_capital_uses_the_filers_own_tax_rate() -> None:
    result = analyse(_general_facts())

    # 4,500 / 18,000 = 25%.
    assert _value_of(result, "return.effective_tax_rate", 2025) == 25.0
    # NOPAT = 20,000 x 0.75.
    assert _value_of(result, "derived.nopat", 2025) == 15_000.0
    # Invested capital = total debt 50,000 + equity 60,000 - cash 10,000.
    assert _value_of(result, "derived.invested_capital", 2025) == 100_000.0
    # FY2024 invested capital is 40,000 + 50,000 - 8,000 = 82,000; average
    # 91,000, so 15,000 / 91,000 = 16.4835...%.
    assert _value_of(result, "return.roic", 2025) == 16.4835


def test_returns_fall_back_to_the_closing_balance_and_say_so() -> None:
    """The first year has no opening balance, so the close is used explicitly."""
    facts = [
        _flow("income.net_income", 2025, 13_500.0),
        _stock("balance.total_equity", 2025, 60_000.0),
    ]
    result = analyse(facts)

    roe = _find(result, "return.roe", 2025)
    assert roe.value == 22.5  # 13,500 / 60,000
    assert "at FY2025 year end" in (roe.formula or "")


# --- DuPont -----------------------------------------------------------------


def test_dupont_factors_multiply_back_to_return_on_equity() -> None:
    result = analyse(_general_facts())

    assert _value_of(result, "dupont.net_margin", 2025) == 0.108
    assert _value_of(result, "dupont.asset_turnover", 2025) == 0.892857
    assert _value_of(result, "dupont.equity_multiplier", 2025) == 2.545455
    # The product is formed from the unrounded factors, so it equals ROE.
    assert _value_of(result, "dupont.roe", 2025) == _value_of(
        result, "return.roe", 2025
    )


# --- Liquidity and leverage -------------------------------------------------


def test_liquidity_ratios_use_the_closing_balance_sheet() -> None:
    result = analyse(_general_facts())

    assert _value_of(result, "liquidity.current_ratio", 2025) == 2.0
    # (50,000 - 15,000) / 25,000.
    assert _value_of(result, "liquidity.quick_ratio", 2025) == 1.4
    assert _value_of(result, "liquidity.cash_ratio", 2025) == 0.4


def test_leverage_ratios_and_interest_coverage() -> None:
    result = analyse(_general_facts())

    # Total debt is both halves: 5,000 + 45,000.
    assert _value_of(result, "derived.total_debt", 2025) == 50_000.0
    assert _value_of(result, "derived.net_debt", 2025) == 40_000.0
    assert _value_of(result, "leverage.debt_to_equity", 2025) == 0.833333
    assert _value_of(result, "leverage.debt_to_assets", 2025) == 0.333333
    assert _value_of(result, "leverage.liabilities_to_equity", 2025) == 1.5
    # Net debt 40,000 over EBITDA 25,000.
    assert _value_of(result, "leverage.net_debt_to_ebitda", 2025) == 1.6
    assert _value_of(result, "leverage.interest_coverage", 2025) == 10.0


def test_total_debt_needs_both_halves_of_the_debt() -> None:
    """Summing only the disclosed half would understate leverage silently."""
    facts = [
        _stock("balance.long_term_debt", 2025, 45_000.0),
        _stock("balance.total_equity", 2025, 60_000.0),
        _stock("balance.total_liabilities", 2025, 90_000.0),
    ]
    result = analyse(facts)

    assert not _has(result, "derived.total_debt", 2025)
    assert not _has(result, "leverage.debt_to_equity", 2025)
    # Leverage against total liabilities is still reported, because total
    # liabilities are always disclosed.
    assert _value_of(result, "leverage.liabilities_to_equity", 2025) == 1.5


# --- Cash flow --------------------------------------------------------------


def test_cash_flow_metrics() -> None:
    result = analyse(_general_facts())

    # 25,000 - 8,000.
    assert _value_of(result, "cashflow.free_cash_flow", 2025) == 17_000.0
    assert _value_of(result, "cashflow.capex_intensity", 2025) == 6.4
    # 17,000 / 13,500 = 125.9259...%
    assert _value_of(result, "cashflow.fcf_conversion", 2025) == 125.9259
    assert _value_of(result, "cashflow.earnings_quality", 2025) == 1.851852


def test_a_derived_currency_figure_displays_in_millions() -> None:
    """Matches the table footnote's promise of "millions" — the stored

    `value` stays the raw figure the arithmetic used; only the rendered
    text is scaled.
    """
    result = analyse(_general_facts())

    fcf = _find(result, "cashflow.free_cash_flow", 2025)
    assert fcf.value == 17_000.0
    assert fcf.display_value == "0.02"


def test_free_cash_flow_to_the_firm_takes_out_the_working_capital_build() -> None:
    result = analyse(_general_facts())

    # NWC FY2025 25,000, FY2024 20,000, so the build is 5,000.
    assert _value_of(result, "derived.net_working_capital", 2025) == 25_000.0
    # 15,000 NOPAT + 5,000 depreciation - 8,000 capex - 5,000 build.
    assert _value_of(result, "cashflow.fcff", 2025) == 7_000.0


# --- Working capital cycle --------------------------------------------------


def test_working_capital_cycle() -> None:
    result = analyse(_general_facts())

    # 20,000 / 125,000 x 365.
    assert _value_of(result, "working_capital.dso", 2025) == 58.4
    # 15,000 / 75,000 x 365.
    assert _value_of(result, "working_capital.dio", 2025) == 73.0
    # 12,000 / 75,000 x 365.
    assert _value_of(result, "working_capital.dpo", 2025) == 58.4
    # 58.4 + 73.0 - 58.4.
    assert (
        _value_of(result, "working_capital.cash_conversion_cycle", 2025) == 73.0
    )


def test_the_cycle_is_not_reported_when_one_of_its_legs_is_missing() -> None:
    facts = [
        _flow("income.revenue", 2025, 125_000.0),
        _flow("income.cost_of_revenue", 2025, 75_000.0),
        _stock("balance.accounts_receivable", 2025, 20_000.0),
        _stock("balance.inventory", 2025, 15_000.0),
    ]
    result = analyse(facts)

    assert _has(result, "working_capital.dso", 2025)
    assert _has(result, "working_capital.dio", 2025)
    assert not _has(result, "working_capital.dpo", 2025)
    assert not _has(result, "working_capital.cash_conversion_cycle", 2025)


# --- Common-size statements -------------------------------------------------


def test_common_size_income_is_each_line_over_revenue() -> None:
    result = analyse(_general_facts())

    assert _value_of(result, "common_size.income.cost_of_revenue", 2025) == 60.0
    assert _value_of(result, "common_size.income.gross_profit", 2025) == 40.0
    assert _value_of(result, "common_size.income.net_income", 2025) == 10.8


def test_common_size_balance_is_each_line_over_total_assets() -> None:
    result = analyse(_general_facts())

    # 10,000 / 150,000 = 6.6667%.
    assert (
        _value_of(result, "common_size.balance.cash_and_equivalents", 2025)
        == 6.6667
    )
    assert _value_of(result, "common_size.balance.total_equity", 2025) == 40.0
    assert (
        _value_of(result, "common_size.balance.total_liabilities", 2025) == 60.0
    )


# --- Margin bridge ----------------------------------------------------------


def test_margin_bridge_components_sum_to_the_margin_change() -> None:
    result = analyse(_general_facts())

    # Gross margin 38.0% -> 40.0%; operating margin 12.0% -> 16.0%; the opex
    # ratio falls from 26.0% to 24.0%, which helps the margin by 2 points.
    assert _value_of(result, "bridge.gross_margin_contribution", 2025) == 2.0
    assert _value_of(result, "bridge.opex_contribution", 2025) == 2.0
    assert _value_of(result, "bridge.operating_margin_change", 2025) == 4.0
    # The identity closes exactly, so there is nothing left to explain.
    assert not _has(result, "bridge.other_contribution", 2025)


def test_margin_bridge_reports_what_the_identity_does_not_explain() -> None:
    """An operating line outside gross profit and opex is named, not absorbed."""
    facts = [
        _flow("income.revenue", 2024, 100_000.0),
        _flow("income.gross_profit", 2024, 38_000.0),
        _flow("income.operating_expenses", 2024, 26_000.0),
        _flow("income.operating_income", 2024, 12_000.0),
        _flow("income.revenue", 2025, 125_000.0),
        _flow("income.gross_profit", 2025, 50_000.0),
        _flow("income.operating_expenses", 2025, 30_000.0),
        # 5,000 of other operating expense sits outside the two components.
        _flow("income.operating_income", 2025, 15_000.0),
    ]
    result = analyse(facts)

    assert _value_of(result, "bridge.gross_margin_contribution", 2025) == 2.0
    assert _value_of(result, "bridge.opex_contribution", 2025) == 2.0
    # Operating margin moves 12.0% -> 12.0%, so nothing net changed.
    assert _value_of(result, "bridge.operating_margin_change", 2025) == 0.0
    assert _value_of(result, "bridge.other_contribution", 2025) == -4.0


def test_margin_bridge_derives_opex_when_it_is_not_separately_tagged() -> None:
    facts = [
        _flow("income.revenue", 2024, 100_000.0),
        _flow("income.gross_profit", 2024, 38_000.0),
        _flow("income.operating_income", 2024, 12_000.0),
        _flow("income.revenue", 2025, 125_000.0),
        _flow("income.gross_profit", 2025, 50_000.0),
        _flow("income.operating_income", 2025, 20_000.0),
    ]
    result = analyse(facts)

    contribution = _find(result, "bridge.opex_contribution", 2025)
    assert contribution.value == 2.0
    assert "gross profit less operating income" in (contribution.formula or "")


# --- Per-share and shareholder returns --------------------------------------


def test_per_share_metrics() -> None:
    result = analyse(_general_facts())

    assert _value_of(result, "per_share.eps_basic", 2025) == 1.5  # 13,500/9,000
    assert _value_of(result, "per_share.eps_diluted", 2025) == 1.35
    # 60,000 / 9,000 = 6.6667, rendered to the cent.
    assert _value_of(result, "per_share.book_value", 2025) == 6.67
    assert _value_of(result, "per_share.dividend", 2025) == 0.5


def test_shareholder_returns() -> None:
    result = analyse(_general_facts())

    assert _value_of(result, "shareholder.payout_ratio", 2025) == 33.3333
    assert _value_of(result, "shareholder.buyback_ratio", 2025) == 22.2222
    # (4,500 + 3,000) / 13,500.
    assert _value_of(result, "shareholder.total_payout_ratio", 2025) == 55.5556


def test_share_count_change_is_negative_when_the_count_shrinks() -> None:
    result = analyse(_general_facts())

    # 10,500 diluted shares fall to 10,000.
    assert _value_of(result, "shareholder.share_count_change", 2025) == -4.7619


def test_per_share_figures_carry_a_per_share_unit() -> None:
    result = analyse(_general_facts())

    assert _find(result, "per_share.book_value", 2025).unit == "USD/share"


# --- Growth attribution -----------------------------------------------------


GEOGRAPHY_PREFIX = "attribution.geography.geographical"
BUSINESS_PREFIX = "attribution.segment.business_segments"


def test_segment_contributions_are_points_of_prior_year_revenue() -> None:
    result = analyse(_general_facts() + _segment_facts())

    # Americas grew 55,000 -> 70,000 against a 100,000 base.
    assert _value_of(result, f"{GEOGRAPHY_PREFIX}.americas.revenue") == 15.0
    assert _value_of(result, f"{GEOGRAPHY_PREFIX}.europe.revenue") == 5.0
    assert _value_of(result, f"{GEOGRAPHY_PREFIX}.asia.revenue") == 5.0


def test_segment_contributions_add_up_to_the_consolidated_growth_rate() -> None:
    result = analyse(_general_facts() + _segment_facts())

    contributions = sum(
        fact.value or 0.0
        for fact in result.facts
        if fact.metric.startswith("attribution.")
    )
    assert contributions == _value_of(result, "growth.income.revenue.yoy", 2025)
    assert not _has(result, f"{GEOGRAPHY_PREFIX}.unattributed.revenue")


def test_revenue_growth_no_segment_explains_is_reported_as_a_residual() -> None:
    result = analyse(_general_facts() + _segment_facts(include_asia=False))

    # Americas 15 points and Europe 5 points leave 5 of the 25 unexplained.
    assert _value_of(result, f"{GEOGRAPHY_PREFIX}.unattributed.revenue") == 5.0


def test_a_business_axis_is_attributed_separately_from_a_geographic_one() -> None:
    result = analyse(_general_facts() + _segment_facts(axis=BUSINESS_AXIS))

    assert _has(result, f"{BUSINESS_PREFIX}.americas.revenue")
    assert not _has(result, f"{GEOGRAPHY_PREFIX}.americas.revenue")


def test_two_breakdowns_of_the_same_revenue_are_attributed_independently() -> None:
    """Adding a regional contribution to a product one counts revenue twice.

    A filer commonly reports the same revenue along two axes, each accounting
    for all of it. Each breakdown must sum to the growth rate on its own, and
    neither may leak a residual caused by the other.
    """
    facts = (
        _general_facts()
        + _segment_facts()
        + _segment_facts(axis=BUSINESS_AXIS, include_asia=False)
    )
    result = analyse(facts)

    growth = _value_of(result, "growth.income.revenue.yoy", 2025)

    geography = sum(
        fact.value or 0.0
        for fact in result.facts
        if fact.metric.startswith(GEOGRAPHY_PREFIX)
    )
    business = sum(
        fact.value or 0.0
        for fact in result.facts
        if fact.metric.startswith(BUSINESS_PREFIX)
    )

    # The geographic breakdown is complete; the business one is missing Asia
    # and carries its own residual, which brings it back to the same total.
    assert geography == growth
    assert business == growth
    assert not _has(result, f"{GEOGRAPHY_PREFIX}.unattributed.revenue")
    assert _value_of(result, f"{BUSINESS_PREFIX}.unattributed.revenue") == 5.0


def test_members_of_different_axes_never_collide_on_a_metric_name() -> None:
    facts = (
        _general_facts()
        + _segment_facts()
        + _segment_facts(axis=BUSINESS_AXIS)
    )
    result = analyse(facts)

    attribution = [
        fact.metric
        for fact in result.facts
        if fact.metric.startswith("attribution.")
    ]
    assert len(attribution) == len(set(attribution))
    # Americas appears under both breakdowns, under two different paths.
    assert f"{GEOGRAPHY_PREFIX}.americas.revenue" in attribution
    assert f"{BUSINESS_PREFIX}.americas.revenue" in attribution


def test_attribution_needs_two_comparable_periods() -> None:
    single_period = [
        fact for fact in _segment_facts() if fact.fiscal_year is None
        and fact.period_end == _date("2025-09-30")
    ]
    result = analyse(_general_facts() + single_period)

    assert not any(
        fact.metric.startswith("attribution.") for fact in result.facts
    )


# --- Trailing twelve months -------------------------------------------------


def _quarter(start: str, end: str, value: float, *, filed: str) -> Fact:
    """One interim period of revenue, as a 10-Q reports it."""
    return Fact(
        metric="income.revenue",
        label="Revenue",
        value=value,
        display_value=f"{value:,.2f}",
        unit="USD",
        period_start=_date(start),
        period_end=_date(end),
        fiscal_year=None,
        tier=SourceTier.FILING,
        source_type=SourceType.SEC_XBRL,
        source_url="https://www.sec.gov/Archives/edgar/data/320193/x.htm",
        accession_no="0000320193-26-000009",
        filed_date=_date(filed),
        extraction_method=ExtractionMethod.XBRL_COMPANY_FACTS,
        confidence=CONFIDENCE_EXACT,
    )


def test_trailing_twelve_months_sums_the_latest_four_quarters() -> None:
    facts = [
        _quarter("2025-01-01", "2025-03-31", 28_000.0, filed="2025-05-01"),
        _quarter("2025-04-01", "2025-06-30", 30_000.0, filed="2025-08-01"),
        _quarter("2025-07-01", "2025-09-30", 32_000.0, filed="2025-11-01"),
        _quarter("2025-10-01", "2025-12-31", 35_000.0, filed="2026-02-01"),
    ]
    result = analyse(facts)

    ttm = _find(result, "ttm.income.revenue")
    assert ttm.value == 125_000.0
    assert ttm.period_start == _date("2025-01-01")
    assert ttm.period_end == _date("2025-12-31")


def test_trailing_twelve_months_falls_back_to_year_plus_year_to_date() -> None:
    facts = [
        _quarter("2024-01-01", "2024-12-31", 100_000.0, filed="2025-02-01"),
        _quarter("2024-01-01", "2024-09-30", 74_000.0, filed="2024-11-01"),
        _quarter("2025-01-01", "2025-09-30", 82_000.0, filed="2025-11-01"),
    ]
    result = analyse(facts)

    ttm = _find(result, "ttm.income.revenue")
    # 100,000 + 82,000 - 74,000.
    assert ttm.value == 108_000.0
    # Taking Jan-Sep 2024 out of calendar 2024 leaves Oct-Dec 2024, and adding
    # Jan-Sep 2025 makes the window October 2024 to September 2025.
    assert ttm.period_start == _date("2024-10-01")
    assert ttm.period_end == _date("2025-09-30")


def test_trailing_twelve_months_rejects_a_gap_in_the_quarters() -> None:
    """Three quarters and a stale one is not twelve months of trading."""
    facts = [
        _quarter("2024-01-01", "2024-03-31", 20_000.0, filed="2024-05-01"),
        _quarter("2025-04-01", "2025-06-30", 30_000.0, filed="2025-08-01"),
        _quarter("2025-07-01", "2025-09-30", 32_000.0, filed="2025-11-01"),
        _quarter("2025-10-01", "2025-12-31", 35_000.0, filed="2026-02-01"),
    ]
    result = analyse(facts)

    assert not _has(result, "ttm.income.revenue")


def test_trailing_twelve_months_is_not_built_from_annual_periods_alone() -> None:
    result = analyse(_general_facts())

    assert not _has(result, "ttm.income.revenue")


# --- Sector templates -------------------------------------------------------


def test_sic_code_selects_the_metric_set() -> None:
    facts = _general_facts()

    assert analyse(facts).template is SectorTemplate.GENERAL
    assert analyse(facts, sic_code="6022").template is SectorTemplate.BANK
    assert analyse(facts, sic_code="6798").template is SectorTemplate.REIT
    assert analyse(facts, sic_code="6331").template is SectorTemplate.INSURANCE
    # A bank holding company files under 6712.
    assert analyse(facts, sic_code="6712").template is SectorTemplate.BANK
    # An unrecognised or absent code is never guessed at.
    assert analyse(facts, sic_code="7372").template is SectorTemplate.GENERAL
    assert analyse(facts, sic_code=None).template is SectorTemplate.GENERAL
    assert analyse(facts, sic_code="not a code").template is SectorTemplate.GENERAL


def test_a_bank_does_not_get_metrics_its_balance_sheet_cannot_support() -> None:
    """No classified balance sheet means no current ratio and no cycle."""
    result = analyse(_general_facts(), sic_code="6022")
    metrics = _metrics(result)

    assert not any(metric.startswith("liquidity.") for metric in metrics)
    assert not any(metric.startswith("working_capital.") for metric in metrics)
    assert not any(metric.startswith("bridge.") for metric in metrics)
    assert "cashflow.free_cash_flow" not in metrics
    # What every filer gets is still there.
    assert "return.roe" in metrics
    assert "growth.income.revenue.yoy" in metrics


def test_bank_metrics() -> None:
    facts = _general_facts() + [
        _flow("bank.net_interest_income", 2025, 4_000.0),
        _flow("bank.noninterest_income", 2025, 2_000.0),
        _flow("bank.noninterest_expense", 2025, 3_000.0),
        _flow("bank.provision_for_credit_losses", 2025, 750.0),
        _stock("bank.interest_earning_assets", 2024, 90_000.0),
        _stock("bank.interest_earning_assets", 2025, 110_000.0),
        _stock("bank.total_loans", 2024, 70_000.0),
        _stock("bank.total_loans", 2025, 80_000.0),
        _stock("bank.total_deposits", 2025, 100_000.0),
        _stock("bank.cet1_capital", 2025, 15_000.0),
        _stock("bank.risk_weighted_assets", 2025, 125_000.0),
    ]
    result = analyse(facts, sic_code="6022")

    # 4,000 over average earning assets of 100,000.
    assert _value_of(result, "bank.net_interest_margin", 2025) == 4.0
    assert _value_of(result, "bank.cet1_ratio", 2025) == 12.0
    # 3,000 / (4,000 + 2,000).
    assert _value_of(result, "bank.efficiency_ratio", 2025) == 50.0
    assert _value_of(result, "bank.loan_to_deposit", 2025) == 80.0
    # 750 over average loans of 75,000.
    assert _value_of(result, "bank.provision_to_loans", 2025) == 1.0


def test_reit_funds_from_operations_and_what_follows_from_it() -> None:
    facts = _general_facts() + [
        _flow("reit.real_estate_depreciation", 2025, 30_000.0),
        _flow("reit.gains_on_property_sales", 2025, 2_000.0),
        _flow("reit.recurring_capex", 2025, 5_000.0),
        _flow("reit.straight_line_rent", 2025, 1_000.0),
        _stock("reit.real_estate_fair_value", 2025, 500_000.0),
    ]
    result = analyse(facts, sic_code="6798")

    # 13,500 net income + 30,000 depreciation - 2,000 gains.
    assert _value_of(result, "reit.ffo", 2025) == 41_500.0
    # 41,500 - 5,000 recurring capex - 1,000 straight-line rent.
    assert _value_of(result, "reit.affo", 2025) == 35_500.0
    assert _value_of(result, "reit.ffo_per_share", 2025) == 4.15  # over 10,000
    assert _value_of(result, "reit.affo_per_share", 2025) == 3.55
    # 4,500 dividends over 41,500 of FFO.
    assert _value_of(result, "reit.ffo_payout_ratio", 2025) == 10.8434
    # (500,000 - 90,000) / 9,000 shares.
    assert _value_of(result, "reit.nav_per_share", 2025) == 45.56


def test_reit_names_the_depreciation_it_used_when_real_estate_is_absent() -> None:
    facts = _general_facts()
    result = analyse(facts, sic_code="6798")

    ffo = _find(result, "reit.ffo", 2025)
    # 13,500 net income + 5,000 total depreciation.
    assert ffo.value == 18_500.0
    assert "real estate depreciation not separately tagged" in (ffo.formula or "")


def test_net_asset_value_is_not_computed_without_a_disclosed_fair_value() -> None:
    result = analyse(_general_facts(), sic_code="6798")

    assert not _has(result, "reit.nav_per_share", 2025)


def test_insurance_combined_ratio_is_the_sum_of_its_two_halves() -> None:
    facts = _general_facts() + [
        _flow("insurance.earned_premiums", 2025, 100_000.0),
        _flow("insurance.losses_incurred", 2025, 65_000.0),
        _flow("insurance.underwriting_expenses", 2025, 28_000.0),
    ]
    result = analyse(facts, sic_code="6331")

    assert _value_of(result, "insurance.loss_ratio", 2025) == 65.0
    assert _value_of(result, "insurance.expense_ratio", 2025) == 28.0
    assert _value_of(result, "insurance.combined_ratio", 2025) == 93.0


def test_every_template_defines_a_metric_group_set() -> None:
    for template in SectorTemplate:
        assert SECTOR_METRIC_GROUPS[template]

    assert MetricGroup.BANK in SECTOR_METRIC_GROUPS[SectorTemplate.BANK]
    assert MetricGroup.REIT in SECTOR_METRIC_GROUPS[SectorTemplate.REIT]
    assert (
        MetricGroup.INSURANCE in SECTOR_METRIC_GROUPS[SectorTemplate.INSURANCE]
    )
    assert MetricGroup.BANK not in SECTOR_METRIC_GROUPS[SectorTemplate.GENERAL]


# --- Panel construction -----------------------------------------------------


def test_the_latest_filed_figure_wins_a_restatement() -> None:
    original = _flow(
        "income.revenue",
        2025,
        120_000.0,
        filed=_date("2025-11-11"),
        accession="0000320193-25-000001",
    )
    restated = _flow(
        "income.revenue",
        2025,
        125_000.0,
        filed=_date("2026-02-02"),
        accession="0000320193-26-000004",
    )
    prior = _flow("income.revenue", 2024, 100_000.0)

    result = analyse([original, restated, prior])

    assert _value_of(result, "growth.income.revenue.yoy", 2025) == 25.0


def test_a_same_day_tie_is_broken_deterministically_by_accession() -> None:
    filed = _date("2026-02-02")
    low = _flow(
        "income.revenue", 2025, 120_000.0, filed=filed, accession="0000320193-26-000001"
    )
    high = _flow(
        "income.revenue", 2025, 125_000.0, filed=filed, accession="0000320193-26-000009"
    )
    prior = _flow("income.revenue", 2024, 100_000.0)

    forwards = analyse([low, high, prior])
    backwards = analyse([high, low, prior])

    assert _value_of(forwards, "growth.income.revenue.yoy", 2025) == 25.0
    assert forwards.facts == backwards.facts


def test_a_fact_without_a_fiscal_year_is_not_placed_in_a_year() -> None:
    """A year the filer did not give is a year this system does not invent."""
    unlabelled = _flow("income.revenue", 2025, 125_000.0).model_copy(
        update={"fiscal_year": None}
    )
    result = analyse([unlabelled, _flow("income.revenue", 2024, 100_000.0)])

    assert not _has(result, "growth.income.revenue.yoy", 2025)


def test_segment_facts_do_not_enter_the_consolidated_panel() -> None:
    """Summing segments into the panel would double-count the total."""
    result = analyse(_general_facts() + _segment_facts())

    assert _value_of(result, "margin.gross", 2025) == 40.0
    assert not _has(result, "common_size.segment.revenue", 2025)


def test_a_quarterly_fact_never_displaces_the_annual_one() -> None:
    facts = _general_facts() + [
        _quarter("2025-07-01", "2025-09-30", 32_000.0, filed="2026-06-01")
    ]
    result = analyse(facts)

    # The annual 125,000 still drives the margin, not the 32,000 quarter.
    assert _value_of(result, "margin.gross", 2025) == 40.0


# --- Provenance and traceability --------------------------------------------


def test_every_derived_fact_is_labelled_calculated_and_carries_its_formula() -> None:
    result = analyse(_general_facts() + _segment_facts())

    assert result.facts
    for fact in result.facts:
        assert fact.is_calculated is True
        assert fact.extraction_method is ExtractionMethod.CALCULATED
        assert (fact.formula or "").strip()
        assert fact.display_value.strip()


def test_a_derived_fact_records_the_ids_of_everything_it_consumed() -> None:
    facts = _general_facts()
    result = analyse(facts)

    roe = _find(result, "return.roe", 2025)
    derivation = result.derivation_for(fact_id(roe))
    assert derivation is not None

    expected = {
        fact_id(fact)
        for fact in facts
        if (fact.metric == "income.net_income" and fact.fiscal_year == 2025)
        or (fact.metric == "balance.total_equity" and fact.fiscal_year in (2024, 2025))
    }
    assert set(derivation.inputs) == expected
    assert derivation.metric == "return.roe"


def test_every_derived_fact_has_a_derivation_and_every_input_is_a_real_fact() -> None:
    facts = _general_facts() + _segment_facts()
    result = analyse(facts)
    known = {fact_id(fact) for fact in facts}

    assert len(result.derivations) == len(result.facts)
    for fact, derivation in zip(result.facts, result.derivations, strict=True):
        assert derivation.fact_id == fact_id(fact)
        assert derivation.inputs
        # Inputs are either reported facts or figures derived from them.
        assert set(derivation.inputs) <= known


def test_fact_ids_are_stable_across_calls_and_distinguish_facts() -> None:
    facts = _general_facts()
    first = {fact_id(fact) for fact in facts}
    second = {fact_id(fact) for fact in facts}

    assert first == second
    # Nothing collides: every fixture fact is distinct.
    assert len(first) == len(facts)


def test_a_derived_fact_inherits_the_weakest_tier_of_its_inputs() -> None:
    """A figure is only as well sourced as the worst thing that went into it."""
    facts = [
        _flow("income.revenue", 2025, 125_000.0),
        _flow(
            "income.gross_profit",
            2025,
            50_000.0,
            tier=SourceTier.COMPANY,
            confidence=0.8,
        ),
    ]
    result = analyse(facts)

    margin = _find(result, "margin.gross", 2025)
    assert margin.tier is SourceTier.COMPANY
    assert margin.confidence == 0.8


def test_a_derived_fact_is_dated_by_the_flow_it_measures() -> None:
    result = analyse(_general_facts())

    roe = _find(result, "return.roe", 2025)
    assert roe.period_start == _date("2024-10-01")
    assert roe.period_end == _date("2025-09-30")

    # A ratio of two balances is an instant, not a period.
    current_ratio = _find(result, "liquidity.current_ratio", 2025)
    assert current_ratio.period_start is None
    assert current_ratio.period_end == _date("2025-09-30")


def test_provenance_points_at_the_most_recently_filed_input() -> None:
    result = analyse(_general_facts())

    roe = _find(result, "return.roe", 2025)
    assert roe.accession_no == _accession_for(2025)
    assert roe.filed_date == _filed_for(2025)


# --- Determinism and purity -------------------------------------------------


def test_identical_input_yields_identical_output() -> None:
    facts = _general_facts() + _segment_facts()

    first = analyse(facts)
    second = analyse(facts)

    assert first.facts == second.facts
    assert first.derivations == second.derivations


def test_input_order_does_not_change_the_output() -> None:
    facts = _general_facts() + _segment_facts()
    shuffled = list(facts)
    random.Random(20260729).shuffle(shuffled)

    assert analyse(shuffled).facts == analyse(facts).facts


def test_the_input_list_is_not_mutated() -> None:
    facts = _general_facts()
    before = list(facts)

    compute_derived_metrics(facts)

    assert facts == before


def test_no_two_derived_facts_share_a_metric_and_period() -> None:
    """The facts table is unique on this; a clash would fail the insert."""
    result = analyse(_general_facts() + _segment_facts())

    keys = [
        (fact.metric, fact.period_end, fact.period_start) for fact in result.facts
    ]
    assert len(keys) == len(set(keys))


def test_output_is_sorted_by_metric_then_period() -> None:
    result = analyse(_general_facts())

    keys = [
        (
            fact.metric,
            fact.period_end.isoformat(),
            fact.period_start.isoformat() if fact.period_start else "",
        )
        for fact in result.facts
    ]
    assert keys == sorted(keys)


# --- Degradation ------------------------------------------------------------


def test_no_facts_yields_no_metrics_rather_than_an_error() -> None:
    result = analyse([])

    assert result.facts == ()
    assert result.derivations == ()
    assert result.template is SectorTemplate.GENERAL


def test_a_metric_with_no_value_is_ignored() -> None:
    """A NOT_DISCLOSED marker carries no value and must not become a zero."""
    marker = Fact(
        metric="income.gross_profit",
        label="Gross profit",
        value=None,
        display_value="Not disclosed",
        period_end=_date("2025-09-30"),
        tier=SourceTier.FILING,
        source_type=SourceType.SEC_FILING,
        source_url="https://www.sec.gov/Archives/edgar/data/320193/x.htm",
        accession_no=_accession_for(2025),
        filed_date=_filed_for(2025),
        extraction_method=ExtractionMethod.NOT_DISCLOSED,
        confidence=0.0,
    )
    result = analyse([marker, _flow("income.revenue", 2025, 125_000.0)])

    assert not _has(result, "margin.gross", 2025)


def test_a_zero_denominator_produces_no_metric_rather_than_an_infinity() -> None:
    facts = [
        _flow("income.revenue", 2025, 0.0),
        _flow("income.gross_profit", 2025, 50_000.0),
    ]
    result = analyse(facts)

    assert not _has(result, "margin.gross", 2025)


def test_the_public_interface_returns_plain_facts() -> None:
    facts = _general_facts()

    derived = compute_derived_metrics(facts)

    assert isinstance(derived, list)
    assert derived == list(analyse(facts).facts)


@pytest.mark.parametrize("sic_code", ["6022", "6798", "6331", "7372", None])
def test_a_sparse_filer_never_crashes_whatever_its_sector(
    sic_code: str | None,
) -> None:
    """One revenue figure and nothing else is a small report, not a failure."""
    result = analyse([_flow("income.revenue", 2025, 125_000.0)], sic_code=sic_code)

    assert result.derivations == ()
    assert result.facts == ()
