"""Assembles one valuation for a completed report.

Responsibility
    Read a report's stored facts, run m07's projection or its inverse over
    them, and render the result for the HTTP boundary. Nothing here computes
    a figure: every number comes from m07, and every rendering from m07's own
    renderers, so the workbench and the document cannot disagree about either.

Why this is not part of the pipeline
    The pipeline runs once and produces a document. This runs whenever a
    reader moves an assumption, long after that document was verified. It
    therefore reads from the fact store rather than from the run, and it must
    never write back: a projection is not a fact, and the report's compliance
    was settled before any of this was asked for.

The compliance boundary
    Nothing here constructs a `Fact`. `ValuationResponse.inputs` carries the
    real facts the calculation rested on, unchanged, so the interface can cite
    them; every projected figure is a `ValuationFigure`, which has no
    accession number and no source. That absence is the point — it is what
    tells a reader, structurally rather than by a label, which figures came
    from a filing and which did not.

Public interface
    build(report_id, query) -> ValuationResponse
    FactsUnavailableError
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.config import (
    VALUATION_NO_RECOMMENDATION_NOTE,
    VALUATION_PROJECTION_NOTE,
    VALUATION_REVERSE_NOTE,
)
from app.models import (
    AssumptionOut,
    DcfEstimate,
    DocumentFact,
    Fact,
    ImpliedGrowth,
    ScenarioCase,
    SensitivityCell,
    SensitivityGrid,
    ValuationFigure,
    ValuationMode,
    ValuationQuery,
    ValuationResponse,
)
from app.modules import m06_factstore, m07_analysis

logger = logging.getLogger(__name__)


class FactsUnavailableError(RuntimeError):
    """The report's facts could not be read, so nothing can be computed."""


async def build(report_id: str, query: ValuationQuery) -> ValuationResponse:
    """One valuation for one set of assumptions.

    Raises:
        FactsUnavailableError: The fact store could not be read. Distinct from
            a filer whose facts simply do not support a discounted cash flow,
            which comes back as a stated reason rather than an error.
    """
    try:
        stored = await m06_factstore.load_facts(report_id)
    except m06_factstore.FactStoreError as cause:
        logger.warning(
            "Valuation could not read facts",
            extra={"report_id": report_id},
            exc_info=cause,
        )
        raise FactsUnavailableError(report_id) from cause

    # A report is generated against its filer's sector template, which gates
    # some metric groups out. Free cash flow may be one of them, so the wider
    # recompute fills in what the template left behind before anything is
    # projected from it.
    facts = m07_analysis.widen(stored)
    discount, growth, terminal = query.as_rates()
    years = query.projection_years or _default_years()

    currency = _currency_of(stored)

    implied = None
    estimate = None
    if query.mode is ValuationMode.REVERSE:
        solved = m07_analysis.solve_implied_growth(
            facts,
            discount_rate=discount,
            terminal_growth_rate=terminal,
            projection_years=years,
        )
        implied = _implied(solved, currency)
        # The grid is anchored on the solved rate rather than on the default,
        # so a reader sees the sensitivity around the answer they were given
        # rather than around a rate nobody chose.
        if solved.implied_growth_rate is not None:
            growth = solved.implied_growth_rate.value
    else:
        estimate = _estimate(
            m07_analysis.project_dcf(
                facts,
                discount_rate=discount,
                fcf_growth_rate=growth,
                terminal_growth_rate=terminal,
                projection_years=years,
            ),
            currency,
        )

    grid = m07_analysis.project_dcf_grid(
        facts,
        discount_rate=discount,
        fcf_growth_rate=growth,
        terminal_growth_rate=terminal,
        projection_years=years,
    )
    scenarios = m07_analysis.project_dcf_scenarios(
        facts,
        discount_rate=discount,
        fcf_growth_rate=growth,
        terminal_growth_rate=terminal,
        projection_years=years,
    )

    notes = [VALUATION_PROJECTION_NOTE, VALUATION_NO_RECOMMENDATION_NOTE]
    if query.mode is ValuationMode.REVERSE:
        notes.insert(1, VALUATION_REVERSE_NOTE)

    return ValuationResponse(
        mode=query.mode,
        inputs=_inputs(report_id, facts, implied, estimate, grid),
        implied=implied,
        estimate=estimate,
        sensitivity=_grid(grid, currency),
        scenarios=_scenarios(scenarios, currency),
        unavailable_reason=_overall_reason(implied, estimate),
        notes=tuple(notes),
    )


def _default_years() -> int:
    from app.config import DCF_DEFAULT_PROJECTION_YEARS

    return DCF_DEFAULT_PROJECTION_YEARS


# --- Rendering ----------------------------------------------------------------


def _currency_of(facts: Sequence[Fact]) -> str | None:
    """The currency the filer reports in, taken from its own figures."""
    for fact in facts:
        if fact.unit and fact.unit.isalpha() and len(fact.unit) == 3:
            return fact.unit
    return None


def _amount(value: float | None, currency: str | None) -> ValuationFigure | None:
    if value is None:
        return None
    rounded, display, unit = m07_analysis.render_amount(value, currency)
    return ValuationFigure(value=rounded, display=display, unit=unit)


def _per_share(value: float | None, currency: str | None) -> ValuationFigure | None:
    if value is None:
        return None
    rounded, display, unit = m07_analysis.render_per_share(value, currency)
    return ValuationFigure(value=rounded, display=display, unit=unit)


def _assumption(assumption: m07_analysis.DcfAssumption) -> AssumptionOut:
    _, display, _ = m07_analysis.render_rate(assumption.value)
    return AssumptionOut(
        name=assumption.name,
        value=assumption.value,
        display=display,
        source=assumption.source,
        note=assumption.note,
    )


def _estimate(
    result: m07_analysis.DcfResult, currency: str | None = None
) -> DcfEstimate:
    return DcfEstimate(
        enterprise_value=_amount(result.enterprise_value, currency),
        equity_value=_amount(result.equity_value, currency),
        value_per_share=_per_share(result.value_per_share, currency),
        projected_free_cash_flow=tuple(
            figure
            for value in result.projected_free_cash_flow
            if (figure := _amount(value, currency)) is not None
        ),
        base_fcf_fact_id=result.base_fcf_fact_id,
        net_debt_fact_id=result.net_debt_fact_id,
        shares_fact_id=result.shares_fact_id,
        discount_rate=_assumption(result.discount_rate),
        fcf_growth_rate=_assumption(result.fcf_growth_rate),
        terminal_growth_rate=_assumption(result.terminal_growth_rate),
        projection_years=result.projection_years,
        unavailable_reason=result.unavailable_reason,
    )


def _implied(
    result: m07_analysis.ReverseDcfResult, currency: str | None = None
) -> ImpliedGrowth:
    return ImpliedGrowth(
        implied_growth_rate=(
            None
            if result.implied_growth_rate is None
            else _assumption(result.implied_growth_rate)
        ),
        market_cap=_amount(result.market_cap, currency),
        market_cap_fact_id=result.market_cap_fact_id,
        base_fcf_fact_id=result.base_fcf_fact_id,
        net_debt_fact_id=result.net_debt_fact_id,
        discount_rate=_assumption(result.discount_rate),
        terminal_growth_rate=_assumption(result.terminal_growth_rate),
        projection_years=result.projection_years,
        iterations=result.iterations,
        converged=result.converged,
        bound_hit=result.bound_hit,
        unavailable_reason=result.unavailable_reason,
    )


def _grid(
    result: m07_analysis.DcfGridResult, currency: str | None = None
) -> SensitivityGrid:
    return SensitivityGrid(
        discount_rates=tuple(_assumption(rate) for rate in result.discount_rates),
        fcf_growth_rates=tuple(
            _assumption(rate) for rate in result.fcf_growth_rates
        ),
        cells=tuple(
            SensitivityCell(
                discount_rate=_assumption(cell.discount_rate),
                fcf_growth_rate=_assumption(cell.fcf_growth_rate),
                value_per_share=_per_share(cell.result.value_per_share, currency),
                equity_value=_amount(cell.result.equity_value, currency),
            )
            for cell in result.cells
        ),
        unavailable_reason=result.unavailable_reason,
    )


def _scenarios(
    result: m07_analysis.DcfScenarioResult, currency: str | None = None
) -> tuple[ScenarioCase, ...]:
    return tuple(
        ScenarioCase(
            name=case.name,
            fcf_growth_rate=_assumption(case.fcf_growth_rate),
            estimate=_estimate(case.result, currency),
        )
        for case in result.cases
    )


def _inputs(
    report_id: str,
    facts: Sequence[Fact],
    implied: ImpliedGrowth | None,
    estimate: DcfEstimate | None,
    grid: m07_analysis.DcfGridResult,
) -> tuple[DocumentFact, ...]:
    """Only the facts actually used, as the document carries them.

    Returning every fact the report holds would be simpler and wrong: the
    provenance rail would gain cards for figures this section never touched,
    and a reader tracing a projection back would have to guess which of them
    it rested on.

    The grid is included because it is rendered in both modes and divides by a
    share count to reach value per share — a real figure the reverse mode's own
    result never names, so without this a reader could see per-share values
    with no way to reach the count behind them.
    """
    wanted = {
        identifier
        for source in (implied, estimate, grid)
        if source is not None
        for identifier in (
            getattr(source, "market_cap_fact_id", None),
            source.base_fcf_fact_id,
            source.net_debt_fact_id,
            getattr(source, "shares_fact_id", None),
        )
        if identifier is not None
    }
    return tuple(
        DocumentFact.of(fact, report_id=report_id)
        for fact in facts
        if fact.fact_id in wanted
    )


def _overall_reason(
    implied: ImpliedGrowth | None, estimate: DcfEstimate | None
) -> str | None:
    """Set only when the mode's own calculation produced nothing at all."""
    if implied is not None:
        return implied.unavailable_reason
    if estimate is not None:
        return estimate.unavailable_reason
    return None
