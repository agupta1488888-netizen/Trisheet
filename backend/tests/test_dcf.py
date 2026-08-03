"""Tests for m07_analysis.project_dcf.

The point of this calculation is that real data and modelling assumptions
never blur together: every test below checks either that a real input is
correctly used and cited, or that an assumption is correctly labelled as one
and never smuggled in as if it were sourced.
"""

from __future__ import annotations

import datetime as dt

from app.config import (
    DCF_DEFAULT_DISCOUNT_RATE,
    DCF_DEFAULT_FCF_GROWTH_RATE,
    DCF_DEFAULT_TERMINAL_GROWTH_RATE,
)
from app.modules.m07_analysis import project_dcf
from tests.conftest import make_fact

FCF_METRIC = "cashflow.free_cash_flow"
NET_DEBT_METRIC = "derived.net_debt"
SHARES_METRIC = "income.shares_diluted"


def _fcf_fact(value: float = 100.0, year: int = 2024) -> object:
    return make_fact(
        metric=FCF_METRIC,
        label="Free cash flow",
        value=value,
        display_value=f"{value:,.0f}",
        fiscal_year=year,
        period_start=None,
        period_end=dt.date(year, 9, 28),
        is_calculated=True,
        formula="net cash from operating activities − capital expenditure",
    )


def test_no_free_cash_flow_is_unavailable_not_a_guess() -> None:
    result = project_dcf([])

    assert result.unavailable_reason is not None
    assert result.enterprise_value is None
    assert result.equity_value is None
    assert result.value_per_share is None
    assert result.base_fcf_fact_id is None


def test_discount_rate_must_exceed_terminal_growth() -> None:
    result = project_dcf(
        [_fcf_fact()], discount_rate=0.02, terminal_growth_rate=0.05
    )

    assert result.unavailable_reason is not None
    assert result.enterprise_value is None


def test_enterprise_value_from_free_cash_flow_alone() -> None:
    fcf = _fcf_fact(value=100.0)

    result = project_dcf([fcf])

    assert result.unavailable_reason is None
    assert result.enterprise_value is not None
    assert result.enterprise_value > 0
    assert result.base_fcf_fact_id == fcf.fact_id
    # No net debt or share count supplied, so the bridge to equity/per-share
    # cannot run — reported as absent, not guessed at.
    assert result.equity_value is None
    assert result.value_per_share is None
    assert result.net_debt_fact_id is None
    assert result.shares_fact_id is None


def test_equity_and_per_share_bridge_when_facts_are_available() -> None:
    fcf = _fcf_fact(value=100.0)
    net_debt = make_fact(
        metric=NET_DEBT_METRIC,
        label="Net debt",
        value=50.0,
        display_value="50",
        fiscal_year=2024,
        period_start=None,
        period_end=dt.date(2024, 9, 28),
        is_calculated=True,
        formula="total debt − cash and equivalents",
    )
    shares = make_fact(
        metric=SHARES_METRIC,
        label="Diluted shares outstanding",
        value=10.0,
        display_value="10",
        fiscal_year=2024,
        period_start=None,
        period_end=dt.date(2024, 9, 28),
    )

    result = project_dcf([fcf, net_debt, shares])

    assert result.unavailable_reason is None
    assert result.equity_value is not None
    assert result.equity_value == result.enterprise_value - 50.0
    assert result.value_per_share == result.equity_value / 10.0
    assert result.net_debt_fact_id == net_debt.fact_id
    assert result.shares_fact_id == shares.fact_id


def test_defaults_are_labelled_as_defaults_not_sourced() -> None:
    result = project_dcf([_fcf_fact()])

    assert result.discount_rate.source == "default"
    assert result.discount_rate.value == DCF_DEFAULT_DISCOUNT_RATE
    assert result.fcf_growth_rate.source == "default"
    assert result.fcf_growth_rate.value == DCF_DEFAULT_FCF_GROWTH_RATE
    assert result.terminal_growth_rate.source == "default"
    assert result.terminal_growth_rate.value == DCF_DEFAULT_TERMINAL_GROWTH_RATE
    assert "not derived" in result.discount_rate.note


def test_supplied_assumptions_are_labelled_as_supplied() -> None:
    result = project_dcf(
        [_fcf_fact()],
        discount_rate=0.11,
        fcf_growth_rate=0.04,
        terminal_growth_rate=0.025,
    )

    assert result.discount_rate.source == "user_supplied"
    assert result.discount_rate.value == 0.11
    assert result.fcf_growth_rate.source == "user_supplied"
    assert result.terminal_growth_rate.source == "user_supplied"


def test_uses_the_most_recent_period_when_several_are_present() -> None:
    older = _fcf_fact(value=50.0, year=2023)
    newer = _fcf_fact(value=100.0, year=2024)

    result = project_dcf([older, newer])

    assert result.base_fcf_fact_id == newer.fact_id
