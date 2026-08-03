"""Tests for chat_agent — the anti-hallucination gate matters more here than
anywhere else in the pipeline, because this is the one place a model answers
something a human just asked, in real time, with no m11 pass to catch it
after the fact. Every path below is a way the assistant could be tempted to
answer beyond what it was actually given, and confirms it does not.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.models import ChatRole, Report, ReportStatus
from app.modules import chat_agent
from app.services import db, llm, runlog
from tests.conftest import make_fact

REPORT_ID = "22222222-2222-2222-2222-222222222222"


def _report(status: ReportStatus = ReportStatus.COMPLETE) -> Report:
    return Report(
        id=REPORT_ID,
        ticker="AAPL",
        cik="0000320193",
        status=status,
        created_at=dt.datetime(2024, 11, 1, tzinfo=dt.UTC),
        completed_at=dt.datetime(2024, 11, 1, tzinfo=dt.UTC),
    )


@pytest.fixture(autouse=True)
def _report_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runlog, "get_report", lambda report_id: _report())


@pytest.fixture(autouse=True)
def _no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persistence is a sink; tests exercise the answer, not the write.

    `is_configured` returning False means `_persist` returns immediately, so a
    test that never sets up a fake Supabase client still runs the exact code
    path a misconfigured deployment would.
    """
    monkeypatch.setattr(db, "is_configured", lambda: False)


def _refuse_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fails the test if the model is called when it should not be.

    The not-found path must be decided before any request reaches the model —
    that is the whole point of matching in Python first.
    """

    async def _fail(*args: Any, **kwargs: Any) -> dict[str, Any]:
        message = "The model should not have been called for this question."
        raise AssertionError(message)

    monkeypatch.setattr(llm, "complete_json", _fail)


def _stub_llm(
    monkeypatch: pytest.MonkeyPatch, answer: dict[str, Any]
) -> None:
    async def _answer(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return answer

    monkeypatch.setattr(llm, "complete_json", _answer)


async def test_tier_1_answers_from_an_already_stored_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revenue = make_fact(metric="income.revenue", label="Revenue")

    async def _load_facts(report_id: str) -> list[Any]:
        return [revenue]

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)
    _stub_llm(
        monkeypatch,
        {
            "claims": [
                {"text": "Revenue was 391,035,000,000.", "fact_id": revenue.fact_id}
            ],
            "not_found": False,
        },
    )

    turn = await chat_agent.answer_question(REPORT_ID, "What was revenue?")

    assert turn.role is ChatRole.ASSISTANT
    assert not turn.not_found
    assert len(turn.claims) == 1
    claim = turn.claims[0]
    assert claim.fact_id == revenue.fact_id
    assert claim.tier == revenue.tier
    assert claim.accession_no == revenue.accession_no
    assert not claim.not_found


async def test_no_matching_fact_is_not_found_without_calling_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revenue = make_fact(metric="income.revenue", label="Revenue")

    async def _load_facts(report_id: str) -> list[Any]:
        return [revenue]

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)
    _refuse_llm(monkeypatch)

    turn = await chat_agent.answer_question(
        REPORT_ID, "What is the capital of France?"
    )

    assert turn.not_found
    assert len(turn.claims) == 1
    assert turn.claims[0].not_found
    assert turn.claims[0].fact_id is None
    assert turn.claims[0].tier is None


async def test_model_citing_an_unsupplied_fact_id_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The core anti-hallucination check: a citation to a fact id that was
    never in the table offered to the model does not survive, even if the
    model's prose sounds confident."""
    revenue = make_fact(metric="income.revenue", label="Revenue")

    async def _load_facts(report_id: str) -> list[Any]:
        return [revenue]

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)
    _stub_llm(
        monkeypatch,
        {
            "claims": [
                {
                    "text": "Revenue was 400 billion dollars.",
                    "fact_id": "fact_not_in_the_supplied_table",
                }
            ],
            "not_found": False,
        },
    )

    turn = await chat_agent.answer_question(REPORT_ID, "What was revenue?")

    assert turn.not_found
    assert all(
        claim.fact_id != "fact_not_in_the_supplied_table"
        for claim in turn.claims
    )


async def test_model_declining_to_answer_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revenue = make_fact(metric="income.revenue", label="Revenue")

    async def _load_facts(report_id: str) -> list[Any]:
        return [revenue]

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)
    _stub_llm(monkeypatch, {"claims": [], "not_found": True})

    turn = await chat_agent.answer_question(
        REPORT_ID, "What was revenue in a currency it does not report in?"
    )

    assert turn.not_found


async def test_tier_2_recomputes_a_metric_the_report_never_emitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A current ratio needs current assets and current liabilities, both
    reported but neither itself a match for the question — only the wider
    m07 pass computing "derived.current_ratio" produces something to cite."""
    current_assets = make_fact(
        metric="balance.current_assets",
        label="Total current assets",
        value=100.0,
        display_value="100",
        fiscal_year=2024,
        period_start=None,
        period_end=dt.date(2024, 9, 28),
    )
    current_liabilities = make_fact(
        metric="balance.current_liabilities",
        label="Total current liabilities",
        value=50.0,
        display_value="50",
        fiscal_year=2024,
        period_start=None,
        period_end=dt.date(2024, 9, 28),
    )

    async def _load_facts(report_id: str) -> list[Any]:
        return [current_assets, current_liabilities]

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)

    captured: dict[str, Any] = {}

    async def _answer(
        system: str, user: str, schema: dict[str, Any], *, purpose: str
    ) -> dict[str, Any]:
        captured["user"] = user
        # Whatever candidate fact m07 derived for "current ratio" is the one
        # fact_id offered — assert on shape, not on m07's internal formula.
        first_fact_id = user.split("\n")[-2].split(" | ")[0]
        return {
            "claims": [{"text": "The current ratio is 2.0.", "fact_id": first_fact_id}],
            "not_found": False,
        }

    monkeypatch.setattr(llm, "complete_json", _answer)

    turn = await chat_agent.answer_question(
        REPORT_ID, "What is the current ratio?"
    )

    assert not turn.not_found
    assert len(turn.claims) == 1
    assert turn.claims[0].tier is not None


async def test_valuation_question_never_calls_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DCF path is pure Python arithmetic over real facts — there is
    nothing for the model to decide, so it is never invoked, which also means
    a valuation question costs nothing beyond the calculation itself."""
    fcf = make_fact(
        metric="cashflow.free_cash_flow",
        label="Free cash flow",
        value=100.0,
        display_value="100",
        fiscal_year=2024,
        period_start=None,
        period_end=dt.date(2024, 9, 28),
        is_calculated=True,
        formula="net cash from operating activities − capital expenditure",
    )

    async def _load_facts(report_id: str) -> list[Any]:
        return [fcf]

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)
    _refuse_llm(monkeypatch)

    turn = await chat_agent.answer_question(
        REPORT_ID, "What is the DCF valuation of this company?"
    )

    assert not turn.not_found
    certified = [claim for claim in turn.claims if not claim.is_assumption]
    assumptions = [claim for claim in turn.claims if claim.is_assumption]
    assert len(certified) == 1
    assert certified[0].fact_id == fcf.fact_id
    assert certified[0].tier == fcf.tier
    # The rates, and the resulting estimate, both come back as assumptions —
    # neither is ever presented as a filed figure.
    assert len(assumptions) == 2
    assert all(claim.assumption_note for claim in assumptions)
    assert all(claim.tier is None and claim.fact_id is None for claim in assumptions)


async def test_valuation_question_with_no_free_cash_flow_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revenue = make_fact(metric="income.revenue", label="Revenue")

    async def _load_facts(report_id: str) -> list[Any]:
        return [revenue]

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)
    _refuse_llm(monkeypatch)

    turn = await chat_agent.answer_question(
        REPORT_ID, "What is this company worth?"
    )

    assert turn.not_found
