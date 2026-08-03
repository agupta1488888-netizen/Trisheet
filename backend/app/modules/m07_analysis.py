"""m07 — analysis. All arithmetic in the system happens here.

Responsibility
    Compute growth rates, margins, ratios and bridges from extracted facts,
    using pandas and numpy. The LLM never performs arithmetic; it receives
    what this module produces and writes prose about it.

Constraints
    - Pure functions only. No I/O, no globals, no side effects. The one
      mutable object, `_Ledger`, is created per call and never escapes except
      as the immutable result — identical input always yields byte-identical
      output, which is what makes a report reproducible.
    - Every derived value carries its formula so the interface can label it
      "calculated" and show how it was reached.
    - Unit tests are required for this module.

What is deliberately not computed
    A metric whose inputs are absent is not emitted. It is never interpolated,
    never carried forward from a prior year and never approximated from a
    near-enough figure. The interface renders the gap as "Not disclosed",
    which is the truthful answer. Two consequences worth stating:

    - Growth from a non-positive base is undefined, not negative. A company
      that lost money last year and made money this year has no meaningful
      percentage growth, so none is given.
    - NAV per share is computed only where the filer discloses real estate at
      fair value. Deriving it from a capitalisation rate would be an estimate
      wearing a calculation's clothes.

Traceability
    Every computed figure records the ids of the facts it consumed, in
    `AnalysisResult.derivations`. Ids are content-addressed — a fact's id is a
    digest of its identity and provenance, so the same filing always yields the
    same id, in this process or any other.

Public interface
    analyse(facts, sic_code=...) -> AnalysisResult
    compute_derived_metrics(facts, sic_code=...) -> list[Fact]
    fact_id(fact) -> str
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import pandas as pd

from app.config import (
    ANALYSIS_ZERO_TOLERANCE,
    ANNUAL_PERIOD_MAX_DAYS,
    ANNUAL_PERIOD_MIN_DAYS,
    CAGR_WINDOWS_YEARS,
    COMMON_SIZE_BALANCE_METRICS,
    COMMON_SIZE_INCOME_METRICS,
    CURRENCY_DECIMAL_PLACES,
    DAYS_DISPLAY_TEMPLATE,
    DAYS_IN_YEAR,
    DISPLAY_SCALE_DIVISOR,
    GEOGRAPHIC_SEGMENT_AXES,
    GROWTH_METRICS,
    NON_CURRENCY_UNIT_KEYS,
    PER_SHARE_DISPLAY_TEMPLATE,
    PERCENT_DECIMAL_PLACES,
    PERCENT_DISPLAY_TEMPLATE,
    PERCENTAGE_POINTS_DISPLAY_TEMPLATE,
    QUARTERLY_PERIOD_MAX_DAYS,
    QUARTERLY_PERIOD_MIN_DAYS,
    RATIO_DECIMAL_PLACES,
    RATIO_DISPLAY_TEMPLATE,
    RESIDUAL_TOLERANCE_POINTS,
    SEGMENT_METRIC,
    TIMES_DISPLAY_TEMPLATE,
    TTM_METRICS,
    TTM_QUARTER_ADJACENCY_TOLERANCE_DAYS,
    TTM_QUARTER_COUNT,
    UNIT_DAYS,
    UNIT_PER_SHARE_TEMPLATE,
    UNIT_PERCENT,
    UNIT_PERCENTAGE_POINTS,
    UNIT_RATIO,
    UNIT_TIMES,
    SectorTemplate,
    sector_template_for_sic,
)
from app.models import ExtractionMethod, Fact, SourceTier

# --- Module constants -------------------------------------------------------

#: A ratio times this is a percentage. Named so no bare 100 appears in a
#: formula's implementation.
PERCENT_SCALE = 100.0

#: Days between the end of one period and the start of the next, when they
#: abut exactly.
ADJACENT_PERIOD_GAP_DAYS = 1

#: Halves a sum when averaging two balance sheet dates.
AVERAGE_DIVISOR = 2.0

#: Collapses any run of non-alphanumeric characters when a segment name is
#: turned into a metric path component.
_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")

#: Splits "srt:ProductOrServiceAxis" style QNames.
_QNAME_SPLIT = re.compile(r"[:_]")

#: Inserts a space at each lower-to-upper transition, so "ProductOrService"
#: slugs to "product_or_service" rather than one run of letters.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

#: How an intermediate figure reads inside a formula. Without these a derived
#: input would name itself by its dotted metric path, and a formula that says
#: "derived.invested_capital" explains nothing to a reader.
_DERIVED_LABELS: dict[str, str] = {
    "derived.ebitda": "EBITDA",
    "derived.total_debt": "total debt",
    "derived.net_debt": "net debt",
    "derived.nopat": "NOPAT",
    "derived.invested_capital": "invested capital",
    "derived.effective_tax_rate": "effective tax rate",
    "derived.net_working_capital": "net working capital",
    "derived.free_cash_flow": "free cash flow",
    "derived.fcff": "free cash flow to the firm",
    "derived.ffo": "funds from operations",
    "derived.affo": "adjusted funds from operations",
}


class MetricGroup(StrEnum):
    """A family of derived metrics, switched on or off by sector template."""

    GROWTH = "growth"
    MARGINS = "margins"
    RETURNS = "returns"
    DUPONT = "dupont"
    LIQUIDITY = "liquidity"
    LEVERAGE = "leverage"
    CASH_FLOW = "cash_flow"
    WORKING_CAPITAL = "working_capital"
    COMMON_SIZE = "common_size"
    ATTRIBUTION = "attribution"
    MARGIN_BRIDGE = "margin_bridge"
    TTM = "ttm"
    PER_SHARE = "per_share"
    BANK = "bank"
    REIT = "reit"
    INSURANCE = "insurance"


#: Groups every filer gets. Growth, returns and leverage read the same way for
#: a bank as for a manufacturer, even where the components differ.
_UNIVERSAL_GROUPS: frozenset[MetricGroup] = frozenset(
    {
        MetricGroup.GROWTH,
        MetricGroup.MARGINS,
        MetricGroup.RETURNS,
        MetricGroup.DUPONT,
        MetricGroup.LEVERAGE,
        MetricGroup.COMMON_SIZE,
        MetricGroup.ATTRIBUTION,
        MetricGroup.TTM,
        MetricGroup.PER_SHARE,
    }
)

#: Which metric families each template runs. The exclusions carry the
#: reasoning: a bank publishes no classified balance sheet, so it has no
#: current ratio and no cash conversion cycle; it reports no cost of revenue,
#: so it has no gross margin and therefore no margin bridge; and free cash flow
#: is not how a deposit-funded balance sheet is judged. A REIT has no inventory
#: or receivable cycle. An insurer is judged on its combined ratio, not on
#: working capital.
SECTOR_METRIC_GROUPS: dict[SectorTemplate, frozenset[MetricGroup]] = {
    SectorTemplate.GENERAL: _UNIVERSAL_GROUPS
    | {
        MetricGroup.LIQUIDITY,
        MetricGroup.CASH_FLOW,
        MetricGroup.WORKING_CAPITAL,
        MetricGroup.MARGIN_BRIDGE,
    },
    SectorTemplate.BANK: _UNIVERSAL_GROUPS | {MetricGroup.BANK},
    SectorTemplate.REIT: _UNIVERSAL_GROUPS
    | {
        MetricGroup.LIQUIDITY,
        MetricGroup.CASH_FLOW,
        MetricGroup.MARGIN_BRIDGE,
        MetricGroup.REIT,
    },
    SectorTemplate.INSURANCE: _UNIVERSAL_GROUPS
    | {MetricGroup.CASH_FLOW, MetricGroup.INSURANCE},
}

#: Every group there is, regardless of sector template. The chat assistant
#: passes this to `analyse` so a filer's own template cannot gate out a metric
#: that its reported facts can still support — the report pipeline never passes
#: this, and keeps exactly today's per-sector selection.
ALL_METRIC_GROUPS: frozenset[MetricGroup] = frozenset(MetricGroup)


class _Kind(StrEnum):
    """How a derived figure is measured, which fixes its unit and rendering."""

    PERCENT = "percent"
    POINTS = "points"
    RATIO = "ratio"
    TIMES = "times"
    DAYS = "days"
    CURRENCY = "currency"
    PER_SHARE = "per_share"


# --- Public value types -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Derivation:
    """What one computed figure was made of.

    The audit record behind a calculated fact: which facts went in, and by
    what formula. Held beside the facts rather than on them so that this
    module needs no change to the shared `Fact` contract.
    """

    #: Id of the calculated fact this describes.
    fact_id: str
    metric: str
    period_end: dt.date
    #: Ids of every fact consumed, sorted so the record is order-independent.
    inputs: tuple[str, ...]
    formula: str


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """Everything m07 derived, with the trail behind each figure."""

    facts: tuple[Fact, ...]
    derivations: tuple[Derivation, ...]
    #: The metric set these figures were computed with.
    template: SectorTemplate

    def derivation_for(self, target: str) -> Derivation | None:
        """The trail behind a fact id, or None when it is not a derived fact."""
        for derivation in self.derivations:
            if derivation.fact_id == target:
                return derivation
        return None


def fact_id(fact: Fact) -> str:
    """A fact's content-addressed identity.

    Delegates to `Fact.fact_id`, which is the one definition of what makes two
    facts the same fact. There must be exactly one: m10 cites these ids in
    prose, m11 verifies those citations against the fact store, and the
    derivation graph below names them as inputs. A second scheme here would
    mean a derivation naming an id no citation could resolve, which is a
    broken audit trail that still looks like a working one.

    Deterministic across processes and runs, and deliberately not a random
    uuid — a derivation that named random ids could not be checked against a
    re-run.
    """
    return fact.fact_id


# --- Public interface -------------------------------------------------------


def analyse(
    facts: Sequence[Fact],
    *,
    sic_code: str | None = None,
    groups: frozenset[MetricGroup] | None = None,
) -> AnalysisResult:
    """Derives every metric the filer's sector template calls for.

    Pure: `facts` is read and never mutated, nothing outside is touched, and
    the same input yields the same output every time.

    Args:
        facts: Reported facts from m03. Facts without a value, without a
            fiscal year, or covering a non-annual period are ignored for the
            annual panel; quarterly and year-to-date facts are used only to
            build a trailing twelve months.
        sic_code: The filer's SIC code, as EDGAR reports it. Selects the metric
            set. An absent or unrecognised code gets the general set.
        groups: Overrides the sector template's metric selection when given.
            The report pipeline never passes this, so it always gets exactly
            today's per-sector selection. The chat assistant passes
            `ALL_METRIC_GROUPS` so a metric the filer's own template gates out
            (e.g. a bank's current ratio) can still be derived from the same
            reported facts when a reader asks for it directly.

    Returns:
        The derived facts and the derivation behind each, sorted so that the
        result is stable across runs.
    """
    template = sector_template_for_sic(sic_code)
    effective_groups = groups if groups is not None else SECTOR_METRIC_GROUPS[template]

    workspace = _Workspace(panel=_build_panel(facts))
    ledger = _Ledger()

    _compute_derived_absolutes(workspace, ledger, effective_groups)
    _compute_growth(workspace, ledger, effective_groups)
    _compute_margins(workspace, ledger, effective_groups)
    _compute_returns(workspace, ledger, effective_groups)
    _compute_dupont(workspace, ledger, effective_groups)
    _compute_liquidity(workspace, ledger, effective_groups)
    _compute_leverage(workspace, ledger, effective_groups)
    _compute_cash_flow(workspace, ledger, effective_groups)
    _compute_working_capital(workspace, ledger, effective_groups)
    _compute_common_size(workspace, ledger, effective_groups)
    _compute_margin_bridge(workspace, ledger, effective_groups)
    _compute_per_share(workspace, ledger, effective_groups)
    _compute_bank(workspace, ledger, effective_groups)
    _compute_reit(workspace, ledger, effective_groups)
    _compute_insurance(workspace, ledger, effective_groups)
    _compute_attribution(facts, workspace, ledger, effective_groups)
    _compute_ttm(facts, ledger, effective_groups)

    return ledger.result(template)


def compute_derived_metrics(
    facts: list[Fact], *, sic_code: str | None = None
) -> list[Fact]:
    """Derives metrics from reported facts.

    The pipeline's entry point. Callers that need the audit trail as well as
    the figures should use `analyse`.

    Pure: the input list is not mutated and nothing outside is touched.
    """
    return list(analyse(facts, sic_code=sic_code).facts)


# --- The annual panel -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Panel:
    """Annual figures as a frame, with the fact behind every cell.

    `frame` is indexed by fiscal year ascending, one float column per metric,
    NaN where the filer did not report. Holding the facts alongside is what
    lets a computed cell name its sources.
    """

    frame: pd.DataFrame
    facts: Mapping[tuple[str, int], Fact]

    @property
    def years(self) -> tuple[int, ...]:
        """Fiscal years present, ascending."""
        return tuple(int(year) for year in self.frame.index)


def _build_panel(facts: Sequence[Fact]) -> _Panel:
    """Reduces reported facts to one value per metric per fiscal year.

    Segment facts are excluded: they subdivide a consolidated figure and
    summing them into the panel would double-count it. Facts the filer did not
    label with a fiscal year are excluded too — a year this system invented is
    a year the filer never reported.
    """
    chosen: dict[tuple[str, int], Fact] = {}

    for fact in facts:
        if fact.value is None or fact.fiscal_year is None:
            continue
        if fact.segment_member is not None:
            continue
        if not _is_annual_or_instant(fact):
            continue

        key = (fact.metric, fact.fiscal_year)
        incumbent = chosen.get(key)
        if incumbent is None or _supersedes(fact, incumbent):
            chosen[key] = fact

    if not chosen:
        return _Panel(frame=pd.DataFrame(dtype=float), facts={})

    metrics = sorted({metric for metric, _ in chosen})
    years = sorted({year for _, year in chosen})

    frame = pd.DataFrame(
        np.full((len(years), len(metrics)), np.nan, dtype=float),
        index=pd.Index(years, name="fiscal_year"),
        columns=metrics,
    )
    for (metric, year), fact in chosen.items():
        frame.at[year, metric] = fact.value

    return _Panel(frame=frame, facts=chosen)


def _is_annual_or_instant(fact: Fact) -> bool:
    """True for a balance sheet date or a period the length of a fiscal year."""
    if fact.period_start is None:
        return True
    days = (fact.period_end - fact.period_start).days
    return ANNUAL_PERIOD_MIN_DAYS <= days <= ANNUAL_PERIOD_MAX_DAYS


def _supersedes(candidate: Fact, incumbent: Fact) -> bool:
    """True when `candidate` is the better authority for the same period.

    The later filing is the filer's current word. Accession number breaks a
    same-day tie so the choice never depends on input order.
    """
    if candidate.filed_date != incumbent.filed_date:
        return candidate.filed_date > incumbent.filed_date
    return candidate.accession_no > incumbent.accession_no


@dataclass(slots=True)
class _Workspace:
    """The panel plus the absolute figures derived from it.

    Intermediate results — EBITDA, invested capital, FFO — are looked up the
    same way as reported ones, so a ratio never cares whether its input was
    extracted or computed. Local to one `analyse` call.
    """

    panel: _Panel
    derived: dict[tuple[str, int], tuple[float, tuple[Fact, ...]]] = field(
        default_factory=dict
    )

    @property
    def years(self) -> tuple[int, ...]:
        return self.panel.years

    def put(
        self, metric: str, year: int, value: float, sources: Sequence[Fact]
    ) -> None:
        """Records a derived absolute so later ratios can consume it."""
        self.derived[(metric, year)] = (value, tuple(sources))

    def lookup(
        self, metric: str, year: int
    ) -> tuple[float, tuple[Fact, ...]] | None:
        """A figure and the facts behind it, derived or reported."""
        found = self.derived.get((metric, year))
        if found is not None:
            return found

        fact = self.panel.facts.get((metric, year))
        if fact is None or fact.value is None:
            return None
        return (fact.value, (fact,))

    def require(
        self, year: int, *metrics: str
    ) -> tuple[dict[str, float], tuple[Fact, ...]] | None:
        """Every named figure for one year, or None when any is missing.

        All-or-nothing on purpose: a ratio computed from a partial set would
        be arithmetically valid and financially wrong.
        """
        values: dict[str, float] = {}
        sources: list[Fact] = []
        for metric in metrics:
            found = self.lookup(metric, year)
            if found is None:
                return None
            values[metric] = found[0]
            sources.extend(found[1])
        return values, tuple(sources)

    def label_of(self, metric: str, year: int) -> str:
        """How a figure reads inside a formula.

        The filer's own label for anything it reported, and a written-out name
        for anything this module derived.
        """
        derived = _DERIVED_LABELS.get(metric)
        if derived is not None:
            return derived
        fact = self.panel.facts.get((metric, year))
        return fact.label if fact is not None else metric


# --- The ledger -------------------------------------------------------------


class _Ledger:
    """Collects derived facts and the trail behind each.

    The only mutable state in the module. Created inside `analyse`, never
    shared, never escapes except as the frozen `AnalysisResult`.
    """

    def __init__(self) -> None:
        self._facts: list[Fact] = []
        self._derivations: list[Derivation] = []

    def add(
        self,
        *,
        metric: str,
        label: str,
        kind: _Kind,
        value: float | None,
        formula: str,
        sources: Sequence[Fact],
        period: tuple[dt.date | None, dt.date] | None = None,
    ) -> None:
        """Records one computed figure, or nothing when it is not computable.

        A value that is None, infinite or NaN is dropped rather than rendered:
        the interface reads a gap as "Not disclosed", which is true, where a
        rendered infinity would not be.

        `period` overrides the window inferred from the inputs. A trailing
        twelve months needs it: the window it covers is not the window of any
        one figure that went into it.
        """
        if value is None or not math.isfinite(value) or not sources:
            return

        ordered = _order_sources(sources)
        period_start, period_end = period if period else _period_of(ordered)
        rounded, display, unit = _render(kind, value, _currency_of(ordered))
        anchor = max(ordered, key=lambda f: (f.filed_date, f.accession_no))

        fact = Fact(
            metric=metric,
            label=label,
            value=rounded,
            display_value=display,
            unit=unit,
            period_start=period_start,
            period_end=period_end,
            fiscal_year=_fiscal_year_of(ordered),
            tier=SourceTier(max(int(source.tier) for source in ordered)),
            source_type=anchor.source_type,
            source_url=str(anchor.source_url),
            accession_no=anchor.accession_no,
            filed_date=anchor.filed_date,
            extraction_method=ExtractionMethod.CALCULATED,
            # A derived figure is no more certain than its least certain input.
            confidence=min(source.confidence for source in ordered),
            is_calculated=True,
            formula=formula,
        )

        self._facts.append(fact)
        self._derivations.append(
            Derivation(
                fact_id=fact_id(fact),
                metric=metric,
                period_end=period_end,
                inputs=tuple(sorted({fact_id(source) for source in ordered})),
                formula=formula,
            )
        )

    def result(self, template: SectorTemplate) -> AnalysisResult:
        """The collected output, in a stable order."""
        order = sorted(
            range(len(self._facts)), key=lambda i: _sort_key(self._facts[i])
        )
        return AnalysisResult(
            facts=tuple(self._facts[i] for i in order),
            derivations=tuple(self._derivations[i] for i in order),
            template=template,
        )


def _sort_key(fact: Fact) -> tuple[str, str, str]:
    """Total order over derived facts, so output never depends on run order."""
    return (
        fact.metric,
        fact.period_end.isoformat(),
        fact.period_start.isoformat() if fact.period_start else "",
    )


def _order_sources(sources: Sequence[Fact]) -> tuple[Fact, ...]:
    """Deduplicates and orders inputs so a derivation reads the same way twice."""
    unique: dict[str, Fact] = {}
    for source in sources:
        unique.setdefault(fact_id(source), source)
    return tuple(
        sorted(
            unique.values(),
            key=lambda f: (f.metric, f.period_end.isoformat(), f.accession_no),
        )
    )


def _period_of(sources: Sequence[Fact]) -> tuple[dt.date | None, dt.date]:
    """The period a derived figure covers, taken from what produced it.

    A figure built from any flow covers that flow's period. One built only
    from balances is an instant, dated at the latest balance sheet used.
    """
    durations = [source for source in sources if source.period_start is not None]
    if durations:
        anchor = max(durations, key=lambda f: f.period_end)
        return (anchor.period_start, anchor.period_end)
    anchor = max(sources, key=lambda f: f.period_end)
    return (None, anchor.period_end)


def _fiscal_year_of(sources: Sequence[Fact]) -> int | None:
    """The fiscal year of the latest input that carries one."""
    labelled = [source for source in sources if source.fiscal_year is not None]
    if not labelled:
        return None
    return max(labelled, key=lambda f: f.period_end).fiscal_year


def _currency_of(sources: Sequence[Fact]) -> str | None:
    """The currency the inputs are measured in, ignoring share counts."""
    for source in sources:
        if source.unit and source.unit not in NON_CURRENCY_UNIT_KEYS:
            return source.unit
    return None


def _render(
    kind: _Kind, value: float, currency: str | None
) -> tuple[float, str, str | None]:
    """Rounds a figure and renders it, returning value, display text and unit.

    Rounding happens here, at the boundary, so that two runs on the same
    filings produce byte-identical facts regardless of the order the
    intermediate arithmetic happened in.
    """
    if kind is _Kind.PERCENT:
        rounded = round(value, PERCENT_DECIMAL_PLACES)
        return (
            _zero(rounded),
            PERCENT_DISPLAY_TEMPLATE.format(value=rounded),
            UNIT_PERCENT,
        )
    if kind is _Kind.POINTS:
        rounded = round(value, PERCENT_DECIMAL_PLACES)
        return (
            _zero(rounded),
            PERCENTAGE_POINTS_DISPLAY_TEMPLATE.format(value=rounded),
            UNIT_PERCENTAGE_POINTS,
        )
    if kind is _Kind.RATIO:
        rounded = round(value, RATIO_DECIMAL_PLACES)
        return _zero(rounded), RATIO_DISPLAY_TEMPLATE.format(value=rounded), UNIT_RATIO
    if kind is _Kind.TIMES:
        rounded = round(value, RATIO_DECIMAL_PLACES)
        return _zero(rounded), TIMES_DISPLAY_TEMPLATE.format(value=rounded), UNIT_TIMES
    if kind is _Kind.DAYS:
        rounded = round(value, RATIO_DECIMAL_PLACES)
        return _zero(rounded), DAYS_DISPLAY_TEMPLATE.format(value=rounded), UNIT_DAYS
    if kind is _Kind.PER_SHARE:
        rounded = round(value, CURRENCY_DECIMAL_PLACES)
        unit = UNIT_PER_SHARE_TEMPLATE.format(currency=currency or "")
        return _zero(rounded), PER_SHARE_DISPLAY_TEMPLATE.format(value=rounded), unit

    rounded = round(value, CURRENCY_DECIMAL_PLACES)
    scaled = rounded / DISPLAY_SCALE_DIVISOR
    display = f"{scaled:,.0f}" if abs(scaled) >= 1 else f"{scaled:,.2f}"
    return _zero(rounded), display, currency


def _zero(value: float) -> float:
    """Normalises negative zero, which renders as "-0.0" and compares equal."""
    return 0.0 if value == 0 else value


# --- Arithmetic helpers -----------------------------------------------------


def _ratio(numerator: float, denominator: float) -> float | None:
    """A quotient, or None when the denominator is too near zero to mean much."""
    if abs(denominator) < ANALYSIS_ZERO_TOLERANCE:
        return None
    result = numerator / denominator
    return result if math.isfinite(result) else None


def _percent(numerator: float, denominator: float) -> float | None:
    """A quotient as a percentage."""
    quotient = _ratio(numerator, denominator)
    return None if quotient is None else quotient * PERCENT_SCALE


def _growth_percent(current: float, prior: float) -> float | None:
    """Period-on-period change as a percentage of the prior period.

    None when the base is not positive. Growth measured from a loss or from
    zero is not a percentage anyone can read: the sign flips and the magnitude
    is arbitrary, so the honest answer is that there is no growth rate.
    """
    if prior <= ANALYSIS_ZERO_TOLERANCE:
        return None
    return (current - prior) / prior * PERCENT_SCALE


def _cagr_percent(end: float, begin: float, years: int) -> float | None:
    """Compound annual growth, or None when either endpoint is not positive."""
    if begin <= ANALYSIS_ZERO_TOLERANCE or end <= ANALYSIS_ZERO_TOLERANCE:
        return None
    if years <= 0:
        return None
    return (float(np.power(end / begin, 1.0 / years)) - 1.0) * PERCENT_SCALE


@dataclass(frozen=True, slots=True)
class _Balance:
    """A balance sheet figure averaged over a period, and how it was taken."""

    value: float
    sources: tuple[Fact, ...]
    #: Reads inside a formula: "average total equity, FY2024 and FY2025".
    description: str


def _average_balance(
    workspace: _Workspace, metric: str, year: int
) -> _Balance | None:
    """Averages the opening and closing balance, or uses the close alone.

    A return divides a flow that happened across a year by a balance that
    existed at a point in it, so the average of the two year ends is the
    honest denominator. When the opening balance is not available the closing
    one is used and the formula says so, rather than silently mixing
    conventions between years.
    """
    current = workspace.lookup(metric, year)
    if current is None:
        return None

    label = workspace.label_of(metric, year)
    if metric not in _DERIVED_LABELS:
        # A filer's label is a statement heading; it reads mid-formula here.
        label = label.lower()
    prior = workspace.lookup(metric, year - 1)
    if prior is None:
        return _Balance(
            value=current[0],
            sources=current[1],
            description=f"{label} at FY{year} year end",
        )

    return _Balance(
        value=(current[0] + prior[0]) / AVERAGE_DIVISOR,
        sources=current[1] + prior[1],
        description=f"average {label}, FY{year - 1} and FY{year}",
    )


def _slug(text: str) -> str:
    """Turns a segment name into a metric path component."""
    slug = _SLUG_NON_ALNUM.sub("_", text.strip().lower()).strip("_")
    return slug or "unnamed"


# --- Derived absolutes ------------------------------------------------------


def _compute_derived_absolutes(
    workspace: _Workspace, ledger: _Ledger, groups: frozenset[MetricGroup]
) -> None:
    """Computes the intermediate figures that later ratios are built on.

    Each is put into the workspace whether or not its group is enabled, so a
    ratio that needs it can still be formed; only its emission as a fact is
    gated. That keeps the metric set a statement about what is shown, not a
    hidden constraint on what can be computed.
    """
    for year in workspace.years:
        _derive_ebitda(workspace, ledger, groups, year)
        _derive_debt(workspace, ledger, groups, year)
        _derive_tax_and_capital(workspace, ledger, groups, year)
        _derive_working_capital_absolutes(workspace, ledger, groups, year)
        _derive_free_cash_flow(workspace, ledger, groups, year)


def _derive_ebitda(
    workspace: _Workspace,
    ledger: _Ledger,
    groups: frozenset[MetricGroup],
    year: int,
) -> None:
    found = workspace.require(
        year, "income.operating_income", "income.depreciation_amortisation"
    )
    if found is None:
        return
    values, sources = found
    ebitda = (
        values["income.operating_income"]
        + values["income.depreciation_amortisation"]
    )
    workspace.put("derived.ebitda", year, ebitda, sources)

    if MetricGroup.MARGINS in groups:
        ledger.add(
            metric="derived.ebitda",
            label="EBITDA",
            kind=_Kind.CURRENCY,
            value=ebitda,
            formula=(
                f"operating income FY{year} + depreciation and amortisation "
                f"FY{year}"
            ),
            sources=sources,
        )


def _derive_debt(
    workspace: _Workspace,
    ledger: _Ledger,
    groups: frozenset[MetricGroup],
    year: int,
) -> None:
    """Total and net debt, from both halves of the debt or not at all.

    Summing only the disclosed half would understate leverage while looking
    like a complete figure, so both the current and non-current portions are
    required. Where a filer tags only one, leverage is still reported against
    total liabilities, which is always disclosed.
    """
    found = workspace.require(
        year, "balance.short_term_debt", "balance.long_term_debt"
    )
    if found is None:
        return
    values, sources = found
    total_debt = (
        values["balance.short_term_debt"] + values["balance.long_term_debt"]
    )
    workspace.put("derived.total_debt", year, total_debt, sources)

    if MetricGroup.LEVERAGE in groups:
        ledger.add(
            metric="derived.total_debt",
            label="Total debt",
            kind=_Kind.CURRENCY,
            value=total_debt,
            formula=f"short-term debt FY{year} + long-term debt FY{year}",
            sources=sources,
        )

    cash = workspace.lookup("balance.cash_and_equivalents", year)
    if cash is None:
        return
    net_debt = total_debt - cash[0]
    net_sources = sources + cash[1]
    workspace.put("derived.net_debt", year, net_debt, net_sources)

    if MetricGroup.LEVERAGE in groups:
        ledger.add(
            metric="derived.net_debt",
            label="Net debt",
            kind=_Kind.CURRENCY,
            value=net_debt,
            formula=f"total debt FY{year} − cash and equivalents FY{year}",
            sources=net_sources,
        )


def _derive_tax_and_capital(
    workspace: _Workspace,
    ledger: _Ledger,
    groups: frozenset[MetricGroup],
    year: int,
) -> None:
    """Effective tax rate, NOPAT and invested capital.

    NOPAT uses the filer's own effective rate rather than a statutory one:
    a statutory rate would be an assumption about a company, and this system
    does not make those.
    """
    taxes = workspace.require(
        year, "income.income_tax_expense", "income.pretax_income"
    )
    if taxes is not None:
        tax_values, tax_sources = taxes
        rate = _ratio(
            tax_values["income.income_tax_expense"],
            tax_values["income.pretax_income"],
        )
        if rate is not None and tax_values["income.pretax_income"] > 0:
            workspace.put("derived.effective_tax_rate", year, rate, tax_sources)
            if MetricGroup.RETURNS in groups:
                ledger.add(
                    metric="return.effective_tax_rate",
                    label="Effective tax rate",
                    kind=_Kind.PERCENT,
                    value=rate * PERCENT_SCALE,
                    formula=(
                        f"income tax expense FY{year} ÷ income before tax "
                        f"FY{year}"
                    ),
                    sources=tax_sources,
                )

    nopat_inputs = workspace.require(
        year, "income.operating_income", "derived.effective_tax_rate"
    )
    if nopat_inputs is not None:
        values, sources = nopat_inputs
        nopat = values["income.operating_income"] * (
            1.0 - values["derived.effective_tax_rate"]
        )
        workspace.put("derived.nopat", year, nopat, sources)
        if MetricGroup.RETURNS in groups:
            ledger.add(
                metric="derived.nopat",
                label="Net operating profit after tax",
                kind=_Kind.CURRENCY,
                value=nopat,
                formula=(
                    f"operating income FY{year} × (1 − effective tax rate "
                    f"FY{year})"
                ),
                sources=sources,
            )

    capital = workspace.require(
        year,
        "derived.total_debt",
        "balance.total_equity",
        "balance.cash_and_equivalents",
    )
    if capital is not None:
        values, sources = capital
        invested = (
            values["derived.total_debt"]
            + values["balance.total_equity"]
            - values["balance.cash_and_equivalents"]
        )
        workspace.put("derived.invested_capital", year, invested, sources)
        if MetricGroup.RETURNS in groups:
            ledger.add(
                metric="derived.invested_capital",
                label="Invested capital",
                kind=_Kind.CURRENCY,
                value=invested,
                formula=(
                    f"total debt FY{year} + total equity FY{year} − cash and "
                    f"equivalents FY{year}"
                ),
                sources=sources,
            )


def _derive_working_capital_absolutes(
    workspace: _Workspace,
    ledger: _Ledger,
    groups: frozenset[MetricGroup],
    year: int,
) -> None:
    found = workspace.require(
        year, "balance.current_assets", "balance.current_liabilities"
    )
    if found is None:
        return
    values, sources = found
    net_working_capital = (
        values["balance.current_assets"] - values["balance.current_liabilities"]
    )
    workspace.put(
        "derived.net_working_capital", year, net_working_capital, sources
    )

    if MetricGroup.WORKING_CAPITAL in groups:
        ledger.add(
            metric="derived.net_working_capital",
            label="Net working capital",
            kind=_Kind.CURRENCY,
            value=net_working_capital,
            formula=(
                f"total current assets FY{year} − total current liabilities "
                f"FY{year}"
            ),
            sources=sources,
        )


def _derive_free_cash_flow(
    workspace: _Workspace,
    ledger: _Ledger,
    groups: frozenset[MetricGroup],
    year: int,
) -> None:
    """Free cash flow, and free cash flow to the firm.

    Capital expenditure is tagged as a positive outflow, so it is subtracted.
    """
    found = workspace.require(
        year, "cashflow.operating", "cashflow.capital_expenditure"
    )
    if found is None:
        return
    values, sources = found
    free_cash_flow = (
        values["cashflow.operating"] - values["cashflow.capital_expenditure"]
    )
    workspace.put("derived.free_cash_flow", year, free_cash_flow, sources)

    if MetricGroup.CASH_FLOW in groups:
        ledger.add(
            metric="cashflow.free_cash_flow",
            label="Free cash flow",
            kind=_Kind.CURRENCY,
            value=free_cash_flow,
            formula=(
                f"net cash from operating activities FY{year} − capital "
                f"expenditure FY{year}"
            ),
            sources=sources,
        )

    fcff_inputs = workspace.require(
        year,
        "derived.nopat",
        "income.depreciation_amortisation",
        "cashflow.capital_expenditure",
        "derived.net_working_capital",
    )
    prior_working_capital = workspace.lookup(
        "derived.net_working_capital", year - 1
    )
    if fcff_inputs is None or prior_working_capital is None:
        return

    values, fcff_sources = fcff_inputs
    change_in_working_capital = (
        values["derived.net_working_capital"] - prior_working_capital[0]
    )
    fcff = (
        values["derived.nopat"]
        + values["income.depreciation_amortisation"]
        - values["cashflow.capital_expenditure"]
        - change_in_working_capital
    )
    all_sources = fcff_sources + prior_working_capital[1]
    workspace.put("derived.fcff", year, fcff, all_sources)

    if MetricGroup.CASH_FLOW in groups:
        ledger.add(
            metric="cashflow.fcff",
            label="Free cash flow to the firm",
            kind=_Kind.CURRENCY,
            value=fcff,
            formula=(
                f"NOPAT FY{year} + depreciation and amortisation FY{year} − "
                f"capital expenditure FY{year} − (net working capital FY{year} "
                f"− net working capital FY{year - 1})"
            ),
            sources=all_sources,
        )


# --- Growth -----------------------------------------------------------------


def _compute_growth(
    workspace: _Workspace, ledger: _Ledger, groups: frozenset[MetricGroup]
) -> None:
    """Year-on-year change and compound growth over each configured window."""
    if MetricGroup.GROWTH not in groups:
        return

    for metric in GROWTH_METRICS:
        for year in workspace.years:
            _add_yoy(workspace, ledger, metric, year)
            for window in CAGR_WINDOWS_YEARS:
                _add_cagr(workspace, ledger, metric, year, window)


def _add_yoy(
    workspace: _Workspace, ledger: _Ledger, metric: str, year: int
) -> None:
    current = workspace.lookup(metric, year)
    prior = workspace.lookup(metric, year - 1)
    if current is None or prior is None:
        return

    label = workspace.label_of(metric, year)
    inline = label.lower()
    ledger.add(
        metric=f"growth.{metric}.yoy",
        label=f"{label} growth, year on year",
        kind=_Kind.PERCENT,
        value=_growth_percent(current[0], prior[0]),
        formula=(
            f"({inline} FY{year} − {inline} FY{year - 1}) ÷ {inline} "
            f"FY{year - 1}"
        ),
        sources=current[1] + prior[1],
    )


def _add_cagr(
    workspace: _Workspace, ledger: _Ledger, metric: str, year: int, window: int
) -> None:
    current = workspace.lookup(metric, year)
    begin = workspace.lookup(metric, year - window)
    if current is None or begin is None:
        return

    label = workspace.label_of(metric, year)
    inline = label.lower()
    ledger.add(
        metric=f"growth.{metric}.cagr_{window}y",
        label=f"{label} {window}-year compound annual growth",
        kind=_Kind.PERCENT,
        value=_cagr_percent(current[0], begin[0], window),
        formula=(
            f"({inline} FY{year} ÷ {inline} FY{year - window}) ^ "
            f"(1 ÷ {window}) − 1"
        ),
        sources=current[1] + begin[1],
    )


# --- Margins ----------------------------------------------------------------

#: Margins expressed against revenue, as (metric suffix, label, numerator).
_MARGIN_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("gross", "Gross margin", "income.gross_profit"),
    ("operating", "Operating margin", "income.operating_income"),
    ("pretax", "Pre-tax margin", "income.pretax_income"),
    ("net", "Net margin", "income.net_income"),
    ("ebitda", "EBITDA margin", "derived.ebitda"),
)


def _compute_margins(
    workspace: _Workspace, ledger: _Ledger, groups: frozenset[MetricGroup]
) -> None:
    if MetricGroup.MARGINS not in groups:
        return

    for year in workspace.years:
        for suffix, label, numerator in _MARGIN_DEFINITIONS:
            found = workspace.require(year, numerator, "income.revenue")
            if found is None:
                continue
            values, sources = found
            # A derived label is already written the way it should read; a
            # filer's own label is a heading and needs lowering mid-sentence.
            numerator_label = workspace.label_of(numerator, year)
            if numerator not in _DERIVED_LABELS:
                numerator_label = numerator_label.lower()
            ledger.add(
                metric=f"margin.{suffix}",
                label=label,
                kind=_Kind.PERCENT,
                value=_percent(values[numerator], values["income.revenue"]),
                formula=f"{numerator_label} FY{year} ÷ revenue FY{year}",
                sources=sources,
            )


# --- Returns ----------------------------------------------------------------


def _compute_returns(
    workspace: _Workspace, ledger: _Ledger, groups: frozenset[MetricGroup]
) -> None:
    """Return on equity, assets and invested capital, on average balances."""
    if MetricGroup.RETURNS not in groups:
        return

    for year in workspace.years:
        _add_return(
            workspace,
            ledger,
            year,
            metric="return.roe",
            label="Return on equity",
            numerator="income.net_income",
            numerator_label="net income",
            balance="balance.total_equity",
        )
        _add_return(
            workspace,
            ledger,
            year,
            metric="return.roa",
            label="Return on assets",
            numerator="income.net_income",
            numerator_label="net income",
            balance="balance.total_assets",
        )
        _add_return(
            workspace,
            ledger,
            year,
            metric="return.roic",
            label="Return on invested capital",
            numerator="derived.nopat",
            numerator_label="NOPAT",
            balance="derived.invested_capital",
        )


def _add_return(
    workspace: _Workspace,
    ledger: _Ledger,
    year: int,
    *,
    metric: str,
    label: str,
    numerator: str,
    numerator_label: str,
    balance: str,
) -> None:
    flow = workspace.lookup(numerator, year)
    average = _average_balance(workspace, balance, year)
    if flow is None or average is None:
        return

    ledger.add(
        metric=metric,
        label=label,
        kind=_Kind.PERCENT,
        value=_percent(flow[0], average.value),
        formula=f"{numerator_label} FY{year} ÷ {average.description}",
        sources=flow[1] + average.sources,
    )


# --- DuPont -----------------------------------------------------------------


def _compute_dupont(
    workspace: _Workspace, ledger: _Ledger, groups: frozenset[MetricGroup]
) -> None:
    """Return on equity split into margin, turnover and leverage.

    The three factors multiply back to return on equity, which is why the
    product is emitted alongside them: a reader can check the decomposition
    without doing arithmetic, and so can m11.
    """
    if MetricGroup.DUPONT not in groups:
        return

    for year in workspace.years:
        income = workspace.require(year, "income.net_income", "income.revenue")
        assets = _average_balance(workspace, "balance.total_assets", year)
        equity = _average_balance(workspace, "balance.total_equity", year)
        if income is None or assets is None or equity is None:
            continue

        values, income_sources = income
        margin = _ratio(values["income.net_income"], values["income.revenue"])
        turnover = _ratio(values["income.revenue"], assets.value)
        multiplier = _ratio(assets.value, equity.value)
        if margin is None or turnover is None or multiplier is None:
            continue

        sources = income_sources + assets.sources + equity.sources

        ledger.add(
            metric="dupont.net_margin",
            label="DuPont: net margin",
            kind=_Kind.RATIO,
            value=margin,
            formula=f"net income FY{year} ÷ revenue FY{year}",
            sources=income_sources,
        )
        ledger.add(
            metric="dupont.asset_turnover",
            label="DuPont: asset turnover",
            kind=_Kind.TIMES,
            value=turnover,
            formula=f"revenue FY{year} ÷ {assets.description}",
            sources=income_sources + assets.sources,
        )
        ledger.add(
            metric="dupont.equity_multiplier",
            label="DuPont: equity multiplier",
            kind=_Kind.TIMES,
            value=multiplier,
            formula=f"{assets.description} ÷ {equity.description}",
            sources=assets.sources + equity.sources,
        )
        ledger.add(
            metric="dupont.roe",
            label="DuPont: return on equity",
            kind=_Kind.PERCENT,
            value=margin * turnover * multiplier * PERCENT_SCALE,
            formula="net margin × asset turnover × equity multiplier",
            sources=sources,
        )


# --- Liquidity --------------------------------------------------------------


def _compute_liquidity(
    workspace: _Workspace, ledger: _Ledger, groups: frozenset[MetricGroup]
) -> None:
    if MetricGroup.LIQUIDITY not in groups:
        return

    for year in workspace.years:
        current = workspace.require(
            year, "balance.current_assets", "balance.current_liabilities"
        )
        if current is not None:
            values, sources = current
            ledger.add(
                metric="liquidity.current_ratio",
                label="Current ratio",
                kind=_Kind.TIMES,
                value=_ratio(
                    values["balance.current_assets"],
                    values["balance.current_liabilities"],
                ),
                formula=(
                    f"total current assets FY{year} ÷ total current "
                    f"liabilities FY{year}"
                ),
                sources=sources,
            )

        quick = workspace.require(
            year,
            "balance.current_assets",
            "balance.inventory",
            "balance.current_liabilities",
        )
        if quick is not None:
            values, sources = quick
            ledger.add(
                metric="liquidity.quick_ratio",
                label="Quick ratio",
                kind=_Kind.TIMES,
                value=_ratio(
                    values["balance.current_assets"] - values["balance.inventory"],
                    values["balance.current_liabilities"],
                ),
                formula=(
                    f"(total current assets FY{year} − inventory FY{year}) ÷ "
                    f"total current liabilities FY{year}"
                ),
                sources=sources,
            )

        cash = workspace.require(
            year, "balance.cash_and_equivalents", "balance.current_liabilities"
        )
        if cash is not None:
            values, sources = cash
            ledger.add(
                metric="liquidity.cash_ratio",
                label="Cash ratio",
                kind=_Kind.TIMES,
                value=_ratio(
                    values["balance.cash_and_equivalents"],
                    values["balance.current_liabilities"],
                ),
                formula=(
                    f"cash and equivalents FY{year} ÷ total current "
                    f"liabilities FY{year}"
                ),
                sources=sources,
            )


# --- Leverage ---------------------------------------------------------------


def _compute_leverage(
    workspace: _Workspace, ledger: _Ledger, groups: frozenset[MetricGroup]
) -> None:
    if MetricGroup.LEVERAGE not in groups:
        return

    for year in workspace.years:
        _add_simple_ratio(
            workspace,
            ledger,
            year,
            metric="leverage.debt_to_equity",
            label="Debt to equity",
            kind=_Kind.TIMES,
            numerator="derived.total_debt",
            denominator="balance.total_equity",
            formula=f"total debt FY{year} ÷ total equity FY{year}",
        )
        _add_simple_ratio(
            workspace,
            ledger,
            year,
            metric="leverage.debt_to_assets",
            label="Debt to assets",
            kind=_Kind.RATIO,
            numerator="derived.total_debt",
            denominator="balance.total_assets",
            formula=f"total debt FY{year} ÷ total assets FY{year}",
        )
        _add_simple_ratio(
            workspace,
            ledger,
            year,
            metric="leverage.liabilities_to_equity",
            label="Liabilities to equity",
            kind=_Kind.TIMES,
            numerator="balance.total_liabilities",
            denominator="balance.total_equity",
            formula=f"total liabilities FY{year} ÷ total equity FY{year}",
        )
        _add_simple_ratio(
            workspace,
            ledger,
            year,
            metric="leverage.net_debt_to_ebitda",
            label="Net debt to EBITDA",
            kind=_Kind.TIMES,
            numerator="derived.net_debt",
            denominator="derived.ebitda",
            formula=f"net debt FY{year} ÷ EBITDA FY{year}",
        )

        coverage = workspace.require(
            year, "income.operating_income", "income.interest_expense"
        )
        if coverage is None:
            continue
        values, sources = coverage
        ledger.add(
            metric="leverage.interest_coverage",
            label="Interest coverage",
            kind=_Kind.TIMES,
            value=_ratio(
                values["income.operating_income"],
                abs(values["income.interest_expense"]),
            ),
            formula=(
                f"operating income FY{year} ÷ interest expense FY{year}, "
                f"absolute"
            ),
            sources=sources,
        )


def _add_simple_ratio(
    workspace: _Workspace,
    ledger: _Ledger,
    year: int,
    *,
    metric: str,
    label: str,
    kind: _Kind,
    numerator: str,
    denominator: str,
    formula: str,
) -> None:
    """Emits one quotient of two figures for one year, when both are present.

    The kind decides the scale as well as the rendering: a quotient asked for
    as a percentage is scaled here, so no caller has to remember to do it.
    """
    found = workspace.require(year, numerator, denominator)
    if found is None:
        return

    values, sources = found
    scale = _percent if kind is _Kind.PERCENT else _ratio
    ledger.add(
        metric=metric,
        label=label,
        kind=kind,
        value=scale(values[numerator], values[denominator]),
        formula=formula,
        sources=sources,
    )


# --- Cash flow --------------------------------------------------------------


def _compute_cash_flow(
    workspace: _Workspace, ledger: _Ledger, groups: frozenset[MetricGroup]
) -> None:
    if MetricGroup.CASH_FLOW not in groups:
        return

    for year in workspace.years:
        intensity = workspace.require(
            year, "cashflow.capital_expenditure", "income.revenue"
        )
        if intensity is not None:
            values, sources = intensity
            ledger.add(
                metric="cashflow.capex_intensity",
                label="Capital expenditure intensity",
                kind=_Kind.PERCENT,
                value=_percent(
                    values["cashflow.capital_expenditure"],
                    values["income.revenue"],
                ),
                formula=f"capital expenditure FY{year} ÷ revenue FY{year}",
                sources=sources,
            )

        conversion = workspace.require(
            year, "derived.free_cash_flow", "income.net_income"
        )
        if conversion is not None:
            values, sources = conversion
            ledger.add(
                metric="cashflow.fcf_conversion",
                label="Free cash flow conversion",
                kind=_Kind.PERCENT,
                value=_percent(
                    values["derived.free_cash_flow"], values["income.net_income"]
                ),
                formula=f"free cash flow FY{year} ÷ net income FY{year}",
                sources=sources,
            )

        quality = workspace.require(
            year, "cashflow.operating", "income.net_income"
        )
        if quality is not None:
            values, sources = quality
            ledger.add(
                metric="cashflow.earnings_quality",
                label="Operating cash flow to net income",
                kind=_Kind.TIMES,
                value=_ratio(
                    values["cashflow.operating"], values["income.net_income"]
                ),
                formula=(
                    f"net cash from operating activities FY{year} ÷ net "
                    f"income FY{year}"
                ),
                sources=sources,
            )


# --- Working capital cycle --------------------------------------------------


def _compute_working_capital(
    workspace: _Workspace, ledger: _Ledger, groups: frozenset[MetricGroup]
) -> None:
    """Days sales outstanding, days inventory, days payable and their cycle.

    Each uses the closing balance against the year's flow. The cycle is the
    sum of the first two less the third: how long cash is tied up between
    paying a supplier and being paid by a customer.
    """
    if MetricGroup.WORKING_CAPITAL not in groups:
        return

    for year in workspace.years:
        days_sales = _days_of(
            workspace,
            ledger,
            year,
            metric="working_capital.dso",
            label="Days sales outstanding",
            balance="balance.accounts_receivable",
            flow="income.revenue",
            formula=(
                f"accounts receivable FY{year} ÷ revenue FY{year} × "
                f"{DAYS_IN_YEAR}"
            ),
        )
        days_inventory = _days_of(
            workspace,
            ledger,
            year,
            metric="working_capital.dio",
            label="Days inventory outstanding",
            balance="balance.inventory",
            flow="income.cost_of_revenue",
            formula=(
                f"inventory FY{year} ÷ cost of revenue FY{year} × "
                f"{DAYS_IN_YEAR}"
            ),
        )
        days_payable = _days_of(
            workspace,
            ledger,
            year,
            metric="working_capital.dpo",
            label="Days payable outstanding",
            balance="balance.accounts_payable",
            flow="income.cost_of_revenue",
            formula=(
                f"accounts payable FY{year} ÷ cost of revenue FY{year} × "
                f"{DAYS_IN_YEAR}"
            ),
        )

        if days_sales is None or days_inventory is None or days_payable is None:
            continue

        ledger.add(
            metric="working_capital.cash_conversion_cycle",
            label="Cash conversion cycle",
            kind=_Kind.DAYS,
            value=days_sales[0] + days_inventory[0] - days_payable[0],
            formula=(
                "days sales outstanding + days inventory outstanding − days "
                "payable outstanding"
            ),
            sources=days_sales[1] + days_inventory[1] + days_payable[1],
        )


def _days_of(
    workspace: _Workspace,
    ledger: _Ledger,
    year: int,
    *,
    metric: str,
    label: str,
    balance: str,
    flow: str,
    formula: str,
) -> tuple[float, tuple[Fact, ...]] | None:
    """Emits one days-outstanding figure and returns it for the cycle sum."""
    found = workspace.require(year, balance, flow)
    if found is None:
        return None
    values, sources = found

    quotient = _ratio(values[balance], values[flow])
    if quotient is None:
        return None
    days = quotient * DAYS_IN_YEAR

    ledger.add(
        metric=metric,
        label=label,
        kind=_Kind.DAYS,
        value=days,
        formula=formula,
        sources=sources,
    )
    return (days, sources)


# --- Common-size statements -------------------------------------------------


def _compute_common_size(
    workspace: _Workspace, ledger: _Ledger, groups: frozenset[MetricGroup]
) -> None:
    """Every statement line as a percentage of its statement's base.

    Vectorised across the whole panel: the income statement is divided by
    revenue and the balance sheet by total assets in one operation each, which
    is both faster and less error-prone than a line-by-line loop.
    """
    if MetricGroup.COMMON_SIZE not in groups:
        return

    _common_size_block(
        workspace,
        ledger,
        metrics=COMMON_SIZE_INCOME_METRICS,
        base="income.revenue",
        base_label="revenue",
    )
    _common_size_block(
        workspace,
        ledger,
        metrics=COMMON_SIZE_BALANCE_METRICS,
        base="balance.total_assets",
        base_label="total assets",
    )


def _common_size_block(
    workspace: _Workspace,
    ledger: _Ledger,
    *,
    metrics: Sequence[str],
    base: str,
    base_label: str,
) -> None:
    frame = workspace.panel.frame
    if base not in frame.columns:
        return

    present = [metric for metric in metrics if metric in frame.columns]
    if not present:
        return

    # A base too near zero is blanked rather than divided by, so the whole
    # statement is scaled in one operation without a guard per line.
    base_series = frame[base].where(frame[base].abs() >= ANALYSIS_ZERO_TOLERANCE)
    shares = (frame[present].div(base_series, axis=0) * PERCENT_SCALE).to_numpy(
        dtype=float
    )

    for column, metric in enumerate(present):
        for row, year in enumerate(workspace.years):
            value = float(shares[row, column])
            if math.isnan(value):
                continue
            found = workspace.require(year, metric, base)
            if found is None:
                continue
            _, sources = found
            label = workspace.label_of(metric, year)
            ledger.add(
                metric=f"common_size.{metric}",
                label=f"{label} as a share of {base_label}",
                kind=_Kind.PERCENT,
                value=value,
                formula=f"{label.lower()} FY{year} ÷ {base_label} FY{year}",
                sources=sources,
            )


# --- Margin bridge ----------------------------------------------------------


def _compute_margin_bridge(
    workspace: _Workspace, ledger: _Ledger, groups: frozenset[MetricGroup]
) -> None:
    """Splits the change in operating margin into gross margin and opex.

    The two contributions sum to the change, by construction. Where operating
    expenses are not separately tagged they are taken as gross profit less
    operating income, which is what they are; the formula says so. Anything
    the identity does not close — other operating income, a restructuring line
    sitting outside both — is emitted as a residual rather than absorbed
    silently into one of the two.
    """
    if MetricGroup.MARGIN_BRIDGE not in groups:
        return

    for year in workspace.years:
        current = _margin_set(workspace, year)
        prior = _margin_set(workspace, year - 1)
        if current is None or prior is None:
            continue

        gross_change = (current.gross - prior.gross) * PERCENT_SCALE
        opex_contribution = -(current.opex - prior.opex) * PERCENT_SCALE
        operating_change = (current.operating - prior.operating) * PERCENT_SCALE
        residual = operating_change - gross_change - opex_contribution
        sources = current.sources + prior.sources

        ledger.add(
            metric="bridge.operating_margin_change",
            label="Operating margin change",
            kind=_Kind.POINTS,
            value=operating_change,
            formula=f"operating margin FY{year} − operating margin FY{year - 1}",
            sources=sources,
        )
        ledger.add(
            metric="bridge.gross_margin_contribution",
            label="Operating margin change from gross margin",
            kind=_Kind.POINTS,
            value=gross_change,
            formula=f"gross margin FY{year} − gross margin FY{year - 1}",
            sources=sources,
        )
        ledger.add(
            metric="bridge.opex_contribution",
            label="Operating margin change from operating expenses",
            kind=_Kind.POINTS,
            value=opex_contribution,
            formula=(
                f"−(operating expense ratio FY{year} − operating expense "
                f"ratio FY{year - 1}), {current.opex_basis}"
            ),
            sources=sources,
        )

        if abs(residual) > RESIDUAL_TOLERANCE_POINTS:
            ledger.add(
                metric="bridge.other_contribution",
                label="Operating margin change not explained by the bridge",
                kind=_Kind.POINTS,
                value=residual,
                formula=(
                    "operating margin change − gross margin contribution − "
                    "operating expense contribution"
                ),
                sources=sources,
            )


@dataclass(frozen=True, slots=True)
class _MarginSet:
    """One year's margins as fractions, with how the opex ratio was formed."""

    gross: float
    opex: float
    operating: float
    opex_basis: str
    sources: tuple[Fact, ...]


def _margin_set(workspace: _Workspace, year: int) -> _MarginSet | None:
    found = workspace.require(
        year, "income.revenue", "income.gross_profit", "income.operating_income"
    )
    if found is None:
        return None
    values, sources = found
    revenue = values["income.revenue"]

    gross = _ratio(values["income.gross_profit"], revenue)
    operating = _ratio(values["income.operating_income"], revenue)
    if gross is None or operating is None:
        return None

    reported_opex = workspace.lookup("income.operating_expenses", year)
    if reported_opex is not None:
        opex_amount = reported_opex[0]
        basis = "as reported"
        sources = sources + reported_opex[1]
    else:
        opex_amount = (
            values["income.gross_profit"] - values["income.operating_income"]
        )
        basis = "operating expenses taken as gross profit less operating income"

    opex = _ratio(opex_amount, revenue)
    if opex is None:
        return None

    return _MarginSet(
        gross=gross,
        opex=opex,
        operating=operating,
        opex_basis=basis,
        sources=sources,
    )


# --- Per-share and shareholder returns --------------------------------------


def _compute_per_share(
    workspace: _Workspace, ledger: _Ledger, groups: frozenset[MetricGroup]
) -> None:
    """Per-share figures and what the company returned to shareholders.

    Earnings per share is recomputed rather than passed through. The filer's
    own basic and diluted figures are already facts; computing them again from
    net income and the weighted share count gives a reader something to
    compare them against, which is where preferred dividends and discontinued
    operations show up.
    """
    if MetricGroup.PER_SHARE not in groups:
        return

    for year in workspace.years:
        _add_simple_ratio(
            workspace,
            ledger,
            year,
            metric="per_share.eps_basic",
            label="Earnings per share, basic",
            kind=_Kind.PER_SHARE,
            numerator="income.net_income",
            denominator="income.shares_basic",
            formula=(
                f"net income FY{year} ÷ basic weighted average shares FY{year}"
            ),
        )
        _add_simple_ratio(
            workspace,
            ledger,
            year,
            metric="per_share.eps_diluted",
            label="Earnings per share, diluted",
            kind=_Kind.PER_SHARE,
            numerator="income.net_income",
            denominator="income.shares_diluted",
            formula=(
                f"net income FY{year} ÷ diluted weighted average shares "
                f"FY{year}"
            ),
        )
        _add_simple_ratio(
            workspace,
            ledger,
            year,
            metric="per_share.book_value",
            label="Book value per share",
            kind=_Kind.PER_SHARE,
            numerator="balance.total_equity",
            denominator="balance.shares_outstanding",
            formula=f"total equity FY{year} ÷ shares outstanding FY{year}",
        )
        _add_simple_ratio(
            workspace,
            ledger,
            year,
            metric="per_share.dividend",
            label="Dividend per share, from cash paid",
            kind=_Kind.PER_SHARE,
            numerator="cashflow.dividends_paid",
            denominator="income.shares_basic",
            formula=(
                f"dividends paid FY{year} ÷ basic weighted average shares "
                f"FY{year}"
            ),
        )
        _add_simple_ratio(
            workspace,
            ledger,
            year,
            metric="shareholder.payout_ratio",
            label="Dividend payout ratio",
            kind=_Kind.PERCENT,
            numerator="cashflow.dividends_paid",
            denominator="income.net_income",
            formula=f"dividends paid FY{year} ÷ net income FY{year}",
        )
        _add_simple_ratio(
            workspace,
            ledger,
            year,
            metric="shareholder.buyback_ratio",
            label="Buyback ratio",
            kind=_Kind.PERCENT,
            numerator="cashflow.share_repurchases",
            denominator="income.net_income",
            formula=f"share repurchases FY{year} ÷ net income FY{year}",
        )

        _add_total_payout(workspace, ledger, year)
        _add_share_count_change(workspace, ledger, year)


def _add_total_payout(
    workspace: _Workspace, ledger: _Ledger, year: int
) -> None:
    found = workspace.require(
        year,
        "cashflow.dividends_paid",
        "cashflow.share_repurchases",
        "income.net_income",
    )
    if found is None:
        return
    values, sources = found
    ledger.add(
        metric="shareholder.total_payout_ratio",
        label="Total payout ratio",
        kind=_Kind.PERCENT,
        value=_percent(
            values["cashflow.dividends_paid"]
            + values["cashflow.share_repurchases"],
            values["income.net_income"],
        ),
        formula=(
            f"(dividends paid FY{year} + share repurchases FY{year}) ÷ net "
            f"income FY{year}"
        ),
        sources=sources,
    )


def _add_share_count_change(
    workspace: _Workspace, ledger: _Ledger, year: int
) -> None:
    """Change in the diluted share count. Negative means the count shrank."""
    current = workspace.lookup("income.shares_diluted", year)
    prior = workspace.lookup("income.shares_diluted", year - 1)
    if current is None or prior is None:
        return

    ledger.add(
        metric="shareholder.share_count_change",
        label="Diluted share count change",
        kind=_Kind.PERCENT,
        value=_growth_percent(current[0], prior[0]),
        formula=(
            f"(diluted weighted average shares FY{year} − diluted weighted "
            f"average shares FY{year - 1}) ÷ diluted weighted average shares "
            f"FY{year - 1}"
        ),
        sources=current[1] + prior[1],
    )


# --- Sector: banks ----------------------------------------------------------


def _compute_bank(
    workspace: _Workspace, ledger: _Ledger, groups: frozenset[MetricGroup]
) -> None:
    """Net interest margin, capital adequacy, efficiency and credit cost."""
    if MetricGroup.BANK not in groups:
        return

    for year in workspace.years:
        earning_assets = _average_balance(
            workspace, "bank.interest_earning_assets", year
        )
        net_interest = workspace.lookup("bank.net_interest_income", year)
        if earning_assets is not None and net_interest is not None:
            ledger.add(
                metric="bank.net_interest_margin",
                label="Net interest margin",
                kind=_Kind.PERCENT,
                value=_percent(net_interest[0], earning_assets.value),
                formula=(
                    f"net interest income FY{year} ÷ {earning_assets.description}"
                ),
                sources=net_interest[1] + earning_assets.sources,
            )

        _add_simple_ratio(
            workspace,
            ledger,
            year,
            metric="bank.cet1_ratio",
            label="Common equity tier 1 ratio",
            kind=_Kind.PERCENT,
            numerator="bank.cet1_capital",
            denominator="bank.risk_weighted_assets",
            formula=(
                f"common equity tier 1 capital FY{year} ÷ risk-weighted "
                f"assets FY{year}"
            ),
        )
        _add_simple_ratio(
            workspace,
            ledger,
            year,
            metric="bank.loan_to_deposit",
            label="Loan to deposit ratio",
            kind=_Kind.PERCENT,
            numerator="bank.total_loans",
            denominator="bank.total_deposits",
            formula=f"loans and leases FY{year} ÷ total deposits FY{year}",
        )

        efficiency = workspace.require(
            year,
            "bank.noninterest_expense",
            "bank.net_interest_income",
            "bank.noninterest_income",
        )
        if efficiency is not None:
            values, sources = efficiency
            ledger.add(
                metric="bank.efficiency_ratio",
                label="Efficiency ratio",
                kind=_Kind.PERCENT,
                value=_percent(
                    values["bank.noninterest_expense"],
                    values["bank.net_interest_income"]
                    + values["bank.noninterest_income"],
                ),
                formula=(
                    f"non-interest expense FY{year} ÷ (net interest income "
                    f"FY{year} + non-interest income FY{year})"
                ),
                sources=sources,
            )

        provision = workspace.lookup("bank.provision_for_credit_losses", year)
        loans = _average_balance(workspace, "bank.total_loans", year)
        if provision is not None and loans is not None:
            ledger.add(
                metric="bank.provision_to_loans",
                label="Provision to average loans",
                kind=_Kind.PERCENT,
                value=_percent(provision[0], loans.value),
                formula=(
                    f"provision for credit losses FY{year} ÷ {loans.description}"
                ),
                sources=provision[1] + loans.sources,
            )


# --- Sector: REITs ----------------------------------------------------------


def _compute_reit(
    workspace: _Workspace, ledger: _Ledger, groups: frozenset[MetricGroup]
) -> None:
    """Funds from operations and what follows from it.

    FFO follows the NAREIT definition: net income with real estate
    depreciation added back and gains on property sales taken out, because
    property does not in fact depreciate the way the income statement says it
    does. Which depreciation figure was used is stated in the formula — a
    filer that does not tag real estate depreciation separately has its total
    depreciation used instead, and the reader is told so rather than left to
    assume.
    """
    if MetricGroup.REIT not in groups:
        return

    for year in workspace.years:
        ffo = _funds_from_operations(workspace, year)
        if ffo is not None:
            workspace.put("derived.ffo", year, ffo[0], ffo[1])
            ledger.add(
                metric="reit.ffo",
                label="Funds from operations",
                kind=_Kind.CURRENCY,
                value=ffo[0],
                formula=ffo[2],
                sources=ffo[1],
            )

        affo = _adjusted_funds_from_operations(workspace, year)
        if affo is not None:
            workspace.put("derived.affo", year, affo[0], affo[1])
            ledger.add(
                metric="reit.affo",
                label="Adjusted funds from operations",
                kind=_Kind.CURRENCY,
                value=affo[0],
                formula=affo[2],
                sources=affo[1],
            )

        _add_simple_ratio(
            workspace,
            ledger,
            year,
            metric="reit.ffo_per_share",
            label="Funds from operations per share",
            kind=_Kind.PER_SHARE,
            numerator="derived.ffo",
            denominator="income.shares_diluted",
            formula=(
                f"funds from operations FY{year} ÷ diluted weighted average "
                f"shares FY{year}"
            ),
        )
        _add_simple_ratio(
            workspace,
            ledger,
            year,
            metric="reit.affo_per_share",
            label="Adjusted funds from operations per share",
            kind=_Kind.PER_SHARE,
            numerator="derived.affo",
            denominator="income.shares_diluted",
            formula=(
                f"adjusted funds from operations FY{year} ÷ diluted weighted "
                f"average shares FY{year}"
            ),
        )
        _add_simple_ratio(
            workspace,
            ledger,
            year,
            metric="reit.ffo_payout_ratio",
            label="Funds from operations payout ratio",
            kind=_Kind.PERCENT,
            numerator="cashflow.dividends_paid",
            denominator="derived.ffo",
            formula=(
                f"dividends paid FY{year} ÷ funds from operations FY{year}"
            ),
        )

        _add_nav_per_share(workspace, ledger, year)


def _funds_from_operations(
    workspace: _Workspace, year: int
) -> tuple[float, tuple[Fact, ...], str] | None:
    """Net income plus real estate depreciation, less gains on property sales.

    Impairment and gains are added only where the filer reports them; the
    formula names exactly which terms went in, so a figure built from fewer
    terms is never mistaken for one built from all of them.
    """
    net_income = workspace.lookup("income.net_income", year)
    if net_income is None:
        return None

    depreciation = workspace.lookup("reit.real_estate_depreciation", year)
    depreciation_basis = "real estate depreciation and amortisation"
    if depreciation is None:
        depreciation = workspace.lookup("income.depreciation_amortisation", year)
        depreciation_basis = (
            "total depreciation and amortisation, real estate depreciation not "
            "separately tagged"
        )
    if depreciation is None:
        return None

    value = net_income[0] + depreciation[0]
    sources = net_income[1] + depreciation[1]
    terms = [f"net income FY{year}", f"{depreciation_basis} FY{year}"]

    gains = workspace.lookup("reit.gains_on_property_sales", year)
    if gains is not None:
        value -= gains[0]
        sources = sources + gains[1]
        terms.append(f"− gains on sales of real estate FY{year}")

    impairment = workspace.lookup("reit.impairment_of_real_estate", year)
    if impairment is not None:
        value += impairment[0]
        sources = sources + impairment[1]
        terms.append(f"+ impairment of real estate FY{year}")

    formula = f"{terms[0]} + {terms[1]}" + "".join(
        f" {term}" for term in terms[2:]
    )
    return (value, sources, formula)


def _adjusted_funds_from_operations(
    workspace: _Workspace, year: int
) -> tuple[float, tuple[Fact, ...], str] | None:
    """FFO less recurring capital expenditure and the straight-line rent accrual."""
    found = workspace.require(
        year, "derived.ffo", "reit.recurring_capex", "reit.straight_line_rent"
    )
    if found is None:
        return None
    values, sources = found
    value = (
        values["derived.ffo"]
        - values["reit.recurring_capex"]
        - values["reit.straight_line_rent"]
    )
    formula = (
        f"funds from operations FY{year} − recurring capital expenditure "
        f"FY{year} − straight-line rent adjustment FY{year}"
    )
    return (value, sources, formula)


def _add_nav_per_share(
    workspace: _Workspace, ledger: _Ledger, year: int
) -> None:
    """Net asset value per share, from disclosed fair value only.

    Never from a capitalisation rate applied to net operating income. That
    would be this system forming a view on what the portfolio is worth, which
    is exactly the kind of estimate it does not make.
    """
    found = workspace.require(
        year,
        "reit.real_estate_fair_value",
        "balance.total_liabilities",
        "balance.shares_outstanding",
    )
    if found is None:
        return
    values, sources = found
    ledger.add(
        metric="reit.nav_per_share",
        label="Net asset value per share",
        kind=_Kind.PER_SHARE,
        value=_ratio(
            values["reit.real_estate_fair_value"]
            - values["balance.total_liabilities"],
            values["balance.shares_outstanding"],
        ),
        formula=(
            f"(real estate at fair value FY{year} − total liabilities "
            f"FY{year}) ÷ shares outstanding FY{year}"
        ),
        sources=sources,
    )


# --- Sector: insurance ------------------------------------------------------


def _compute_insurance(
    workspace: _Workspace, ledger: _Ledger, groups: frozenset[MetricGroup]
) -> None:
    """Loss, expense and combined ratios.

    A combined ratio above one hundred per cent means the underwriting book
    lost money before investment income, which is the single number the
    industry is read on.
    """
    if MetricGroup.INSURANCE not in groups:
        return

    for year in workspace.years:
        loss = workspace.require(
            year, "insurance.losses_incurred", "insurance.earned_premiums"
        )
        expense = workspace.require(
            year, "insurance.underwriting_expenses", "insurance.earned_premiums"
        )

        loss_ratio: float | None = None
        if loss is not None:
            values, sources = loss
            loss_ratio = _percent(
                values["insurance.losses_incurred"],
                values["insurance.earned_premiums"],
            )
            ledger.add(
                metric="insurance.loss_ratio",
                label="Loss ratio",
                kind=_Kind.PERCENT,
                value=loss_ratio,
                formula=(
                    f"losses and loss adjustment expenses FY{year} ÷ net "
                    f"premiums earned FY{year}"
                ),
                sources=sources,
            )

        expense_ratio: float | None = None
        if expense is not None:
            values, sources = expense
            expense_ratio = _percent(
                values["insurance.underwriting_expenses"],
                values["insurance.earned_premiums"],
            )
            ledger.add(
                metric="insurance.expense_ratio",
                label="Expense ratio",
                kind=_Kind.PERCENT,
                value=expense_ratio,
                formula=(
                    f"underwriting expenses FY{year} ÷ net premiums earned "
                    f"FY{year}"
                ),
                sources=sources,
            )

        if loss is None or expense is None:
            continue
        if loss_ratio is None or expense_ratio is None:
            continue

        ledger.add(
            metric="insurance.combined_ratio",
            label="Combined ratio",
            kind=_Kind.PERCENT,
            value=loss_ratio + expense_ratio,
            formula="loss ratio + expense ratio",
            sources=loss[1] + expense[1],
        )


# --- Growth attribution -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class _SegmentSeries:
    """One segment's revenue across the two periods being compared."""

    axis: str
    member: str
    label: str
    current: Fact
    prior: Fact


def _compute_attribution(
    facts: Sequence[Fact],
    workspace: _Workspace,
    ledger: _Ledger,
    groups: frozenset[MetricGroup],
) -> None:
    """Each segment's and geography's share of the change in total revenue.

    Contributions are in percentage points of prior-period consolidated
    revenue, so they add up to the consolidated growth rate. What they do not
    account for — an unreported segment, eliminations, rounding in the filing —
    is emitted as an unattributed residual rather than spread across the
    segments that were reported.

    Each axis is attributed on its own. A filer commonly reports the same
    revenue twice, once by product and once by region, and each breakdown
    accounts for the whole of it: adding a product contribution to a regional
    one would count the same revenue twice and make the residual meaningless.
    That is also why the axis is part of the metric path — two axes can both
    be segmentations, and their members must never share a name.
    """
    if MetricGroup.ATTRIBUTION not in groups:
        return

    segments = [
        fact
        for fact in facts
        if fact.metric == SEGMENT_METRIC
        and fact.value is not None
        and fact.segment_axis is not None
        and fact.segment_member is not None
        and fact.period_start is not None
    ]
    if not segments:
        return

    periods = sorted({fact.period_end for fact in segments}, reverse=True)
    if len(periods) < 2:
        return

    current_end = periods[0]
    prior_end = _prior_annual_end(current_end, periods[1:])
    if prior_end is None:
        return

    series = _pair_segments(segments, current_end, prior_end)
    if not series:
        return

    base = _consolidated_revenue(facts, prior_end)
    current_total = _consolidated_revenue(facts, current_end)
    if base is None or base.value is None or base.value <= 0:
        return

    by_axis: dict[str, list[_SegmentSeries]] = {}
    for entry in series:
        by_axis.setdefault(entry.axis, []).append(entry)

    for axis in sorted(by_axis):
        _attribute_one_axis(
            ledger,
            entries=by_axis[axis],
            axis=axis,
            base=base,
            current_total=current_total,
            current_end=current_end,
            prior_end=prior_end,
        )


def _attribute_one_axis(
    ledger: _Ledger,
    *,
    entries: Sequence[_SegmentSeries],
    axis: str,
    base: Fact,
    current_total: Fact | None,
    current_end: dt.date,
    prior_end: dt.date,
) -> None:
    """Attributes the revenue change across the members of a single axis."""
    if base.value is None or base.value <= 0:
        return

    prefix = f"attribution.{_axis_kind(axis)}.{_axis_slug(axis)}"
    contributions = 0.0

    for entry in entries:
        current_value = entry.current.value
        prior_value = entry.prior.value
        if current_value is None or prior_value is None:
            continue

        contribution = (current_value - prior_value) / base.value * PERCENT_SCALE
        contributions += contribution

        ledger.add(
            metric=f"{prefix}.{_slug(entry.label)}.revenue",
            label=f"{entry.label} contribution to revenue growth",
            kind=_Kind.POINTS,
            value=contribution,
            formula=(
                f"({entry.label} revenue {current_end.isoformat()} − "
                f"{entry.label} revenue {prior_end.isoformat()}) ÷ total "
                f"revenue {prior_end.isoformat()}"
            ),
            sources=(entry.current, entry.prior, base),
        )

    if current_total is None or current_total.value is None:
        return

    total_growth = (current_total.value - base.value) / base.value * PERCENT_SCALE
    residual = total_growth - contributions
    if abs(residual) <= RESIDUAL_TOLERANCE_POINTS:
        return

    ledger.add(
        metric=f"{prefix}.unattributed.revenue",
        label="Revenue growth not attributed to a reported segment",
        kind=_Kind.POINTS,
        value=residual,
        formula=(
            "total revenue growth − the sum of the contributions reported "
            "along this breakdown"
        ),
        sources=(
            current_total,
            base,
            *(entry.current for entry in entries),
            *(entry.prior for entry in entries),
        ),
    )


def _prior_annual_end(
    current: dt.date, candidates: Sequence[dt.date]
) -> dt.date | None:
    """The comparison period end: roughly one year before `current`."""
    for candidate in candidates:
        days = (current - candidate).days
        if ANNUAL_PERIOD_MIN_DAYS <= days <= ANNUAL_PERIOD_MAX_DAYS:
            return candidate
    return None


def _pair_segments(
    segments: Sequence[Fact], current_end: dt.date, prior_end: dt.date
) -> tuple[_SegmentSeries, ...]:
    """Matches each segment's current-period figure with its prior-period one."""
    by_key: dict[tuple[str, str, dt.date], Fact] = {}
    for fact in segments:
        if fact.segment_axis is None or fact.segment_member is None:
            continue
        key = (fact.segment_axis, fact.segment_member, fact.period_end)
        incumbent = by_key.get(key)
        if incumbent is None or _supersedes(fact, incumbent):
            by_key[key] = fact

    paired: list[_SegmentSeries] = []
    members = sorted({(axis, member) for axis, member, _ in by_key})
    for axis, member in members:
        current = by_key.get((axis, member, current_end))
        prior = by_key.get((axis, member, prior_end))
        if current is None or prior is None:
            continue
        paired.append(
            _SegmentSeries(
                axis=axis,
                member=member,
                label=current.segment_label or member,
                current=current,
                prior=prior,
            )
        )
    return tuple(paired)


def _axis_kind(axis: str) -> str:
    """Whether an axis breaks revenue down by geography or by segment."""
    normalised = axis.strip().lower()
    geographic = {value.strip().lower() for value in GEOGRAPHIC_SEGMENT_AXES}
    return "geography" if normalised in geographic else "segment"


def _axis_slug(axis: str) -> str:
    """The axis as a metric path component.

    "srt:ProductOrServiceAxis" becomes "product_or_service". Present in the
    path because two different axes can both be segmentations, and their
    members would otherwise collide.
    """
    local = _QNAME_SPLIT.split(axis.strip())[-1]
    local = local.removesuffix("Axis").removeprefix("Statement")
    return _slug(_CAMEL_BOUNDARY.sub(" ", local))


def _consolidated_revenue(
    facts: Sequence[Fact], period_end: dt.date
) -> Fact | None:
    """The consolidated revenue figure for one period end."""
    candidates = [
        fact
        for fact in facts
        if fact.metric == "income.revenue"
        and fact.segment_member is None
        and fact.value is not None
        and fact.period_end == period_end
        and _is_annual_or_instant(fact)
        and fact.period_start is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda f: (f.filed_date, f.accession_no))


# --- Trailing twelve months -------------------------------------------------


def _compute_ttm(
    facts: Sequence[Fact], ledger: _Ledger, groups: frozenset[MetricGroup]
) -> None:
    """A trailing twelve months for each configured flow metric.

    Two routes, in order of preference: sum the latest four quarters, or take
    the last full year, add the current year to date and take out the same
    period of the prior year. Both give an exact twelve months; neither
    annualises a part-year, which would be an estimate.
    """
    if MetricGroup.TTM not in groups:
        return

    for metric in TTM_METRICS:
        periods = _duration_facts(facts, metric)
        if not periods:
            continue

        computed = _ttm_from_quarters(periods) or _ttm_from_year_to_date(periods)
        if computed is None:
            continue

        ledger.add(
            metric=f"ttm.{metric}",
            label=f"{computed.sources[0].label}, trailing twelve months",
            kind=_Kind.CURRENCY,
            value=computed.value,
            formula=computed.formula,
            sources=computed.sources,
            period=(computed.period_start, computed.period_end),
        )


def _duration_facts(facts: Sequence[Fact], metric: str) -> tuple[Fact, ...]:
    """Every period reported for one flow metric, latest filing per period."""
    by_period: dict[tuple[dt.date, dt.date], Fact] = {}
    for fact in facts:
        if fact.metric != metric or fact.value is None:
            continue
        if fact.segment_member is not None or fact.period_start is None:
            continue
        key = (fact.period_start, fact.period_end)
        incumbent = by_period.get(key)
        if incumbent is None or _supersedes(fact, incumbent):
            by_period[key] = fact
    return tuple(
        sorted(by_period.values(), key=lambda f: (f.period_end, f.period_start))
    )


def _period_days(fact: Fact) -> int:
    """Length of a duration fact in days. Zero for an instant."""
    if fact.period_start is None:
        return 0
    return (fact.period_end - fact.period_start).days


@dataclass(frozen=True, slots=True)
class _TrailingTwelveMonths:
    """Twelve months of a flow, and the exact window it covers.

    The window is stated rather than inferred: it is not the period of any one
    figure that went into the sum, and a reader who cannot see which twelve
    months a figure covers cannot use it.
    """

    value: float
    sources: tuple[Fact, ...]
    formula: str
    period_start: dt.date
    period_end: dt.date


def _ttm_from_quarters(
    periods: Sequence[Fact],
) -> _TrailingTwelveMonths | None:
    """Sums the latest four quarters, when four contiguous ones exist."""
    quarters = [
        fact
        for fact in periods
        if QUARTERLY_PERIOD_MIN_DAYS <= _period_days(fact) <= QUARTERLY_PERIOD_MAX_DAYS
    ]
    if len(quarters) < TTM_QUARTER_COUNT:
        return None

    latest = sorted(quarters, key=lambda f: f.period_end, reverse=True)[
        :TTM_QUARTER_COUNT
    ]
    for newer, older in zip(latest, latest[1:], strict=False):
        if newer.period_start is None:
            return None
        gap = (newer.period_start - older.period_end).days
        if abs(gap - ADJACENT_PERIOD_GAP_DAYS) > TTM_QUARTER_ADJACENCY_TOLERANCE_DAYS:
            return None

    oldest = latest[-1]
    newest = latest[0]
    if oldest.period_start is None:
        return None
    span = (newest.period_end - oldest.period_start).days
    if not ANNUAL_PERIOD_MIN_DAYS <= span <= ANNUAL_PERIOD_MAX_DAYS:
        return None

    total = sum(fact.value or 0.0 for fact in latest)
    ordered = tuple(sorted(latest, key=lambda f: f.period_end))
    formula = (
        f"sum of the {TTM_QUARTER_COUNT} quarters ended "
        + ", ".join(fact.period_end.isoformat() for fact in ordered)
    )
    return _TrailingTwelveMonths(
        value=total,
        sources=ordered,
        formula=formula,
        period_start=oldest.period_start,
        period_end=newest.period_end,
    )


def _ttm_from_year_to_date(
    periods: Sequence[Fact],
) -> _TrailingTwelveMonths | None:
    """Last full year plus the current year to date, less the prior year to date."""
    annuals = [
        fact
        for fact in periods
        if ANNUAL_PERIOD_MIN_DAYS <= _period_days(fact) <= ANNUAL_PERIOD_MAX_DAYS
    ]
    if not annuals:
        return None

    year = max(annuals, key=lambda f: f.period_end)
    if year.period_start is None or year.value is None:
        return None

    current = _year_to_date_after(periods, year.period_end)
    if current is None:
        return None

    prior = _matching_year_to_date(periods, year.period_start, current)
    if prior is None or current.value is None or prior.value is None:
        return None

    total = year.value + current.value - prior.value
    formula = (
        f"the year ended {year.period_end.isoformat()} + the period ended "
        f"{current.period_end.isoformat()} − the same period ended "
        f"{prior.period_end.isoformat()}"
    )
    # Taking the prior year to date out of the full year leaves the stub after
    # it, so the window opens the day that stub began.
    return _TrailingTwelveMonths(
        value=total,
        sources=(year, current, prior),
        formula=formula,
        period_start=prior.period_end + dt.timedelta(days=ADJACENT_PERIOD_GAP_DAYS),
        period_end=current.period_end,
    )


def _year_to_date_after(
    periods: Sequence[Fact], year_end: dt.date
) -> Fact | None:
    """The longest partial period starting the day after a fiscal year ends."""
    start = year_end + dt.timedelta(days=ADJACENT_PERIOD_GAP_DAYS)
    candidates = [
        fact
        for fact in periods
        if fact.period_start is not None
        and abs((fact.period_start - start).days)
        <= TTM_QUARTER_ADJACENCY_TOLERANCE_DAYS
        and _period_days(fact) < ANNUAL_PERIOD_MIN_DAYS
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda f: (_period_days(f), f.period_end))


def _matching_year_to_date(
    periods: Sequence[Fact], year_start: dt.date, current: Fact
) -> Fact | None:
    """The prior year's period of the same length, starting at that year's start."""
    wanted = _period_days(current)
    candidates = [
        fact
        for fact in periods
        if fact.period_start is not None
        and abs((fact.period_start - year_start).days)
        <= TTM_QUARTER_ADJACENCY_TOLERANCE_DAYS
        and abs(_period_days(fact) - wanted)
        <= TTM_QUARTER_ADJACENCY_TOLERANCE_DAYS
    ]
    if not candidates:
        return None
    return min(
        candidates, key=lambda f: (abs(_period_days(f) - wanted), f.period_end)
    )
