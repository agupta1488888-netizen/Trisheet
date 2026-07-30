"""The run record: report status, pipeline steps and rendered artifacts.

Every write a running report makes about *itself* goes through here — the facts
it extracts go through m06 instead. Two consumers read what this writes: the
browser's progress feed, over Supabase Realtime, and the metrics endpoint.

Why steps are written twice
    A step event is written to first-class columns (`step`, `state`,
    `duration_ms`, `count`) and, in the same row, into `context.step`. The
    columns are what the metrics endpoint aggregates and indexes; the jsonb is
    the shape the browser already parses off a Realtime payload without a join.
    Both come from one `StepEvent`, so they cannot disagree.

Degradation
    Supabase is not a hard dependency. With no database configured the run
    record lives in a process-local dictionary: the API still returns a job id,
    the report still generates, and status is still pollable — it is simply
    lost when the process restarts, and `is_durable()` says so. This is what
    lets the pipeline be exercised end to end against EDGAR alone.

Public interface
    create_report(ticker, cik, depth) -> Report
    get_report(report_id) -> Report | None
    set_status(report_id, status, *, error_message) -> None
    record_step(report_id, event) -> None
    finish(report_id, summary) -> None
    record_artifacts(report_id, artifacts) -> None
    steps_for(report_id) -> list[ProgressStep]
    is_durable() -> bool
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.models import (
    AnalysisDepth,
    ArtifactRef,
    Company,
    ProgressStep,
    Report,
    ReportStatus,
    StepState,
)
from app.services import db

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)

#: One write against the database, deferred so it can be attempted inside
#: the error boundary rather than at every call site.
_Write = Callable[["Client"], object]

REPORTS_TABLE = "reports"
RUN_LOGS_TABLE = "run_logs"
ARTIFACTS_TABLE = "artifacts"
COMPANIES_TABLE = "companies"

#: Columns read back when rebuilding a `Report`. Explicit so a schema addition
#: cannot silently start arriving as an unexpected key.
_REPORT_COLUMNS = (
    "id,ticker,cik,status,error_message,created_at,completed_at"
)

#: Levels the run_logs check constraint accepts.
_LEVEL_INFO = "INFO"
_LEVEL_ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class StepEvent:
    """One pipeline step, at one moment.

    Emitted twice per step: once on entry with `state=RUNNING` and no duration,
    once on settling with the outcome and the wall time it took. The browser
    merges the two by module, which is what makes the feed a feed rather than a
    growing list of duplicates.
    """

    module: str
    label: str
    state: StepState
    count: int | None = None
    count_label: str | None = None
    detail: str | None = None
    duration_ms: int | None = None
    at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))

    @property
    def level(self) -> str:
        return _LEVEL_ERROR if self.state is StepState.FAILED else _LEVEL_INFO

    def as_step(self) -> ProgressStep:
        return ProgressStep(
            module=self.module,
            label=self.label,
            state=self.state,
            count=self.count,
            count_label=self.count_label if self.count is not None else None,
            at=self.at,
            detail=self.detail,
            duration_ms=self.duration_ms,
        )

    def as_context(self) -> dict[str, Any]:
        """The `context.step` payload the browser's progress feed parses."""
        return {
            "step": {
                "label": self.label,
                "state": str(self.state),
                "count": self.count,
                "count_label": (
                    self.count_label if self.count is not None else None
                ),
                "detail": self.detail,
                "duration_ms": self.duration_ms,
            }
        }


@dataclass(frozen=True, slots=True)
class RunSummary:
    """What a settled run is worth recording about itself.

    Everything here is measured during the run and written once. None of it is
    re-derived on read: a report's cost is a fact about that run, and repricing
    it later against a changed rate card would quietly restate history.
    """

    duration_ms: int
    fact_count: int
    figure_count: int
    cited_figure_count: int
    coverage_ratio: float
    compliance_passed: bool
    input_tokens: int
    output_tokens: int
    cost_usd: float


# --- In-memory fallback ------------------------------------------------------


@dataclass
class _Run:
    """A run record held in this process, when there is no database."""

    report: Report
    steps: dict[str, ProgressStep] = field(default_factory=dict)
    artifacts: list[ArtifactRef] = field(default_factory=list)
    summary: RunSummary | None = None
    #: The assembled document, held so the report can be served without
    #: re-running the pipeline. Typed loosely to keep this service free of a
    #: dependency on the assembler, which depends on it in turn.
    document: object | None = None


#: Process-local. Documented rather than hidden: with no Supabase configured
#: this is where a report lives, it is not shared between workers, and it does
#: not survive a restart. `is_durable()` reports the condition so the interface
#: can say which mode it is in.
_runs: dict[str, _Run] = {}

#: Runs kept in memory. A long-lived worker would otherwise hold every document
#: it ever assembled; the oldest are dropped, and a dropped report reads as
#: "no longer available" rather than as a blank page.
MAX_RETAINED_RUNS = 64


def _evict_if_needed() -> None:
    """Drops the oldest runs once the cache is over its bound."""
    while len(_runs) > MAX_RETAINED_RUNS:
        oldest = min(_runs, key=lambda key: _runs[key].report.created_at)
        del _runs[oldest]


def is_durable() -> bool:
    """True when the run record is written to a database rather than memory."""
    return db.is_configured()


def reset() -> None:
    """Clears the in-memory records. Used by tests."""
    _runs.clear()


# --- Report lifecycle --------------------------------------------------------


def create_report(
    ticker: str, cik: str | None, depth: AnalysisDepth
) -> Report:
    """Registers a queued report and returns it with its id.

    The id is minted here rather than by the database so that the endpoint can
    answer with a job id in the same breath whether or not persistence is
    configured — and so a failed insert does not leave the caller without one.
    """
    report = Report(
        id=str(uuid.uuid4()),
        ticker=ticker.upper(),
        cik=cik,
        status=ReportStatus.QUEUED,
        created_at=dt.datetime.now(dt.UTC),
    )
    _runs[report.id] = _Run(report=report)
    _evict_if_needed()

    if not is_durable():
        logger.warning(
            "No database configured; this run is recorded in memory only",
            extra={"report_id": report.id, "ticker": report.ticker},
        )
        return report

    row = {
        "id": report.id,
        "ticker": report.ticker,
        "cik": report.cik,
        "status": str(report.status),
        "depth": str(depth),
        "created_at": report.created_at.isoformat(),
        "started_at": report.created_at.isoformat(),
    }
    _try_write(
        lambda client: client.table(REPORTS_TABLE).insert(row).execute(),
        "Report could not be registered",
        {"report_id": report.id},
    )
    return report


def register_company(company: Company) -> None:
    """Upserts the filer a report resolved to.

    Called once m01 has resolved a real `Company`, which is necessarily after
    `create_report` — the endpoint answers with a job id before resolution has
    run, so a report's own `cik` cannot be required to already exist here.
    Nothing else in the pipeline writes this table; a CIK reported on is
    otherwise never recorded anywhere durable.
    """
    if not is_durable():
        return

    row = {
        "cik": company.cik,
        "ticker": company.ticker,
        "name": company.name,
        "filer_type": str(company.filer_type),
        "sic_code": company.sic_code,
        "sector": company.sector,
        "fiscal_year_end": company.fiscal_year_end,
        "reporting_currency": company.reporting_currency,
    }
    _try_write(
        lambda client: (
            client.table(COMPANIES_TABLE).upsert(row, on_conflict="cik").execute()
        ),
        "Company could not be registered",
        {"cik": company.cik},
    )


def get_report(report_id: str) -> Report | None:
    """The current state of a report, or None when there is no such id."""
    if is_durable():
        row = _read_report(report_id)
        if row is not None:
            return row

    run = _runs.get(report_id)
    return run.report if run is not None else None


def set_status(
    report_id: str,
    status: ReportStatus,
    *,
    error_message: str | None = None,
) -> None:
    """Moves a report to a new status, and says why when it failed.

    The failure message is written for a reader, because it is what the
    progress screen shows in place of the report.
    """
    run = _runs.get(report_id)
    completed = (
        dt.datetime.now(dt.UTC)
        if status in (ReportStatus.COMPLETE, ReportStatus.FAILED)
        else None
    )

    if run is not None:
        run.report = run.report.model_copy(
            update={
                "status": status,
                "error_message": error_message,
                "completed_at": completed or run.report.completed_at,
            }
        )

    if not is_durable():
        return

    update: dict[str, Any] = {"status": str(status)}
    if error_message is not None:
        update["error_message"] = error_message
    if completed is not None:
        update["completed_at"] = completed.isoformat()

    _try_write(
        lambda client: (
            client.table(REPORTS_TABLE)
            .update(update)
            .eq("id", report_id)
            .execute()
        ),
        "Report status could not be updated",
        {"report_id": report_id, "status": str(status)},
    )


def finish(report_id: str, summary: RunSummary) -> None:
    """Records what the run produced and what it cost."""
    run = _runs.get(report_id)
    if run is not None:
        run.summary = summary

    if not is_durable():
        return

    update = {
        "duration_ms": summary.duration_ms,
        "fact_count": summary.fact_count,
        "figure_count": summary.figure_count,
        "cited_figure_count": summary.cited_figure_count,
        "coverage_ratio": round(summary.coverage_ratio, 6),
        "compliance_passed": summary.compliance_passed,
        "input_tokens": summary.input_tokens,
        "output_tokens": summary.output_tokens,
        "cost_usd": round(summary.cost_usd, 6),
    }
    _try_write(
        lambda client: (
            client.table(REPORTS_TABLE)
            .update(update)
            .eq("id", report_id)
            .execute()
        ),
        "Run summary could not be recorded",
        {"report_id": report_id},
    )


# --- Steps -------------------------------------------------------------------


def record_step(report_id: str, event: StepEvent) -> None:
    """Writes one step event.

    Never raises. A progress feed that cannot be written is a feed the reader
    does not see; it is not a reason to stop generating their report.
    """
    run = _runs.get(report_id)
    if run is not None:
        run.steps[event.module] = event.as_step()

    if not is_durable():
        return

    row = {
        "report_id": report_id,
        "module": event.module,
        "level": event.level,
        "message": event.label,
        "context": event.as_context(),
        "step": event.label,
        "state": str(event.state),
        "duration_ms": event.duration_ms,
        "count": event.count,
        "created_at": event.at.isoformat(),
    }
    _try_write(
        lambda client: client.table(RUN_LOGS_TABLE).insert(row).execute(),
        "Step could not be logged",
        # Not "module" — collides with LogRecord's own reserved attribute of
        # that name and would crash the error-reporting log call itself.
        {"report_id": report_id, "pipeline_module": event.module},
    )


def steps_for(report_id: str) -> list[ProgressStep]:
    """The steps recorded for a run, in the order they were first entered."""
    run = _runs.get(report_id)
    return list(run.steps.values()) if run is not None else []


# --- Artifacts ---------------------------------------------------------------


def record_artifacts(
    report_id: str, artifacts: Sequence[ArtifactRef]
) -> None:
    """Records the artifacts that were rendered and uploaded.

    Only artifacts that exist are recorded. One that could not be rendered has
    no row: the reason travels on the report document instead, because an empty
    row would claim a file that is not there.
    """
    run = _runs.get(report_id)
    if run is not None:
        run.artifacts = list(artifacts)

    if not is_durable():
        return

    rows = [
        {
            "report_id": report_id,
            "kind": str(artifact.kind),
            "storage_path": artifact.storage_path,
            "url": str(artifact.url) if artifact.url else None,
            "size_bytes": artifact.size_bytes,
            "content_type": artifact.content_type,
        }
        for artifact in artifacts
        if artifact.storage_path and artifact.size_bytes > 0
    ]
    if not rows:
        return

    _try_write(
        lambda client: (
            client.table(ARTIFACTS_TABLE)
            .upsert(rows, on_conflict="report_id,kind")
            .execute()
        ),
        "Artifacts could not be recorded",
        {"report_id": report_id},
    )


def artifacts_for(report_id: str) -> list[ArtifactRef]:
    """Artifacts recorded in this process for a run."""
    run = _runs.get(report_id)
    return list(run.artifacts) if run is not None else []


# --- The assembled document --------------------------------------------------


def record_document(report_id: str, document: object) -> None:
    """Holds the assembled document so it can be served without re-running.

    Kept in memory rather than written to the database: it is a projection of
    the facts, which are already stored, and re-persisting it would create a
    second copy that could disagree with them.
    """
    run = _runs.get(report_id)
    if run is not None:
        run.document = document


def document_for(report_id: str) -> object | None:
    """The assembled document for a run, or None when it is no longer held."""
    run = _runs.get(report_id)
    return run.document if run is not None else None


# --- Reading the record back -------------------------------------------------
# What the metrics endpoint aggregates. Reads are shaped here rather than in
# the endpoint so that the in-memory and database modes answer with the same
# rows, and the arithmetic over them is written once.


@dataclass(frozen=True, slots=True)
class RunRecord:
    """One settled run, as the monitoring window sees it."""

    report_id: str
    ticker: str
    status: ReportStatus
    created_at: dt.datetime
    completed_at: dt.datetime | None
    duration_ms: int | None
    fact_count: int | None
    figure_count: int | None
    cited_figure_count: int | None
    coverage_ratio: float | None
    compliance_passed: bool | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None


@dataclass(frozen=True, slots=True)
class StepRecord:
    """One settled step, for the per-module timing table."""

    module: str
    label: str
    state: StepState
    duration_ms: int


_RUN_COLUMNS = (
    "id,ticker,status,created_at,completed_at,duration_ms,fact_count,"
    "figure_count,cited_figure_count,coverage_ratio,compliance_passed,"
    "input_tokens,output_tokens,cost_usd"
)


def recent_runs(since: dt.datetime, limit: int) -> list[RunRecord]:
    """Runs created since `since`, newest first.

    Reads the database when one is configured and this process's own records
    otherwise, so the metrics endpoint answers in both modes rather than
    reporting zero runs on a machine that has just generated ten.
    """
    if is_durable():
        rows = _read_runs(since, limit)
        if rows is not None:
            return rows

    return sorted(
        (
            RunRecord(
                report_id=run.report.id,
                ticker=run.report.ticker,
                status=run.report.status,
                created_at=run.report.created_at,
                completed_at=run.report.completed_at,
                duration_ms=run.summary.duration_ms if run.summary else None,
                fact_count=run.summary.fact_count if run.summary else None,
                figure_count=run.summary.figure_count if run.summary else None,
                cited_figure_count=(
                    run.summary.cited_figure_count if run.summary else None
                ),
                coverage_ratio=(
                    run.summary.coverage_ratio if run.summary else None
                ),
                compliance_passed=(
                    run.summary.compliance_passed if run.summary else None
                ),
                input_tokens=run.summary.input_tokens if run.summary else None,
                output_tokens=(
                    run.summary.output_tokens if run.summary else None
                ),
                cost_usd=run.summary.cost_usd if run.summary else None,
            )
            for run in _runs.values()
            if run.report.created_at >= since
        ),
        key=lambda record: record.created_at,
        reverse=True,
    )[:limit]


def recent_steps(since: dt.datetime, limit: int) -> list[StepRecord]:
    """Settled steps since `since`. Empty when they were never written down."""
    if not is_durable():
        # In-memory mode keeps only the latest step per module per run, which
        # is a feed rather than a history. Reporting nothing is honest; a
        # timing table built from one sample per module would not be.
        return []

    try:
        response = (
            db.get_client()
            .table(RUN_LOGS_TABLE)
            .select("module,step,state,duration_ms")
            .gte("created_at", since.isoformat())
            .not_.is_("duration_ms", "null")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as cause:  # noqa: BLE001 — metrics never fail a request
        logger.warning(
            "Step timings could not be read", extra={"error": str(cause)}
        )
        return []

    records: list[StepRecord] = []
    for row in getattr(response, "data", None) or []:
        duration = row.get("duration_ms")
        state = row.get("state")
        if not isinstance(duration, int) or state is None:
            continue
        records.append(
            StepRecord(
                module=str(row.get("module", "")),
                label=str(row.get("step") or row.get("module", "")),
                state=StepState(state),
                duration_ms=duration,
            )
        )
    return records


def _read_runs(since: dt.datetime, limit: int) -> list[RunRecord] | None:
    """Reads settled runs from the database, or None when it could not."""
    try:
        response = (
            db.get_client()
            .table(REPORTS_TABLE)
            .select(_RUN_COLUMNS)
            .gte("created_at", since.isoformat())
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as cause:  # noqa: BLE001 — falls back to memory
        logger.warning(
            "Runs could not be read for metrics", extra={"error": str(cause)}
        )
        return None

    records: list[RunRecord] = []
    for row in getattr(response, "data", None) or []:
        try:
            records.append(
                RunRecord(
                    report_id=str(row["id"]),
                    ticker=str(row.get("ticker", "")),
                    status=ReportStatus(row["status"]),
                    created_at=_parsed(row.get("created_at")),
                    completed_at=_optional_time(row.get("completed_at")),
                    duration_ms=_optional_int(row.get("duration_ms")),
                    fact_count=_optional_int(row.get("fact_count")),
                    figure_count=_optional_int(row.get("figure_count")),
                    cited_figure_count=_optional_int(
                        row.get("cited_figure_count")
                    ),
                    coverage_ratio=_optional_float(row.get("coverage_ratio")),
                    compliance_passed=row.get("compliance_passed"),
                    input_tokens=_optional_int(row.get("input_tokens")),
                    output_tokens=_optional_int(row.get("output_tokens")),
                    cost_usd=_optional_float(row.get("cost_usd")),
                )
            )
        except (KeyError, ValueError, TypeError) as cause:
            logger.warning(
                "Run row could not be read for metrics",
                extra={"error": str(cause)},
            )
    return records


def _parsed(value: object) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    return dt.datetime.fromisoformat(str(value))


def _optional_time(value: object) -> dt.datetime | None:
    return None if value is None else _parsed(value)


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float | str) and value != "" else None


def _optional_float(value: object) -> float | None:
    return (
        float(value)
        if isinstance(value, int | float | str) and value != ""
        else None
    )


# --- Persistence plumbing ----------------------------------------------------


def _try_write(
    operation: _Write, message: str, context: dict[str, Any]
) -> None:
    """Runs one write, logging rather than raising on failure.

    The run record is a sink. Losing a status update costs the reader their
    progress feed; raising here would cost them the report.
    """
    try:
        operation(db.get_client())
    except Exception as cause:  # noqa: BLE001 — a sink never fails the report
        logger.error(message, extra={**context, "error": str(cause)})


def _read_report(report_id: str) -> Report | None:
    """Reads a report row back, or None when it is absent or unreadable."""
    try:
        response = (
            db.get_client()
            .table(REPORTS_TABLE)
            .select(_REPORT_COLUMNS)
            .eq("id", report_id)
            .limit(1)
            .execute()
        )
    except Exception as cause:  # noqa: BLE001 — falls back to memory
        logger.warning(
            "Report could not be read",
            extra={"report_id": report_id, "error": str(cause)},
        )
        return None

    rows = getattr(response, "data", None) or []
    if not rows:
        return None

    try:
        return Report.model_validate(rows[0])
    except Exception as cause:  # noqa: BLE001 — schema drift, not a report
        logger.warning(
            "Stored report row could not be read back",
            extra={"report_id": report_id, "error": str(cause)},
        )
        return None
