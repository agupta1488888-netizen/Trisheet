"""Operational metrics: success rate, latency, citation coverage, cost.

Every figure here is counted from run records, never sampled or estimated. The
arithmetic is Python's, over rows the run itself wrote — the same rule that
governs the reports this system produces governs the numbers it reports about
itself.

Absent is not zero
    A window with no settled run reports no runs, and the rate figures read
    "No data" rather than "0%". Those are different statements: one says
    nothing has happened, the other says everything failed.

Public interface
    summarise(window_hours) -> ReportMetrics
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence

from app.config import (
    LATENCY_PERCENTILES,
    METRIC_UNAVAILABLE_TEXT,
    METRICS_DEFAULT_WINDOW_HOURS,
    METRICS_MAX_ROWS,
    METRICS_MAX_WINDOW_HOURS,
)
from app.models import ReportMetrics, ReportStatus, StepState, StepTiming
from app.services import runlog
from app.services.runlog import RunRecord, StepRecord

logger = logging.getLogger(__name__)

#: Steps read per window. Generous: a window of a hundred reports writes a few
#: thousand step rows, and the timing table is only meaningful over all of them.
_MAX_STEP_ROWS = METRICS_MAX_ROWS * 16


def summarise(window_hours: int = METRICS_DEFAULT_WINDOW_HOURS) -> ReportMetrics:
    """The operational picture over the last `window_hours`.

    Args:
        window_hours: Clamped to the configured maximum. A caller asking for a
            year gets the maximum window and a figure that says which window
            it is, rather than a slow query or a silent truncation.
    """
    hours = max(1, min(window_hours, METRICS_MAX_WINDOW_HOURS))
    since = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)

    runs = runlog.recent_runs(since, METRICS_MAX_ROWS)
    steps = runlog.recent_steps(since, _MAX_STEP_ROWS)

    settled = [run for run in runs if _is_settled(run)]
    complete = [run for run in settled if run.status is ReportStatus.COMPLETE]
    failed = [run for run in settled if run.status is ReportStatus.FAILED]

    success_rate = (
        len(complete) / len(settled) if settled else None
    )
    latencies = [
        run.duration_ms for run in complete if run.duration_ms is not None
    ]
    coverages = [
        run.coverage_ratio
        for run in complete
        if run.coverage_ratio is not None
    ]
    costs = [run.cost_usd for run in complete if run.cost_usd is not None]
    tokens = [
        (run.input_tokens or 0) + (run.output_tokens or 0)
        for run in complete
        if run.input_tokens is not None or run.output_tokens is not None
    ]

    p50, p95 = (
        (_percentile(latencies, LATENCY_PERCENTILES[0]),
         _percentile(latencies, LATENCY_PERCENTILES[1]))
        if latencies
        else (None, None)
    )
    coverage = _mean(coverages)
    cost = _mean(costs)
    per_report_tokens = _mean([float(count) for count in tokens])

    metrics = ReportMetrics(
        window_hours=hours,
        reports_total=len(runs),
        reports_complete=len(complete),
        reports_failed=len(failed),
        success_rate=success_rate,
        success_rate_display=_percent_display(success_rate),
        p50_latency_ms=p50,
        p95_latency_ms=p95,
        citation_coverage=coverage,
        citation_coverage_display=_percent_display(coverage),
        cost_per_report_usd=None if cost is None else round(cost, 4),
        cost_per_report_display=_money_display(cost),
        tokens_per_report=None if per_report_tokens is None else int(per_report_tokens),
        steps=_step_timings(steps),
        generated_at=dt.datetime.now(dt.UTC),
    )

    logger.info(
        "Metrics computed",
        extra={
            "window_hours": hours,
            "reports": metrics.reports_total,
            "success_rate": metrics.success_rate_display,
            "p95_latency_ms": metrics.p95_latency_ms,
            "coverage": metrics.citation_coverage_display,
        },
    )
    return metrics


def _is_settled(run: RunRecord) -> bool:
    """True when a run reached a terminal status.

    A run still in flight is counted in the total but not in the rate: it has
    not succeeded and it has not failed, and averaging it in either direction
    would misstate both.
    """
    return run.status in (ReportStatus.COMPLETE, ReportStatus.FAILED)


def _percentile(values: Sequence[int], percentile: int) -> int:
    """Nearest-rank percentile over a small sample.

    Deliberately not an interpolating percentile: with a handful of runs in the
    window, interpolation invents a latency no request actually had. This
    returns a duration that was really observed.
    """
    ordered = sorted(values)
    if not ordered:
        return 0
    rank = max(1, -(-percentile * len(ordered) // 100))
    return ordered[min(rank, len(ordered)) - 1]


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percent_display(ratio: float | None) -> str:
    return METRIC_UNAVAILABLE_TEXT if ratio is None else f"{ratio:.0%}"


def _money_display(amount: float | None) -> str:
    if amount is None:
        return METRIC_UNAVAILABLE_TEXT
    # Sub-cent spend is real and worth showing to a reader deciding whether a
    # report is affordable at volume, so it is not rounded away.
    return f"${amount:.4f}" if amount < 0.01 else f"${amount:.2f}"


def _step_timings(steps: Sequence[StepRecord]) -> tuple[StepTiming, ...]:
    """Per-module timings, slowest at p95 first.

    Ordered by p95 rather than by run order because the reason to read this
    table is to find what a reader waits on.
    """
    grouped: dict[str, list[StepRecord]] = {}
    for step in steps:
        grouped.setdefault(step.module, []).append(step)

    timings = [
        StepTiming(
            module=module,
            label=members[0].label,
            runs=len(members),
            p50_ms=_percentile(
                [member.duration_ms for member in members],
                LATENCY_PERCENTILES[0],
            ),
            p95_ms=_percentile(
                [member.duration_ms for member in members],
                LATENCY_PERCENTILES[1],
            ),
            failures=sum(
                1 for member in members if member.state is StepState.FAILED
            ),
        )
        for module, members in grouped.items()
    ]
    return tuple(sorted(timings, key=lambda timing: -timing.p95_ms))
