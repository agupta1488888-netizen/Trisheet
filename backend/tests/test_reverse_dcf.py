"""Tests for m07_analysis.solve_implied_growth.

The reverse of `project_dcf`: instead of assuming a growth rate and reporting
a value, it takes the market's own valuation as given and reports the growth
rate that would justify it.

What these guard is the property that makes the answer trustworthy — that the
solve is the exact inverse of the projection, so the two can never quietly
disagree — and the refusals, which matter more here than in the forward
direction. A reverse DCF is arithmetic that will happily return a number on
inputs where the number means nothing: a negative base cash flow inverts the
relationship it solves along, and a valuation outside the search bracket has
no solution inside it. Both must refuse or report a bound, never a figure a
reader could act on.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.config import (
    DCF_DEFAULT_DISCOUNT_RATE,
    DCF_DEFAULT_TERMINAL_GROWTH_RATE,
    DCF_REVERSE_GROWTH_MAX,
    DCF_REVERSE_GROWTH_MIN,
    DCF_REVERSE_RATE_TOLERANCE,
    MARKET_CAP_METRIC,
)
from app.models import ExtractionMethod, Fact, SourceTier, SourceType
from app.modules.m07_analysis import project_dcf, solve_implied_growth
from tests.conftest import make_fact

FCF_METRIC = "cashflow.free_cash_flow"
NET_DEBT_METRIC = "derived.net_debt"


def _fcf_fact(value: float = 100.0, year: int = 2024) -> Fact:
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


def _net_debt_fact(value: float = 200.0, year: int = 2024) -> Fact:
    return make_fact(
        metric=NET_DEBT_METRIC,
        label="Net debt",
        value=value,
        display_value=f"{value:,.0f}",
        fiscal_year=year,
        period_start=None,
        period_end=dt.date(year, 9, 28),
        is_calculated=True,
        formula="total debt FY2024 − cash and equivalents FY2024",
    )


def _market_cap_fact(value: float) -> Fact:
    return make_fact(
        metric=MARKET_CAP_METRIC,
        label="Market capitalisation",
        value=value,
        display_value=f"{value:,.0f} USD",
        fiscal_year=None,
        period_start=None,
        period_end=dt.date(2026, 8, 5),
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
    )


def _facts(*, fcf: float = 100.0, net_debt: float = 200.0) -> list[Fact]:
    return [_fcf_fact(fcf), _net_debt_fact(net_debt)]


# --- The inverse property -----------------------------------------------------


@pytest.mark.parametrize("growth", [-0.10, 0.0, 0.03, 0.07, 0.25])
def test_it_recovers_the_growth_rate_the_projection_was_built_with(
    growth: float,
) -> None:
    """The solve is the exact inverse of the projection.

    This is the property the whole calculation rests on: value the filer
    forward at a known rate, hand the answer back as what the market pays,
    and the same rate must come out. If these two ever drift apart, every
    implied figure in the report is wrong in a way no other test would catch.
    """
    facts = _facts()
    forward = project_dcf(facts, fcf_growth_rate=growth)
    assert forward.equity_value is not None

    result = solve_implied_growth(facts, market_cap=forward.equity_value)

    assert result.unavailable_reason is None
    assert result.converged
    assert result.bound_hit is None
    assert result.implied_growth_rate is not None
    assert result.implied_growth_rate.value == pytest.approx(
        growth, abs=DCF_REVERSE_RATE_TOLERANCE
    )


def test_it_reads_the_market_capitalisation_off_the_facts() -> None:
    """The valuation is a cited fact, not something a caller has to carry."""
    facts = _facts()
    forward = project_dcf(facts, fcf_growth_rate=0.05)
    assert forward.equity_value is not None
    cap = _market_cap_fact(forward.equity_value)

    result = solve_implied_growth([*facts, cap])

    assert result.implied_growth_rate is not None
    assert result.implied_growth_rate.value == pytest.approx(0.05, abs=1e-6)
    assert result.market_cap_fact_id == cap.fact_id


def test_a_higher_valuation_implies_a_higher_growth_rate() -> None:
    """Monotonicity, which is what makes a bracketed bisection valid here."""
    facts = _facts()
    low = project_dcf(facts, fcf_growth_rate=0.02).equity_value
    high = project_dcf(facts, fcf_growth_rate=0.08).equity_value
    assert low is not None and high is not None

    cheaper = solve_implied_growth(facts, market_cap=low)
    dearer = solve_implied_growth(facts, market_cap=high)

    assert cheaper.implied_growth_rate is not None
    assert dearer.implied_growth_rate is not None
    assert dearer.implied_growth_rate.value > cheaper.implied_growth_rate.value


# --- What it cites and how it labels itself -----------------------------------


def test_the_solved_rate_is_labelled_as_solved_and_not_as_a_filing() -> None:
    facts = _facts()
    forward = project_dcf(facts, fcf_growth_rate=0.04)
    assert forward.equity_value is not None

    result = solve_implied_growth(facts, market_cap=forward.equity_value)

    assert result.implied_growth_rate is not None
    assert result.implied_growth_rate.source == "solved"
    note = result.implied_growth_rate.note
    assert "not a forecast" in note.lower()
    assert "not a filed figure" in note.lower()


def test_it_cites_the_real_facts_it_rested_on() -> None:
    facts = _facts()
    forward = project_dcf(facts, fcf_growth_rate=0.04)
    assert forward.equity_value is not None

    result = solve_implied_growth(facts, market_cap=forward.equity_value)

    assert result.base_fcf_fact_id == facts[0].fact_id
    assert result.net_debt_fact_id == facts[1].fact_id


def test_the_remaining_assumptions_are_still_named_as_assumptions() -> None:
    """It removes one assumption, not three."""
    facts = _facts()
    forward = project_dcf(facts, fcf_growth_rate=0.04)
    assert forward.equity_value is not None

    result = solve_implied_growth(facts, market_cap=forward.equity_value)

    assert result.discount_rate.source == "default"
    assert result.discount_rate.value == DCF_DEFAULT_DISCOUNT_RATE
    assert result.terminal_growth_rate.source == "default"
    assert result.terminal_growth_rate.value == DCF_DEFAULT_TERMINAL_GROWTH_RATE
    assert "not derived from" in result.discount_rate.note


# --- Refusals -----------------------------------------------------------------


def test_no_market_capitalisation_is_a_refusal_not_a_default() -> None:
    result = solve_implied_growth(_facts())

    assert result.implied_growth_rate is None
    assert result.unavailable_reason is not None
    assert "market capitalisation" in result.unavailable_reason


def test_no_free_cash_flow_is_a_refusal() -> None:
    result = solve_implied_growth([_net_debt_fact()], market_cap=1_000.0)

    assert result.implied_growth_rate is None
    assert result.unavailable_reason is not None
    assert "free cash flow" in result.unavailable_reason.lower()


def test_no_net_debt_is_a_refusal_rather_than_a_zero() -> None:
    """Without it there is no bridge from enterprise value to an equity claim.

    Treating an unavailable net debt as zero would compare a market
    capitalisation against an enterprise value, which is not the same thing
    and would misstate the implied rate for every geared filer.
    """
    result = solve_implied_growth([_fcf_fact()], market_cap=1_000.0)

    assert result.implied_growth_rate is None
    assert result.unavailable_reason is not None
    assert "net debt" in result.unavailable_reason.lower()


@pytest.mark.parametrize("fcf", [0.0, -50.0])
def test_a_base_cash_flow_that_is_not_positive_refuses(fcf: float) -> None:
    """The relationship inverts below zero, so a solved rate would be nonsense.

    A higher growth rate applied to a negative cash flow makes the projection
    more negative, so the bisection would run along a decreasing curve and
    return a rate that is arithmetically valid and financially meaningless.
    """
    result = solve_implied_growth(
        _facts(fcf=fcf), market_cap=1_000.0
    )

    assert result.implied_growth_rate is None
    assert result.unavailable_reason is not None
    assert "not positive" in result.unavailable_reason


def test_the_discount_rate_must_exceed_the_terminal_rate() -> None:
    result = solve_implied_growth(
        _facts(), market_cap=1_000.0, discount_rate=0.02, terminal_growth_rate=0.05
    )

    assert result.implied_growth_rate is None
    assert result.unavailable_reason is not None


# --- Bounds -------------------------------------------------------------------


def test_a_valuation_below_the_bracket_is_reported_as_a_bound() -> None:
    """Not extrapolated past, and not silently clamped to look solved."""
    result = solve_implied_growth(_facts(), market_cap=-1e12)

    assert result.bound_hit == "lower"
    assert not result.converged
    assert result.implied_growth_rate is not None
    assert result.implied_growth_rate.value == DCF_REVERSE_GROWTH_MIN
    assert "at or below" in result.implied_growth_rate.note.lower()


def test_a_valuation_above_the_bracket_is_reported_as_a_bound() -> None:
    result = solve_implied_growth(_facts(), market_cap=1e15)

    assert result.bound_hit == "upper"
    assert not result.converged
    assert result.implied_growth_rate is not None
    assert result.implied_growth_rate.value == DCF_REVERSE_GROWTH_MAX
    assert "at or above" in result.implied_growth_rate.note.lower()


def test_the_solve_terminates() -> None:
    """A bounded bisection, so the ceiling is never what stops it."""
    from app.config import DCF_REVERSE_MAX_ITERATIONS

    facts = _facts()
    forward = project_dcf(facts, fcf_growth_rate=0.0371)
    assert forward.equity_value is not None

    result = solve_implied_growth(facts, market_cap=forward.equity_value)

    assert result.converged
    assert 0 < result.iterations < DCF_REVERSE_MAX_ITERATIONS


# --- The compliance boundary --------------------------------------------------


def test_a_solve_produces_no_facts_and_leaves_the_inputs_alone() -> None:
    """A projection is never a Fact, and this must not become the exception.

    `Fact` cannot exist without an accession number, and a rate solved from a
    price names no filing. The result is a frozen dataclass for that reason,
    and the facts it read must come back untouched so nothing it computed can
    reach the fact store through them.
    """
    facts = _facts()
    before = [fact.model_dump() for fact in facts]

    result = solve_implied_growth(facts, market_cap=5_000.0)

    assert not isinstance(result, Fact)
    assert not hasattr(result, "accession_no")
    assert [fact.model_dump() for fact in facts] == before


# --- The two-dimensional grid -------------------------------------------------


def test_the_grid_covers_both_axes_and_orders_them_row_major() -> None:
    from app.config import (
        DCF_SENSITIVITY_DISCOUNT_RATE_STEPS,
        DCF_SENSITIVITY_FCF_GROWTH_STEPS,
    )
    from app.modules.m07_analysis import project_dcf_grid

    grid = project_dcf_grid(_facts())

    rows = len(DCF_SENSITIVITY_DISCOUNT_RATE_STEPS)
    columns = len(DCF_SENSITIVITY_FCF_GROWTH_STEPS)
    assert grid.unavailable_reason is None
    assert len(grid.cells) == rows * columns
    assert len(grid.discount_rates) == rows
    assert len(grid.fcf_growth_rates) == columns
    # Row-major: the first row shares one discount rate across every growth.
    first_row = grid.cells[:columns]
    assert len({cell.discount_rate.value for cell in first_row}) == 1
    assert len({cell.fcf_growth_rate.value for cell in first_row}) == columns


def test_every_grid_cell_shares_the_same_real_inputs() -> None:
    """Only the assumptions vary. The filings behind them do not."""
    from app.modules.m07_analysis import project_dcf_grid

    facts = _facts()
    grid = project_dcf_grid(facts)

    assert grid.base_fcf_fact_id == facts[0].fact_id
    assert grid.net_debt_fact_id == facts[1].fact_id
    assert {cell.result.base_fcf_fact_id for cell in grid.cells} == {
        facts[0].fact_id
    }


def test_the_grid_moves_the_right_way_on_each_axis() -> None:
    from app.modules.m07_analysis import project_dcf_grid

    grid = project_dcf_grid(_facts())
    columns = len(grid.fcf_growth_rates)

    row = grid.cells[:columns]
    values = [cell.result.equity_value for cell in row]
    assert all(value is not None for value in values)
    # Higher growth, higher value, at a fixed discount rate.
    assert values == sorted(values)  # type: ignore[type-var]

    column = grid.cells[::columns]
    down = [cell.result.equity_value for cell in column]
    # Higher discount rate, lower value, at a fixed growth rate.
    assert down == sorted(down, reverse=True)  # type: ignore[type-var]


def test_an_unbuildable_base_yields_no_partial_grid() -> None:
    """A gap beside a real cell invites the reader to fill it themselves."""
    from app.modules.m07_analysis import project_dcf_grid

    grid = project_dcf_grid([])

    assert grid.unavailable_reason is not None
    assert grid.cells == ()
    assert grid.discount_rates == ()
    assert grid.fcf_growth_rates == ()
