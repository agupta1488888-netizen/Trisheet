"""Tests for m07_analysis.project_dcf_sensitivity.

Every point in a sensitivity table is a full DCF run — the same discipline
project_dcf enforces applies at every step: a value is only ever shown when
project_dcf actually computed it, and unavailability degrades honestly
without leaving a gap silently filled in.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.config import DCF_DEFAULT_DISCOUNT_RATE, DCF_SENSITIVITY_DISCOUNT_RATE_STEPS
from app.modules.m07_analysis import project_dcf, project_dcf_sensitivity
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
    result = project_dcf_sensitivity([])

    assert result.unavailable_reason is not None
    assert result.points == ()
    assert result.base_fcf_fact_id is None


def test_default_steps_produce_the_documented_number_of_points() -> None:
    result = project_dcf_sensitivity([_fcf_fact()])

    assert result.unavailable_reason is None
    assert len(result.points) == len(DCF_SENSITIVITY_DISCOUNT_RATE_STEPS)


def test_base_point_matches_a_plain_project_dcf_call() -> None:
    fcf = _fcf_fact()

    sensitivity = project_dcf_sensitivity([fcf])
    plain = project_dcf([fcf])

    base_point = next(
        point
        for point in sensitivity.points
        if point.discount_rate.value == DCF_DEFAULT_DISCOUNT_RATE
    )
    assert base_point.result.enterprise_value == plain.enterprise_value
    assert base_point.result.base_fcf_fact_id == plain.base_fcf_fact_id


def test_discount_rate_offsets_are_applied_around_the_base_rate() -> None:
    result = project_dcf_sensitivity(
        [_fcf_fact()], discount_rate=0.10, steps=(-0.01, 0.0, 0.01)
    )

    rates = [point.discount_rate.value for point in result.points]
    assert rates == pytest.approx([0.09, 0.10, 0.11])


def test_higher_discount_rate_yields_a_lower_enterprise_value() -> None:
    result = project_dcf_sensitivity([_fcf_fact()], steps=(-0.02, 0.0, 0.02))

    values = [point.result.enterprise_value for point in result.points]
    assert values[0] is not None
    assert values[1] is not None
    assert values[2] is not None
    assert values[0] > values[1] > values[2]


def test_supplied_discount_rate_is_labelled_as_supplied_at_every_point() -> None:
    result = project_dcf_sensitivity([_fcf_fact()], discount_rate=0.12)

    for point in result.points:
        assert point.discount_rate.source == "user_supplied"


def test_a_point_pushed_below_terminal_growth_reports_its_own_unavailability() -> None:
    # The lowest offset collides with the supplied terminal growth rate here,
    # so that one point is unavailable while the rest of the table is not —
    # the base case (offset 0.0) stays clear of the collision.
    result = project_dcf_sensitivity(
        [_fcf_fact()],
        discount_rate=0.021,
        terminal_growth_rate=0.02,
        steps=(-0.005, 0.0, 0.01),
    )

    unavailable_points = [
        point for point in result.points if point.result.unavailable_reason is not None
    ]
    available_points = [
        point for point in result.points if point.result.unavailable_reason is None
    ]
    assert len(unavailable_points) == 1
    assert len(available_points) == 2
