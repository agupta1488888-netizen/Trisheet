"""The pipeline: m01 through m12, run as one tracked job.

Responsibility
    Order the modules, move the report through its statuses, and record every
    step with what it produced and how long it took. It holds no opinion about
    filings, figures or prose — each of those belongs to the module it calls.

Failure policy, stated once
    EDGAR is the only hard dependency. Resolution, discovery and XBRL
    extraction can fail the report, because without them there is nothing to
    report. Every other step is optional: it is allowed to produce nothing,
    the step settles as SKIPPED or FAILED with a reason a reader can act on,
    and the run continues. That is not leniency — it is the difference between
    a smaller report and no report.

Provenance without persistence
    Facts pass through m06's gate whether or not a database is configured. The
    gate is what enforces provenance; storage is what remembers it. A run with
    no Supabase still refuses a fact that cannot name its source and still
    hard-blocks Tier 3 from section 3 — it simply does not write the survivors
    down.

Public interface
    run(report_id, ticker, cik, depth) -> RunOutcome
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TypeVar

from app.config import (
    DEPTH_PROFILES,
    PIPELINE_STEP_LABELS,
    STEP_COUNT_LABELS,
    DepthProfile,
)
from app.models import (
    AnalysisDepth,
    ArtifactRef,
    Company,
    ComplianceReport,
    DevelopmentEvent,
    Fact,
    FilingRef,
    GeneratedReport,
    PeerSet,
    Report,
    ReportDocument,
    ReportStatus,
    StepState,
)
from app.modules import (
    m01_resolver,
    m02_discovery,
    m03_financials,
    m04_narrative,
    m05_market,
    m06_factstore,
    m07_analysis,
    m08_peers,
    m09_developments,
    m10_writer,
    m11_factcheck,
    m12_assembler,
)
from app.services import edgar, llm, runlog
from app.services.edgar import EdgarClient
from app.services.runlog import RunSummary, StepEvent

logger = logging.getLogger(__name__)

T = TypeVar("T")

_LABELS = dict(PIPELINE_STEP_LABELS)

#: Said when EDGAR itself could not answer. The one failure with no fallback.
_EDGAR_FAILED = (
    "SEC EDGAR could not be reached, so no filings could be read. Try again "
    "in a moment."
)


class PipelineError(Exception):
    """The report cannot be produced. Carries a message written for a reader."""


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """What a finished run produced. Returned to the caller and to tests."""

    report: Report
    document: ReportDocument | None
    compliance: ComplianceReport | None
    duration_ms: int
    facts: int
    #: Steps that did not settle as DONE, with the reason each gave.
    warnings: tuple[str, ...] = ()

    @property
    def completed(self) -> bool:
        return self.report.status is ReportStatus.COMPLETE


@dataclass
class _Outcome:
    """A step's result, filled in by its body.

    A step that says nothing settles as DONE with no count. One that calls
    `skip` settles as SKIPPED with the reason, which the feed renders as a
    finding rather than as an error.
    """

    count: int | None = None
    detail: str | None = None
    state: StepState = StepState.DONE

    def done(self, count: int | None = None) -> None:
        self.count = count
        self.state = StepState.DONE

    def skip(self, reason: str, count: int | None = None) -> None:
        self.state = StepState.SKIPPED
        self.detail = reason
        self.count = count


@dataclass
class _Work:
    """Everything the run accumulates, in the order the modules produce it."""

    company: Company | None = None
    manifest: list[FilingRef] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    peers: PeerSet | None = None
    events: list[DevelopmentEvent] = field(default_factory=list)
    prose: GeneratedReport | None = None
    compliance: ComplianceReport | None = None
    artifacts: list[ArtifactRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class _Tracker:
    """Emits one step event on entry and one on settling, with the duration."""

    def __init__(self, report_id: str) -> None:
        self.report_id = report_id
        self.warnings: list[str] = []

    @asynccontextmanager
    async def step(self, module: str) -> AsyncIterator[_Outcome]:
        label = _LABELS.get(module, module)
        outcome = _Outcome()

        runlog.record_step(
            self.report_id,
            StepEvent(module=module, label=label, state=StepState.RUNNING),
        )
        started = time.monotonic()

        try:
            yield outcome
        except Exception as failure:
            elapsed = _elapsed_ms(started)
            reason = _reader_message(failure)
            runlog.record_step(
                self.report_id,
                StepEvent(
                    module=module,
                    label=label,
                    state=StepState.FAILED,
                    detail=reason,
                    duration_ms=elapsed,
                ),
            )
            self.warnings.append(f"{module}: {reason}")
            logger.warning(
                "Pipeline step failed",
                extra={
                    "report_id": self.report_id,
                    # Not "module" — that name collides with LogRecord's own
                    # reserved `module` attribute (the caller's source file)
                    # and raises a KeyError that would mask this warning.
                    "pipeline_module": module,
                    "duration_ms": elapsed,
                    "error": str(failure),
                },
            )
            raise

        elapsed = _elapsed_ms(started)
        runlog.record_step(
            self.report_id,
            StepEvent(
                module=module,
                label=label,
                state=outcome.state,
                count=outcome.count,
                count_label=STEP_COUNT_LABELS.get(module),
                detail=outcome.detail,
                duration_ms=elapsed,
            ),
        )
        if outcome.state is StepState.SKIPPED and outcome.detail:
            self.warnings.append(f"{module}: {outcome.detail}")


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _reader_message(failure: BaseException) -> str:
    """A failure phrased for the interface rather than for a log.

    Our own typed failures already carry a reader-facing message; anything
    else gets a plain one, and the detail stays in the log where it belongs.
    """
    known = (
        m01_resolver.ResolutionError,
        m02_discovery.DiscoveryError,
        m06_factstore.FactStoreError,
        edgar.EdgarError,
        llm.LlmError,
        PipelineError,
    )
    if isinstance(failure, known):
        return str(failure)
    return "This step could not be completed."


async def _optional(
    tracker: _Tracker,
    module: str,
    work: _Work,
    body: Callable[[_Outcome], Awaitable[None]],
) -> None:
    """Runs a step that is allowed to fail without failing the report.

    Everything after XBRL extraction goes through here. The step's own event
    already records the failure and its reason; this just stops it propagating.
    """
    try:
        async with tracker.step(module) as outcome:
            await body(outcome)
    except Exception:  # noqa: BLE001 — the whole point of an optional step
        logger.info(
            "Optional step did not complete; the report continues without it",
            extra={"report_id": tracker.report_id, "pipeline_module": module},
        )


# --- The run -----------------------------------------------------------------


async def run(
    report_id: str,
    ticker: str,
    cik: str,
    depth: AnalysisDepth = AnalysisDepth.STANDARD,
) -> RunOutcome:
    """Generates one report, start to finish.

    Args:
        report_id: The job id the endpoint already answered with.
        ticker: For display and for market data.
        cik: The filer. Resolution has already happened at the API boundary,
            so this is a known identity rather than a query.
        depth: How far to go. Never changes a provenance rule.

    Returns:
        A `RunOutcome`. A failed run returns one too, carrying the reason —
        raising here would leave the caller with a background task that
        vanished rather than a report that says what went wrong.
    """
    profile = DEPTH_PROFILES[str(depth)]
    tracker = _Tracker(report_id)
    work = _Work()
    started = time.monotonic()

    # Scoped here so every model call the run makes is counted against this
    # report and no other, even with several reports in flight at once.
    with llm.usage_scope() as usage:
        try:
            # The process-wide client, not a fresh one. Every EdgarClient
            # shares the token bucket, but a new connection pool per report
            # would be waste — and the shared instance is the one closed at
            # application shutdown.
            client = edgar.get_client()
            await _extract(tracker, work, client, ticker, cik, profile)
        except PipelineError as failure:
            return _failed(report_id, ticker, cik, str(failure), tracker, started)
        except (edgar.EdgarError, m01_resolver.ResolutionError) as failure:
            message = str(failure) or _EDGAR_FAILED
            return _failed(report_id, ticker, cik, message, tracker, started)
        except Exception as failure:  # noqa: BLE001 — never a stack trace
            logger.exception(
                "Report generation failed unexpectedly",
                extra={"report_id": report_id, "ticker": ticker},
                exc_info=failure,
            )
            return _failed(
                report_id,
                ticker,
                cik,
                "This report could not be generated. Try again in a moment.",
                tracker,
                started,
            )

        await _write_and_verify(tracker, work, report_id, depth)
        document = await _assemble(tracker, work, report_id, depth)

    duration = _elapsed_ms(started)
    report = _report(report_id, ticker, cik, ReportStatus.COMPLETE, duration)
    runlog.set_status(report_id, ReportStatus.COMPLETE)

    compliance = work.compliance
    if compliance is not None:
        runlog.finish(
            report_id,
            RunSummary(
                duration_ms=duration,
                fact_count=compliance.fact_count,
                figure_count=compliance.figure_count,
                cited_figure_count=compliance.cited_figure_count,
                coverage_ratio=compliance.coverage_ratio,
                compliance_passed=compliance.passed,
                input_tokens=usage.usage.input_tokens,
                output_tokens=usage.usage.output_tokens,
                cost_usd=usage.usage.cost_usd,
            ),
        )

    if document is not None:
        # The report object on the document is rebuilt so its status and
        # completion time match what was just written, rather than the queued
        # placeholder the assembler was handed.
        document = document.model_copy(update={"report": report})
        runlog.record_document(report_id, document)

    logger.info(
        "Report complete",
        extra={
            "report_id": report_id,
            "ticker": ticker,
            "duration_ms": duration,
            "facts": len(work.facts),
            "coverage": compliance.coverage_display if compliance else None,
            "cost_usd": round(usage.usage.cost_usd, 4),
            "warnings": len(tracker.warnings),
        },
    )

    return RunOutcome(
        report=report,
        document=document,
        compliance=compliance,
        duration_ms=duration,
        facts=len(work.facts),
        warnings=tuple(tracker.warnings),
    )


# --- Phases ------------------------------------------------------------------


async def _extract(
    tracker: _Tracker,
    work: _Work,
    client: EdgarClient,
    ticker: str,
    cik: str,
    profile: DepthProfile,
) -> None:
    """Everything that reads from a source. Raises only on a hard dependency."""
    report_id = tracker.report_id
    runlog.set_status(report_id, ReportStatus.RESOLVING)

    async with tracker.step("m01") as outcome:
        # Raises rather than returning None: a CIK that EDGAR does not know is
        # a failed report, not a report about an unnamed filer.
        company = await m01_resolver.load_company(client, cik, ticker)
        work.company = company
        runlog.register_company(company)
        outcome.done()

    async with tracker.step("m02") as outcome:
        work.manifest = await m02_discovery.build_manifest(
            company, client, include_exhibits=profile.developments
        )
        outcome.done(len(work.manifest))

    filings = m02_discovery.as_filings(work.manifest)
    runlog.set_status(report_id, ReportStatus.EXTRACTING)

    async with tracker.step("m03") as outcome:
        reported = await m03_financials.extract_financials(company, filings)
        if profile.segments:
            reported.extend(
                await m03_financials.extract_segments(company, filings)
            )
        work.facts.extend(reported)
        outcome.done(len(reported))

    if not any(fact.value is not None for fact in work.facts):
        message = (
            "No reported figures could be extracted for this filer. It may "
            "not file XBRL financial data with the SEC."
        )
        raise PipelineError(message)

    async def narrative(outcome: _Outcome) -> None:
        if not profile.narrative:
            outcome.skip("Not read at this analysis depth.")
            return
        found = await m04_narrative.extract_narrative(company, filings)
        work.facts.extend(found)
        if not found:
            outcome.skip(
                "No narrative item could be located in the annual report.", 0
            )
            return
        outcome.done(len(found))

    await _optional(tracker, "m04", work, narrative)

    async def market(outcome: _Outcome) -> None:
        quotes = await m05_market.fetch_market_data(company)
        work.facts.extend(quotes)
        if not quotes:
            outcome.skip(
                "No market data provider answered. The report is generated "
                "without the valuation figures.",
                0,
            )
            return
        outcome.done(len(quotes))

    await _optional(tracker, "m05", work, market)

    runlog.set_status(report_id, ReportStatus.ANALYSING)

    async def analyse(outcome: _Outcome) -> None:
        derived = m07_analysis.compute_derived_metrics(
            list(work.facts), sic_code=company.sic_code
        )
        work.facts.extend(derived)
        outcome.done(len(derived))

    await _optional(tracker, "m07", work, analyse)

    async def peers(outcome: _Outcome) -> None:
        if not profile.peers:
            outcome.skip("Not selected at this analysis depth.")
            return
        selected = await m08_peers.select_peers(
            company,
            work.manifest,
            client,
            allow_model=profile.allow_model_peers,
        )
        work.peers = selected
        peer_derived = m08_peers.peer_facts(selected, company)
        work.facts.extend(peer_derived)
        if not selected.peers:
            outcome.skip(
                "No filed source named a comparable company for this filer.",
                0,
            )
            return
        outcome.done(len(selected.peers))

    await _optional(tracker, "m08", work, peers)

    async def developments(outcome: _Outcome) -> None:
        if not profile.developments:
            outcome.skip("Not built at this analysis depth.")
            return
        timeline = await m09_developments.build_timeline(
            company, work.manifest, client
        )
        work.events.extend(timeline.events)
        work.facts.extend(timeline.facts)
        if not timeline.events:
            outcome.skip(
                "This filer has filed no current report covering a "
                "reportable event.",
                0,
            )
            return
        outcome.done(len(timeline.events))

    await _optional(tracker, "m09", work, developments)


async def _write_and_verify(
    tracker: _Tracker, work: _Work, report_id: str, depth: AnalysisDepth
) -> None:
    """The gate, the prose, and the blocking verification."""
    company = work.company
    if company is None:  # pragma: no cover — _extract raises first
        return

    async def store(outcome: _Outcome) -> None:
        result = await m06_factstore.store_facts(report_id, work.facts)
        # The run carries on with exactly what the gate admitted. A fact that
        # was refused does not reach the writer, the checker or the document.
        work.facts = list(result.accepted)
        if result.rejected:
            work.warnings.append(
                f"{result.rejected} fact(s) refused at the provenance gate."
            )
        if not result.persisted:
            outcome.skip(
                "No database is configured, so these facts passed the "
                "provenance gate but were not stored.",
                result.stored,
            )
            return
        outcome.done(result.stored)

    await _optional(tracker, "m06", work, store)

    runlog.set_status(report_id, ReportStatus.WRITING)

    async def write(outcome: _Outcome) -> None:
        if not llm.is_configured():
            outcome.skip(
                "No model is configured. Every figure below is still sourced "
                "and cited.",
                0,
            )
            return
        generated = await m10_writer.write_sections(company, work.facts)
        work.prose = generated
        written = sum(1 for s in generated.sections if s.sentences)
        if not written:
            outcome.skip("No section could be written.", 0)
            return
        outcome.done(written)

    await _optional(tracker, "m10", work, write)

    runlog.set_status(report_id, ReportStatus.VERIFYING)

    async def verify(outcome: _Outcome) -> None:
        compliance = m11_factcheck.verify(
            work.prose or GeneratedReport(), work.facts
        )
        work.compliance = compliance
        if not compliance.passed:
            outcome.detail = (
                f"{len(compliance.blocking_violations)} blocking finding(s). "
                "The figures they concern are shown with the finding beside "
                "them."
            )
            outcome.state = StepState.FAILED
            outcome.count = len(compliance.checks)
            return
        outcome.done(len(compliance.checks))

    await _optional(tracker, "m11", work, verify)


async def _assemble(
    tracker: _Tracker, work: _Work, report_id: str, depth: AnalysisDepth
) -> ReportDocument | None:
    """Builds the document and publishes the artifacts."""
    company = work.company
    compliance = work.compliance
    if company is None or compliance is None:
        return None

    document: ReportDocument | None = None

    async def assemble(outcome: _Outcome) -> None:
        nonlocal document
        placeholder = _report(
            report_id,
            company.ticker,
            company.cik,
            ReportStatus.VERIFYING,
            duration_ms=None,
        )
        built = m12_assembler.assemble_document(
            m12_assembler.AssemblyInput(
                report=placeholder,
                company=company,
                depth=depth,
                facts=work.facts,
                filings=work.manifest,
                compliance=compliance,
                prose=work.prose,
                peers=work.peers,
                events=work.events,
            )
        )
        artifacts = await m12_assembler.publish(report_id, built)
        work.artifacts = list(artifacts)
        runlog.record_artifacts(report_id, artifacts)
        document = built.model_copy(update={"artifacts": tuple(artifacts)})

        published = sum(1 for ref in artifacts if ref.url is not None)
        if published < len(artifacts):
            missing = [
                f"{ref.kind}: {ref.unavailable_reason}"
                for ref in artifacts
                if ref.url is None and ref.unavailable_reason
            ]
            outcome.skip("; ".join(missing), published)
            return
        outcome.done(published)

    await _optional(tracker, "m12", work, assemble)
    return document


# --- Outcomes ----------------------------------------------------------------


def _report(
    report_id: str,
    ticker: str,
    cik: str | None,
    status: ReportStatus,
    duration_ms: int | None,
) -> Report:
    """The report object the caller is handed back."""
    created = dt.datetime.now(dt.UTC)
    if duration_ms is not None:
        created = created - dt.timedelta(milliseconds=duration_ms)
    known = runlog.get_report(report_id)
    return Report(
        id=report_id,
        ticker=ticker.upper(),
        cik=cik,
        status=status,
        error_message=None,
        created_at=known.created_at if known else created,
        completed_at=(
            dt.datetime.now(dt.UTC)
            if status in (ReportStatus.COMPLETE, ReportStatus.FAILED)
            else None
        ),
    )


def _failed(
    report_id: str,
    ticker: str,
    cik: str | None,
    message: str,
    tracker: _Tracker,
    started: float,
) -> RunOutcome:
    """Settles a run that cannot continue, with the reason on the report."""
    duration = _elapsed_ms(started)
    runlog.set_status(report_id, ReportStatus.FAILED, error_message=message)

    known = runlog.get_report(report_id)
    report = Report(
        id=report_id,
        ticker=ticker.upper(),
        cik=cik,
        status=ReportStatus.FAILED,
        error_message=message,
        created_at=known.created_at if known else dt.datetime.now(dt.UTC),
        completed_at=dt.datetime.now(dt.UTC),
    )

    logger.warning(
        "Report failed",
        extra={
            "report_id": report_id,
            "ticker": ticker,
            "duration_ms": duration,
            "reason": message,
        },
    )
    return RunOutcome(
        report=report,
        document=None,
        compliance=None,
        duration_ms=duration,
        facts=0,
        warnings=(*tracker.warnings, message),
    )
