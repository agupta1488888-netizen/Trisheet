"""FastAPI application entrypoint.

Wiring only. Report generation is orchestrated in `app.pipeline`; nothing in
this file knows anything about filings, figures or prose.

Jobs, not requests
    Generating a report means reading dozens of documents from EDGAR and
    writing prose about them, which takes far longer than a browser will hold a
    connection open. `POST /reports` therefore registers the job, answers with
    its id, and returns; the run happens in the background and reports its
    progress through `run_logs`, which the browser follows over Realtime and
    falls back to polling this API for.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import (
    DEFAULT_DEPTH,
    MAX_RESOLUTION_CANDIDATES,
    METRICS_DEFAULT_WINDOW_HOURS,
    METRICS_MAX_WINDOW_HOURS,
    Settings,
    get_settings,
)
from app.logging_config import configure_logging
from app.models import (
    AnalysisDepth,
    ApiError,
    ArtifactKind,
    ChatRequest,
    ChatTurn,
    CreateReportRequest,
    HealthResponse,
    Report,
    ReportDocument,
    ReportMetrics,
    ReportStatus,
    Resolution,
    ResolveRequest,
    SuggestionsResponse,
    TickerSuggestion,
)
from app.modules import chat_agent, m01_resolver
from app.pipeline import run as run_pipeline
from app.services import edgar, llm, metrics, runlog

logger = logging.getLogger(__name__)

API_TITLE = "Trisheet"
API_DESCRIPTION = "Company profiles, sourced from filings."
API_VERSION = "0.1.0"
HEALTH_PATH = "/health"

#: Suggestions returned by autocomplete. Enough to recognise the right filer,
#: few enough to read without scrolling.
MAX_SUGGESTIONS = 10

#: Background tasks are held so the event loop keeps a strong reference to
#: them. Without this a running report can be garbage collected mid-flight.
_running: set[asyncio.Task[object]] = set()


def _error(
    status: int, code: str, message: str, detail: str | None = None
) -> JSONResponse:
    """A typed failure. The API never returns a bare string error."""
    body = ApiError(code=code, message=message, detail=detail)
    return JSONResponse(status_code=status, content=body.model_dump())


def create_app(settings: Settings | None = None) -> FastAPI:
    """Builds the application. Accepts settings so tests can inject their own."""
    resolved = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved.log_level)

        if not resolved.edgar_configured:
            # EDGAR is the only hard dependency. Warn loudly rather than crash,
            # so /health can report the condition instead of the process dying.
            logger.warning(
                "EDGAR contact email is not set; SEC requests will be refused",
                extra={"setting": "EDGAR_CONTACT_EMAIL"},
            )

        if not runlog.is_durable():
            logger.warning(
                "No database configured; run records are held in memory only",
                extra={"setting": "SUPABASE_URL"},
            )

        logger.info(
            "Trisheet backend started",
            extra={"environment": resolved.environment},
        )
        yield
        await edgar.close_client()
        logger.info("Trisheet backend stopped")

    app = FastAPI(
        title=API_TITLE,
        description=API_DESCRIPTION,
        version=API_VERSION,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """Never leak a stack trace to the browser."""
        logger.exception(
            "Unhandled error", extra={"path": request.url.path}, exc_info=exc
        )
        return _error(
            500,
            "internal_error",
            "Something failed on our side. Try again in a moment.",
        )

    @app.get(HEALTH_PATH, response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        """Liveness probe. Reports whether the one hard dependency is wired."""
        return HealthResponse(
            status="ok",
            environment=resolved.environment,
            edgar_configured=resolved.edgar_configured,
        )

    @app.get("/debug/anthropic-net", include_in_schema=False)
    async def debug_anthropic_net() -> dict[str, object]:
        """TEMPORARY. Diagnoses the production "Connection error" reaching
        Anthropic by running DNS, a raw TLS handshake and an httpx request
        directly, so the real underlying exception is visible instead of the
        SDK's generic message. Remove once the root cause is found.
        """
        import socket
        import ssl
        import time

        import httpx

        result: dict[str, object] = {}

        try:
            infos = socket.getaddrinfo(
                "api.anthropic.com", 443, proto=socket.IPPROTO_TCP
            )
            addresses: list[str] = sorted({str(info[4][0]) for info in infos})
            result["dns"] = addresses
        except Exception as cause:  # noqa: BLE001 — diagnostic, wants everything
            result["dns_error"] = repr(cause)
            return result

        ipv4 = next((addr for addr in addresses if ":" not in addr), None)
        if ipv4 is not None:
            try:
                started = time.monotonic()
                with socket.create_connection((ipv4, 443), timeout=10) as sock:
                    result["tcp_connect_ms"] = round(
                        (time.monotonic() - started) * 1000
                    )
                    ctx = ssl.create_default_context()
                    started = time.monotonic()
                    with ctx.wrap_socket(
                        sock, server_hostname="api.anthropic.com"
                    ) as tls:
                        result["tls_handshake_ms"] = round(
                            (time.monotonic() - started) * 1000
                        )
                        result["tls_version"] = tls.version()
                        result["tls_cipher"] = tls.cipher()
                        cert = tls.getpeercert()
                        result["cert_subject"] = cert.get("subject") if cert else None
                        result["cert_not_after"] = (
                            cert.get("notAfter") if cert else None
                        )
            except Exception as cause:  # noqa: BLE001 — diagnostic, wants everything
                result["raw_tls_error"] = repr(cause)
                result["raw_tls_error_type"] = type(cause).__name__

        try:
            started = time.monotonic()
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": "diagnostic-only-invalid-key",
                        "anthropic-version": "2023-06-01",
                    },
                )
                result["httpx_ms"] = round((time.monotonic() - started) * 1000)
                result["httpx_status"] = response.status_code
                result["httpx_http_version"] = response.http_version
        except Exception as cause:  # noqa: BLE001 — diagnostic, wants everything
            result["httpx_error"] = repr(cause)
            result["httpx_error_type"] = type(cause).__name__

        try:
            from app.services import llm

            llm.reset_client()
            await llm.complete_json(
                system="Reply with {\"ok\": true}.",
                user="ping",
                schema={
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                },
                purpose="debug:ping",
                max_tokens=16,
            )
            result["sdk_call"] = "ok"
        except Exception as cause:  # noqa: BLE001 — diagnostic, wants everything
            result["sdk_error"] = repr(cause)
            result["sdk_error_type"] = type(cause).__name__
            inner = getattr(cause, "__cause__", None)
            if inner is not None:
                result["sdk_error_cause"] = repr(inner)

        return result

    # --- Resolution ---------------------------------------------------------

    @app.post("/resolve", response_model=None, tags=["resolve"])
    async def resolve(request: ResolveRequest) -> Response | Resolution:
        """Resolves free text to a filer.

        An ambiguous query is a successful response carrying candidates, not a
        failure — the interface asks rather than guessing.
        """
        if not resolved.edgar_configured:
            return _edgar_unconfigured()

        try:
            return await m01_resolver.resolve(request.query, edgar.get_client())
        except (m01_resolver.ResolutionError, edgar.EdgarError) as failure:
            return _error(502, "resolution_failed", str(failure))

    @app.get("/resolve/suggest", tags=["resolve"])
    async def suggest(
        q: str = Query(min_length=1),
        limit: int = Query(default=MAX_SUGGESTIONS, ge=1, le=MAX_SUGGESTIONS),
    ) -> SuggestionsResponse:
        """Autocomplete against the EDGAR ticker index.

        Returns an empty list on any failure rather than an error: a suggestion
        list that cannot load is not something the reader must act on. They can
        still type a ticker, and the resolver has the final say.
        """
        if not resolved.edgar_configured:
            return SuggestionsResponse()

        try:
            index = await m01_resolver.load_index(edgar.get_client())
        except (m01_resolver.ResolutionError, edgar.EdgarError):
            logger.warning("Ticker index unavailable for autocomplete")
            return SuggestionsResponse()

        needle = q.strip().lower()
        matches = [
            entry
            for entry in index
            if entry.ticker.lower().startswith(needle)
            or needle in entry.name.lower()
        ]
        # A ticker prefix is a stronger signal than a name substring, so exact
        # and prefix ticker matches are offered first.
        matches.sort(
            key=lambda entry: (
                0 if entry.ticker.lower() == needle else 1,
                0 if entry.ticker.lower().startswith(needle) else 1,
                len(entry.ticker),
            )
        )
        return SuggestionsResponse(
            suggestions=tuple(
                TickerSuggestion(
                    cik=entry.cik, ticker=entry.ticker, name=entry.name
                )
                for entry in matches[:limit]
            )
        )

    # --- Reports ------------------------------------------------------------

    @app.post("/reports", status_code=202, response_model=None, tags=["reports"])
    async def create_report(request: CreateReportRequest) -> Response | Report:
        """Queues a report and answers immediately with its job id.

        The run itself happens in the background. Nothing here waits on EDGAR:
        a request that took as long as a report would time out in the browser
        long before the report was ready.
        """
        if not resolved.edgar_configured:
            return _edgar_unconfigured()

        report = runlog.create_report(
            request.ticker, request.cik, request.depth
        )

        task = asyncio.create_task(
            run_pipeline(
                report.id,
                request.ticker,
                request.cik,
                request.depth,
                request.periods,
            ),
            name=f"report:{report.id}",
        )
        _running.add(task)
        task.add_done_callback(_running.discard)

        logger.info(
            "Report queued",
            extra={
                "report_id": report.id,
                "ticker": report.ticker,
                "cik": request.cik,
                "depth": str(request.depth),
            },
        )
        return report

    @app.get("/reports/{report_id}", response_model=None, tags=["reports"])
    async def fetch_report(report_id: str) -> Response | Report:
        """The current state of a run. Polled when Realtime is unavailable."""
        report = runlog.get_report(report_id)
        if report is None:
            return _unknown_report()
        return report

    @app.get("/reports/{report_id}/document", response_model=None, tags=["reports"])
    async def fetch_document(report_id: str) -> Response | ReportDocument:
        """The assembled document. Only meaningful once the run is complete."""
        report = runlog.get_report(report_id)
        if report is None:
            return _unknown_report()

        if report.status is ReportStatus.FAILED:
            return _error(
                409,
                "report_failed",
                report.error_message
                or "This report could not be generated.",
            )

        if report.status is not ReportStatus.COMPLETE:
            return _error(
                409,
                "report_incomplete",
                "This report is still being generated. The progress screen "
                "will show it when it is ready.",
                detail=str(report.status),
            )

        document = runlog.document_for(report_id)
        if not isinstance(document, ReportDocument):
            return _error(
                410,
                "document_unavailable",
                "This report is no longer held on the server. Generate it "
                "again to read it.",
            )
        return document

    @app.post(
        "/reports/{report_id}/chat", response_model=None, tags=["reports"]
    )
    async def chat(report_id: str, request: ChatRequest) -> Response | ChatTurn:
        """Answers one question about a completed report.

        Only meaningful once the run is complete, and only when a model is
        configured — a question the assistant cannot reach the model to
        answer is refused up front rather than answered with a stack trace.
        """
        report = runlog.get_report(report_id)
        if report is None:
            return _unknown_report()

        if report.status is not ReportStatus.COMPLETE:
            return _error(
                409,
                "chat_unavailable",
                "This report is still being generated. The assistant can "
                "answer questions once it is complete.",
                detail=str(report.status),
            )

        if not llm.is_configured():
            return _error(
                503,
                "chat_unconfigured",
                "The assistant is not configured right now.",
            )

        return await chat_agent.answer_question(report_id, request.message)

    @app.get("/reports/{report_id}/artifacts/{kind}", tags=["reports"])
    async def fetch_artifact(report_id: str, kind: ArtifactKind) -> Response:
        """Redirects to a rendered artifact, or says why there is not one."""
        for artifact in runlog.artifacts_for(report_id):
            if artifact.kind is not kind:
                continue
            if artifact.url is None:
                return _error(
                    409,
                    "artifact_unavailable",
                    artifact.unavailable_reason
                    or "This file was not published.",
                )
            return Response(
                status_code=307, headers={"Location": str(artifact.url)}
            )

        return _error(
            404,
            "artifact_not_found",
            f"No {str(kind).upper()} was rendered for this report.",
        )

    # --- Monitoring ---------------------------------------------------------

    @app.get("/metrics", response_model=ReportMetrics, tags=["system"])
    async def report_metrics(
        window_hours: int = Query(
            default=METRICS_DEFAULT_WINDOW_HOURS,
            ge=1,
            le=METRICS_MAX_WINDOW_HOURS,
        ),
    ) -> ReportMetrics:
        """Report success rate, latency, citation coverage and cost."""
        return metrics.summarise(window_hours)

    return app


def _edgar_unconfigured() -> JSONResponse:
    return _error(
        503,
        "edgar_unconfigured",
        "This service is not configured to contact SEC EDGAR, so no report "
        "can be generated. Set a contact email and restart.",
    )


def _unknown_report() -> JSONResponse:
    return _error(
        404,
        "report_not_found",
        "No report with that id. It may have been generated by a server that "
        "has since restarted.",
    )


#: Default depth, resolved once so the value in config is the only one.
DEFAULT_ANALYSIS_DEPTH = AnalysisDepth(DEFAULT_DEPTH)

#: Kept for the resolver's candidate cap, which the interface renders.
RESOLUTION_CANDIDATE_CAP = MAX_RESOLUTION_CANDIDATES

app = create_app()
