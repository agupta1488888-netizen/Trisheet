"""Tests for the JSON log formatter.

The formatter runs inside every other module's error path, so a failure here
is a failure to report a failure — the worst place to have one. These tests
exercise it directly rather than through a caller.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from app.logging_config import JsonFormatter, report_id_var, set_report_id


def _emit(**kwargs: Any) -> dict[str, Any]:
    """Formats one record and returns the parsed line."""
    logger = logging.getLogger("app.tests.formatter")
    record = logger.makeRecord(
        logger.name, logging.WARNING, "test.py", 1, "Something happened", (), None
    )
    for key, value in kwargs.items():
        setattr(record, key, value)

    parsed: Any = json.loads(JsonFormatter().format(record))
    assert isinstance(parsed, dict)
    return parsed


def test_a_record_renders_as_one_json_object() -> None:
    payload = _emit()

    assert payload["level"] == "WARNING"
    assert payload["logger"] == "app.tests.formatter"
    assert payload["message"] == "Something happened"
    assert "timestamp" in payload


def test_structured_context_is_carried_through() -> None:
    payload = _emit(ticker="NKE", facts=498)

    assert payload["ticker"] == "NKE"
    assert payload["facts"] == 498


def test_an_exception_is_reported_as_its_type_and_message() -> None:
    try:
        message = "connection reset"
        raise RuntimeError(message)
    except RuntimeError as cause:
        exc_info = (type(cause), cause, cause.__traceback__)

    payload = _emit(exc_info=exc_info)

    assert payload["error"] == "RuntimeError: connection reset"


def test_a_declined_traceback_is_not_an_error_to_report() -> None:
    """A caller may decide at runtime that a failure needs no traceback.

    `exc_info=False` is stored on the record unchanged, so treating anything
    that is not None as a traceback triples the fault: the line is lost, the
    handler raises, and the original failure goes unreported.
    """
    payload = _emit(exc_info=False)

    assert "error" not in payload
    assert payload["message"] == "Something happened"


def test_a_record_with_no_exception_carries_no_error_field() -> None:
    assert "error" not in _emit(exc_info=(None, None, None))


def test_lines_emitted_during_a_run_carry_its_report_id() -> None:
    token = report_id_var.set(None)
    try:
        set_report_id("11111111-1111-1111-1111-111111111111")
        payload = _emit()
    finally:
        report_id_var.reset(token)

    assert payload["report_id"] == "11111111-1111-1111-1111-111111111111"
    assert "report_id" not in _emit()


@pytest.mark.parametrize("value", [object(), {1, 2}])
def test_a_value_json_cannot_encode_is_stringified_not_dropped(value: Any) -> None:
    """A log line is never lost to an unserialisable piece of context."""
    payload = _emit(context=value)

    assert isinstance(payload["context"], str)
