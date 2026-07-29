"""FastAPI application entrypoint.

Wiring only. Report generation is orchestrated in `app.modules`; nothing in
this file knows anything about filings, figures or prose.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.logging_config import configure_logging
from app.models import ApiError, HealthResponse

logger = logging.getLogger(__name__)

API_TITLE = "Tearsheet"
API_DESCRIPTION = "Company profiles, sourced from filings."
API_VERSION = "0.1.0"
HEALTH_PATH = "/health"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    if not settings.edgar_configured:
        # EDGAR is the only hard dependency. Warn loudly rather than crash, so
        # that /health can report the condition instead of the process dying.
        logger.warning(
            "EDGAR contact email is not set; SEC requests will be refused",
            extra={"setting": "EDGAR_CONTACT_EMAIL"},
        )

    logger.info(
        "Tearsheet backend started", extra={"environment": settings.environment}
    )
    yield
    logger.info("Tearsheet backend stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Builds the application. Accepts settings so tests can inject their own."""
    resolved = settings if settings is not None else get_settings()

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
        error = ApiError(
            code="internal_error",
            message="Something failed on our side. Try again in a moment.",
            detail=None,
        )
        return JSONResponse(status_code=500, content=error.model_dump())

    @app.get(HEALTH_PATH, response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        """Liveness probe. Reports whether the one hard dependency is wired."""
        return HealthResponse(
            status="ok",
            environment=resolved.environment,
            edgar_configured=resolved.edgar_configured,
        )

    return app


app = create_app()
