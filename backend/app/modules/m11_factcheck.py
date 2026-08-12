"""m11 — verification gate. Blocking.

Responsibility
    Verify generated prose against the fact store before anything is assembled
    or shown. A report that fails this gate does not render.

    This module is the reason the product's claim is true rather than intended.
    Everything upstream — the tier rules in m06, the prompt in m10, the
    provenance fields on `Fact` — is a control. This is the audit.

Checks
    figures_sourced    Every number in the prose exists in the fact store.
                       A figure that matches nothing is a fabrication,
                       whatever produced it.
    citation_coverage  Every figure is supported by a fact its own sentence
                       cites. A sentence citing one fact while stating
                       another's number is a mis-citation, not a citation.
    section_3_tiers    No Tier 3 or Tier 4 fact carries a section 3 metric.
                       Hard-fails the report.
    segment_sum        Segment revenue sums to consolidated revenue.
    balance_sheet      Assets equal liabilities plus equity.
    cash_flow_tie      The change in cash equals the three cash flow sections.
    range_sanity       No figure lies outside a stated possible range.

Tolerances
    Every tolerance is named in config and stated on the check that used it.
    Filings round their own figures, so "equal" means "equal within a bound
    someone chose and wrote down" — and the bound is reported alongside the
    result rather than buried in it.

Purity
    `verify` is a pure function of the prose and the facts. No I/O, no clock
    beyond the timestamp stamped on the report, no network. That is what makes
    the gate testable, and it is tested.

    Figure-extraction and fact-matching (what counts as a number, what counts
    as sourcing it) live in `_prose_checks`, shared with m10's own self-check
    on a section before it ships. This module re-exports `find_figures` from
    there so nothing downstream of the audit needs to know the mechanism
    moved; `Figure` itself lives in `_prose_checks`.

Public interface
    verify(report, facts) -> ComplianceReport
    find_figures(text) -> list[Figure]
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from collections import defaultdict
from collections.abc import Sequence

from app.config import (
    BALANCE_SHEET_TOLERANCE,
    CASH_FLOW_TIE_TOLERANCE,
    COVERAGE_NOT_MEASURED_TEXT,
    PERCENT_SANITY_BOUNDS,
    RANGE_SANITY_BOUNDS,
    REQUIRED_CITATION_COVERAGE,
    SECTION_3_ALLOWED_TIERS,
    SEGMENT_METRIC,
    SEGMENT_SOURCE_METRIC,
    SEGMENT_SUM_TOLERANCE,
    UNIT_PERCENT_NAME,
    CheckName,
    RangeBound,
)
from app.models import (
    CheckResult,
    ComplianceReport,
    Fact,
    GeneratedReport,
    GeneratedSection,
    Severity,
    SourceTier,
    Violation,
)
from app.modules._prose_checks import (
    find_figures,
    is_year_reference,
    supporting_facts,
    years_in,
)
from app.modules.m06_factstore import section_for_metric

logger = logging.getLogger(__name__)

#: The section whose tier rule is enforced here. Must agree with m06.
FINANCIAL_HIGHLIGHTS_SECTION = 3

#: Metrics the balance sheet identity is checked against.
_ASSETS_METRIC = "balance.total_assets"
_LIABILITIES_METRIC = "balance.total_liabilities"
_EQUITY_METRIC = "balance.total_equity"

#: Metrics the cash flow tie is checked against.
_CASH_METRIC = "balance.cash_and_equivalents"
_CASH_FLOW_SECTIONS = (
    "cashflow.operating",
    "cashflow.investing",
    "cashflow.financing",
)

# --- Checks over prose ------------------------------------------------------


def _written_sections(report: GeneratedReport) -> list[GeneratedSection]:
    return [section for section in report.sections if section.sentences]


def _check_prose(
    report: GeneratedReport, facts: Sequence[Fact]
) -> tuple[CheckResult, CheckResult, int, int]:
    """Runs the two prose checks together and counts the coverage.

    They share a scan of the same text, and separating them would mean
    tokenising the report twice and risking two answers to the question of
    what counts as a figure.

    Returns the sourced check, the coverage check, and the figure counts the
    compliance report renders.
    """
    by_id = {fact.fact_id: fact for fact in facts}
    years = years_in(facts)

    sourced_violations: list[Violation] = []
    coverage_violations: list[Violation] = []

    examined = 0
    covered = 0

    for section in _written_sections(report):
        for sentence in section.sentences:
            cited = [by_id[fid] for fid in sentence.fact_ids if fid in by_id]
            unknown = [fid for fid in sentence.fact_ids if fid not in by_id]

            for fact_id in unknown:
                coverage_violations.append(
                    Violation(
                        check=CheckName.CITATION_COVERAGE,
                        severity=Severity.BLOCKING,
                        section=section.section_id,
                        message=(
                            "A sentence cites a fact that is not in the fact "
                            "store. The citation cannot be resolved."
                        ),
                        detail=f"{fact_id} — {_clip(sentence.text)}",
                    )
                )

            for figure in find_figures(sentence.text):
                if is_year_reference(figure, years):
                    continue

                examined += 1

                if not supporting_facts(figure, facts):
                    sourced_violations.append(
                        Violation(
                            check=CheckName.FIGURES_SOURCED,
                            severity=Severity.BLOCKING,
                            section=section.section_id,
                            message=(
                                "A figure appears in the prose that no stored "
                                "fact carries. Nothing is rendered that cannot "
                                "be traced to a filing."
                            ),
                            detail=f"{figure.text} — {_clip(sentence.text)}",
                        )
                    )
                    continue

                if supporting_facts(figure, cited):
                    covered += 1
                    continue

                coverage_violations.append(
                    Violation(
                        check=CheckName.CITATION_COVERAGE,
                        severity=Severity.BLOCKING,
                        section=section.section_id,
                        message=(
                            "A figure is stated in a sentence that does not "
                            "cite the fact it came from."
                        ),
                        detail=f"{figure.text} — {_clip(sentence.text)}",
                    )
                )

    coverage_ratio = 1.0 if examined == 0 else covered / examined

    sourced = CheckResult(
        check=CheckName.FIGURES_SOURCED,
        description=(
            "Every figure in the generated prose resolves to a stored fact, "
            "exactly or as a correct rounding of it."
        ),
        passed=not sourced_violations,
        examined=examined,
        violations=tuple(sourced_violations),
    )
    coverage = CheckResult(
        check=CheckName.CITATION_COVERAGE,
        description=(
            "Every figure is supported by a fact its own sentence cites. "
            f"Required coverage: {REQUIRED_CITATION_COVERAGE:.0%}."
        ),
        passed=(
            not coverage_violations
            and coverage_ratio >= REQUIRED_CITATION_COVERAGE
        ),
        examined=examined,
        violations=tuple(coverage_violations),
    )
    return sourced, coverage, examined, covered


def _clip(text: str, limit: int = 160) -> str:
    """Shortens a sentence for a violation message."""
    cleaned = " ".join(text.split())
    return cleaned if len(cleaned) <= limit else f"{cleaned[: limit - 1]}…"


# --- Checks over facts ------------------------------------------------------


def _check_section_3_tiers(facts: Sequence[Fact]) -> CheckResult:
    """Hard-fails if a Tier 3 or Tier 4 fact reached the financial highlights.

    This is the check the whole tier system exists for. m05 cannot express a
    section 3 metric and m06 refuses to store one, so a violation here means a
    control upstream has been bypassed — which is exactly when an audit has to
    be the thing that catches it, rather than trusting the controls.
    """
    violations: list[Violation] = []
    examined = 0

    for fact in facts:
        if section_for_metric(fact.metric) != FINANCIAL_HIGHLIGHTS_SECTION:
            continue
        examined += 1
        if int(fact.tier) in SECTION_3_ALLOWED_TIERS:
            continue
        violations.append(
            Violation(
                check=CheckName.SECTION_3_TIERS,
                severity=Severity.BLOCKING,
                section="financials",
                metric=fact.metric,
                message=(
                    f"A tier {int(fact.tier)} figure reached the financial "
                    "highlights, which accept filings and company sources "
                    "only. The report is withheld."
                ),
                detail=(
                    f"{fact.metric} = {fact.display_value}, sourced "
                    f"{fact.source_type} from {fact.source_url}"
                ),
            )
        )

    return CheckResult(
        check=CheckName.SECTION_3_TIERS,
        description=(
            "No market or news figure appears in the financial highlights. "
            f"Section {FINANCIAL_HIGHLIGHTS_SECTION} accepts tiers "
            f"{', '.join(str(t) for t in SECTION_3_ALLOWED_TIERS)} only."
        ),
        passed=not violations,
        examined=examined,
        violations=tuple(violations),
    )


def _by_period(
    facts: Sequence[Fact], metric: str
) -> dict[tuple[dt.date | None, dt.date], Fact]:
    """Facts for one metric, keyed by the period they cover."""
    return {
        (fact.period_start, fact.period_end): fact
        for fact in facts
        if fact.metric == metric and fact.value is not None
    }


def _relative_gap(difference: float, scale: float) -> float:
    """A difference as a fraction of the figure it is a difference in.

    A scale of zero means there is nothing to be a fraction of, so any
    non-zero difference is total. This keeps a division by zero from reading
    as a pass.
    """
    if scale == 0:
        return 0.0 if difference == 0 else math.inf
    return abs(difference) / abs(scale)


def _check_segment_sum(facts: Sequence[Fact]) -> CheckResult:
    """Segment revenue must sum to consolidated revenue, within tolerance."""
    totals = _by_period(facts, SEGMENT_SOURCE_METRIC)

    sums: dict[tuple[dt.date | None, dt.date], float] = defaultdict(float)
    counts: dict[tuple[dt.date | None, dt.date], int] = defaultdict(int)
    for fact in facts:
        if fact.metric != SEGMENT_METRIC or fact.value is None:
            continue
        key = (fact.period_start, fact.period_end)
        sums[key] += fact.value
        counts[key] += 1

    violations: list[Violation] = []
    examined = 0

    for key, total_fact in totals.items():
        if key not in sums:
            continue
        examined += 1
        segment_total = sums[key]
        total = total_fact.value or 0.0
        gap = _relative_gap(segment_total - total, total)
        if gap <= SEGMENT_SUM_TOLERANCE:
            continue
        violations.append(
            Violation(
                check=CheckName.SEGMENT_SUM,
                severity=Severity.ADVISORY,
                section="analysis",
                metric=SEGMENT_METRIC,
                message=(
                    f"The {counts[key]} reported segments sum to "
                    f"{gap:.1%} away from consolidated revenue for the period "
                    "ending "
                    f"{key[1].isoformat()}. The breakdown may be incomplete."
                ),
                detail=(
                    f"segments {segment_total:,.0f} against revenue "
                    f"{total_fact.value:,.0f}"
                ),
            )
        )

    return CheckResult(
        check=CheckName.SEGMENT_SUM,
        description=(
            "Reported segments sum to consolidated revenue, within "
            f"{SEGMENT_SUM_TOLERANCE:.1%}."
        ),
        passed=not violations,
        examined=examined,
        violations=tuple(violations),
    )


def _check_balance_sheet(facts: Sequence[Fact]) -> CheckResult:
    """Assets must equal liabilities plus equity, within tolerance."""
    assets = _by_period(facts, _ASSETS_METRIC)
    liabilities = _by_period(facts, _LIABILITIES_METRIC)
    equity = _by_period(facts, _EQUITY_METRIC)

    violations: list[Violation] = []
    examined = 0

    for key, assets_fact in assets.items():
        liabilities_fact = liabilities.get(key)
        equity_fact = equity.get(key)
        if liabilities_fact is None or equity_fact is None:
            continue

        examined += 1
        total_assets = assets_fact.value or 0.0
        difference = total_assets - (
            (liabilities_fact.value or 0.0) + (equity_fact.value or 0.0)
        )
        gap = _relative_gap(difference, total_assets)
        if gap <= BALANCE_SHEET_TOLERANCE:
            continue

        violations.append(
            Violation(
                check=CheckName.BALANCE_SHEET,
                severity=Severity.BLOCKING,
                section="financials",
                metric=_ASSETS_METRIC,
                message=(
                    "The balance sheet does not balance for the period ending "
                    f"{key[1].isoformat()}: assets differ from liabilities plus "
                    f"equity by {gap:.1%}. The extraction is wrong somewhere, "
                    "so the report is withheld."
                ),
                detail=(
                    f"assets {total_assets:,.0f}, liabilities "
                    f"{liabilities_fact.value:,.0f}, equity "
                    f"{equity_fact.value:,.0f}"
                ),
            )
        )

    return CheckResult(
        check=CheckName.BALANCE_SHEET,
        description=(
            "Total assets equal total liabilities plus total equity, within "
            f"{BALANCE_SHEET_TOLERANCE:.1%}."
        ),
        passed=not violations,
        examined=examined,
        violations=tuple(violations),
    )


def _check_cash_flow_tie(facts: Sequence[Fact]) -> CheckResult:
    """The change in cash must equal the three cash flow sections.

    The gap between them is the effect of exchange rates on cash held abroad,
    which is reported on its own line and is not extracted as a metric. That is
    why this tolerance is wider than the balance sheet's, and why the check is
    advisory rather than blocking: a filer with foreign cash will legitimately
    miss the tie, and refusing to render its report would be wrong.
    """
    cash = _by_period(facts, _CASH_METRIC)
    balances = sorted(
        ((key[1], fact) for key, fact in cash.items()), key=lambda pair: pair[0]
    )

    flows: dict[dt.date, dict[str, float]] = defaultdict(dict)
    for fact in facts:
        if fact.metric in _CASH_FLOW_SECTIONS and fact.value is not None:
            flows[fact.period_end][fact.metric] = fact.value

    violations: list[Violation] = []
    examined = 0

    for (_, opening), (closing_date, closing) in zip(
        balances, balances[1:], strict=False
    ):
        sections = flows.get(closing_date)
        if sections is None or len(sections) != len(_CASH_FLOW_SECTIONS):
            continue

        examined += 1
        change = (closing.value or 0.0) - (opening.value or 0.0)
        total = sum(sections.values())
        scale = max(abs(change), abs(total))
        gap = _relative_gap(change - total, scale)
        if gap <= CASH_FLOW_TIE_TOLERANCE:
            continue

        violations.append(
            Violation(
                check=CheckName.CASH_FLOW_TIE,
                severity=Severity.ADVISORY,
                section="financials",
                metric=_CASH_METRIC,
                message=(
                    "The change in cash to "
                    f"{closing_date.isoformat()} differs from the sum of the "
                    f"three cash flow sections by {gap:.1%}. The difference is "
                    "usually the effect of exchange rates on cash held abroad, "
                    "which is not extracted as a metric."
                ),
                detail=(
                    f"change {change:,.0f} against sections {total:,.0f}"
                ),
            )
        )

    return CheckResult(
        check=CheckName.CASH_FLOW_TIE,
        description=(
            "The change in cash equals operating plus investing plus financing "
            f"cash flow, within {CASH_FLOW_TIE_TOLERANCE:.0%}."
        ),
        passed=not violations,
        examined=examined,
        violations=tuple(violations),
    )


def _bound_for(metric: str) -> RangeBound | None:
    """The most specific bound configured for a metric, if any."""
    best: RangeBound | None = None
    for bound in RANGE_SANITY_BOUNDS:
        if bound.metric == metric:
            return bound
        if bound.metric.endswith(".") and metric.startswith(bound.metric):
            if best is None or len(bound.metric) > len(best.metric):
                best = bound
    return best


def _check_range_sanity(facts: Sequence[Fact]) -> CheckResult:
    """No figure may lie outside a range that is physically possible.

    This catches the unit error and the sign error — a margin of 4,300%, a
    negative share count — which are arithmetically self-consistent and
    therefore survive every other check here.
    """
    violations: list[Violation] = []
    examined = 0

    for fact in facts:
        if fact.value is None:
            continue

        bound = _bound_for(fact.metric)
        minimum, maximum, reason = _limits(fact, bound)
        if minimum is None and maximum is None:
            continue

        examined += 1
        below = minimum is not None and fact.value < minimum
        above = maximum is not None and fact.value > maximum
        if not below and not above:
            continue

        violations.append(
            Violation(
                check=CheckName.RANGE_SANITY,
                severity=Severity.BLOCKING,
                section=None,
                metric=fact.metric,
                message=(
                    f"{fact.label} is outside the range a figure of its kind "
                    f"can occupy. {reason}"
                ),
                detail=(
                    f"{fact.metric} = {fact.display_value} for the period "
                    f"ending {fact.period_end.isoformat()}"
                ),
            )
        )

    return CheckResult(
        check=CheckName.RANGE_SANITY,
        description=(
            "Every figure lies inside a range stated in config as possible for "
            "its metric."
        ),
        passed=not violations,
        examined=examined,
        violations=tuple(violations),
    )


def _limits(
    fact: Fact, bound: RangeBound | None
) -> tuple[float | None, float | None, str]:
    """The bounds that apply to one fact, and why they do.

    A metric-specific bound wins. Failing that, a fact carrying the percent
    unit gets the generic percentage guard, so a percentage metric added later
    is covered without anyone remembering to extend the table.
    """
    if bound is not None:
        return bound.minimum, bound.maximum, bound.reason
    if fact.unit == UNIT_PERCENT_NAME:
        minimum, maximum = PERCENT_SANITY_BOUNDS
        return (
            minimum,
            maximum,
            "A percentage this large is a unit error, not a business outcome.",
        )
    return None, None, ""


# --- Public interface -------------------------------------------------------


def verify(
    report: GeneratedReport,
    facts: Sequence[Fact],
    *,
    verified_at: dt.datetime | None = None,
) -> ComplianceReport:
    """Verifies generated prose and stored facts, and reports on both.

    Args:
        report: The prose from m10. A section with no sentences is not
            examined — there is nothing in it to be wrong.
        facts: Every fact stored for this report.
        verified_at: Overridable so that a test can assert on a fixed
            timestamp. Defaults to now.

    Returns:
        A `ComplianceReport`. `passed` is False when any blocking violation was
        found, and the caller must not render the report. Advisory findings are
        rendered beside the figures they concern.

    Pure: no I/O, no network, no database. The only impurity is the clock, and
    it is injectable.
    """
    sourced, coverage, figure_count, cited_count = _check_prose(report, facts)

    checks = (
        sourced,
        coverage,
        _check_section_3_tiers(facts),
        _check_segment_sum(facts),
        _check_balance_sheet(facts),
        _check_cash_flow_tie(facts),
        _check_range_sanity(facts),
    )

    violations = tuple(
        violation for check in checks for violation in check.violations
    )
    blocking = tuple(
        violation
        for violation in violations
        if violation.severity is Severity.BLOCKING
    )

    # A ratio over no figures is not a hundred per cent — it is a measurement
    # that did not happen. The gate still passes, because nothing went uncited
    # and an empty report is not a failing one, but the score reported to a
    # reader must not be earned by having checked nothing: coverage is measured
    # over written passages, and a report generated without prose has none.
    coverage_ratio = 1.0 if figure_count == 0 else cited_count / figure_count
    coverage_display = (
        COVERAGE_NOT_MEASURED_TEXT
        if figure_count == 0
        else f"{coverage_ratio:.0%}"
    )

    # Every tier starts at 0 so a tier with no facts reports a count, not a
    # missing key — the UI renders whatever key is absent as "NaN".
    tier_counts: dict[int, int] = {int(tier): 0 for tier in SourceTier}
    for fact in facts:
        tier_counts[int(fact.tier)] += 1

    result = ComplianceReport(
        passed=not blocking,
        verified_at=verified_at or dt.datetime.now(dt.UTC),
        fact_count=len(facts),
        tier_counts=dict(sorted(tier_counts.items())),
        figure_count=figure_count,
        cited_figure_count=cited_count,
        coverage_ratio=coverage_ratio,
        coverage_display=coverage_display,
        checks=checks,
        violations=violations,
    )

    log = logger.error if blocking else logger.info
    log(
        "Verification complete",
        extra={
            "passed": result.passed,
            "facts": result.fact_count,
            "figures": figure_count,
            "coverage": result.coverage_display,
            "blocking": len(blocking),
            "advisory": len(violations) - len(blocking),
            "failed_checks": [
                check.check for check in checks if not check.passed
            ],
        },
    )
    return result
