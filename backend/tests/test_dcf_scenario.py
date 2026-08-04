"""Tests for m07_analysis.project_dcf_scenarios.

Mirrors test_dcf_sensitivity.py's discipline for the free-cash-flow growth
axis instead of the discount-rate axis: every named case is a full DCF run,
and unavailability degrades honestly rather than leaving a gap filled in.
"""

from __future__ import annotations

import datetime as dt

from app.config import DCF_DEFAULT_FCF_GROWTH_RATE, DCF_SCENARIO_FCF_GROWTH_DELTAS
from app.modules.m07_analysis import project_dcf, project_dcf_scenarios
from tests.conftest import make_fact

FCF_METRIC = "cashflow.free_cash_flow"


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
    result = project_dcf_scenarios([])

    assert result.unavailable_reason is not None
    assert result.cases == ()
    assert result.base_fcf_fact_id is None


def test_default_deltas_produce_bear_base_and_bull_cases() -> None:
    result = project_dcf_scenarios([_fcf_fact()])

    assert result.unavailable_reason is None
    names = [case.name for case in result.cases]
    assert names == list(DCF_SCENARIO_FCF_GROWTH_DELTAS.keys())
    assert names == ["bear", "base", "bull"]


def test_base_case_matches_a_plain_project_dcf_call() -> None:
    fcf = _fcf_fact()

    scenario = project_dcf_scenarios([fcf])
    plain = project_dcf([fcf])

    base_case = next(case for case in scenario.cases if case.name == "base")
    assert base_case.result.enterprise_value == plain.enterprise_value
    assert base_case.fcf_growth_rate.value == DCF_DEFAULT_FCF_GROWTH_RATE


def test_bull_case_grows_faster_than_bear_case() -> None:
    result = project_dcf_scenarios([_fcf_fact()])

    by_name = {case.name: case for case in result.cases}
    assert by_name["bull"].fcf_growth_rate.value > by_name["base"].fcf_growth_rate.value
    assert by_name["base"].fcf_growth_rate.value > by_name["bear"].fcf_growth_rate.value
    bull_value = by_name["bull"].result.enterprise_value
    bear_value = by_name["bear"].result.enterprise_value
    assert bull_value is not None
    assert bear_value is not None
    assert bull_value > bear_value


def test_supplied_growth_rate_is_labelled_as_supplied_in_every_case() -> None:
    result = project_dcf_scenarios([_fcf_fact()], fcf_growth_rate=0.05)

    for case in result.cases:
        assert case.fcf_growth_rate.source == "user_supplied"


def test_custom_deltas_replace_the_default_named_cases() -> None:
    result = project_dcf_scenarios(
        [_fcf_fact()], deltas={"downturn": -0.01, "recovery": 0.03}
    )

    names = [case.name for case in result.cases]
    assert names == ["downturn", "recovery"]
