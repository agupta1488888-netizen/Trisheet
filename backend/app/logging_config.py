"""Structured JSON logging, correlated by report_id.

`print()` is never used anywhere in this codebase. Every log line is a single
JSON object on one line so that Railway and any log shipper can parse it.

The active report_id is carried in a context variable, so every line emitted
while handling a report is automatically correlated without threading the id
through every call signature.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from contextvars import ContextVar
from types import TracebackType
from typing import Any

#: Set for the duration of a report run; None outside one.
report_id_var: ContextVar[str | None] = ContextVar("report_id", default=None)

#: Attributes the stdlib puts on every record. Anything else a caller attaches
#: via `extra=` is treated as structured context and included in the output.
_RESERVED_RECORD_KEYS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def _format_exception(
    exc_info: tuple[type[BaseException], BaseException, TracebackType | None]
    | tuple[None, None, None],
) -> str | None:
    exc_type, exc_value, _ = exc_info
    if exc_type is None or exc_value is None:
        return None
    return f"{exc_type.__name__}: {exc_value}"


class JsonFormatter(logging.Formatter):
    """Renders a log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": dt.datetime.fromtimestamp(
                record.created, tz=dt.UTC
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        report_id = report_id_var.get()
        if report_id is not None:
            payload["report_id"] = report_id

        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_KEYS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info is not None:
            payload["error"] = _format_exception(record.exc_info)

        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: str) -> None:
    """Installs the JSON formatter as the only handler on the root logger.

    Idempotent: existing handlers are replaced, so repeated calls during
    application reload do not produce duplicate lines.
    """
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs its own coloured handlers; route them through ours.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True


def set_report_id(report_id: str | None) -> None:
    """Binds the report_id that subsequent log lines will carry."""
    report_id_var.set(report_id)
