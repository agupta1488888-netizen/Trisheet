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

from app.config import CHAT_RATE_LIMIT_MAX_TURNS
from app.models import ChatRole, Report, ReportStatus, SourceTier, SourceType
from app.modules import chat_agent
from app.services import db, llm, runlog, webfetch
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


async def test_no_pasted_url_stays_not_found_without_fetching_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The company-site fallback must never fire on its own — only when the
    reader actually supplied a URL."""
    revenue = make_fact(metric="income.revenue", label="Revenue")

    async def _load_facts(report_id: str) -> list[Any]:
        return [revenue]

    async def _refuse_fetch(url: str) -> None:
        message = "fetch_page should not have been called without a pasted_url"
        raise AssertionError(message)

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)
    monkeypatch.setattr(chat_agent.webfetch, "fetch_page", _refuse_fetch)
    _refuse_llm(monkeypatch)

    turn = await chat_agent.answer_question(
        REPORT_ID, "What is the capital of France?"
    )

    assert turn.not_found


async def test_pasted_url_answers_when_the_report_facts_come_up_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revenue = make_fact(metric="income.revenue", label="Revenue")

    async def _load_facts(report_id: str) -> list[Any]:
        return [revenue]

    async def _fetch_page(url: str) -> webfetch.FetchedPage:
        return webfetch.FetchedPage(
            url="https://example.com/about",
            text="Example Corp was founded in 1999 in Springfield.",
        )

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)
    monkeypatch.setattr(chat_agent.webfetch, "fetch_page", _fetch_page)
    _stub_llm(
        monkeypatch,
        {
            "claims": [{"text": "The company was founded in 1999."}],
            "not_found": False,
        },
    )

    turn = await chat_agent.answer_question(
        REPORT_ID,
        "When was the company founded?",
        pasted_url="https://example.com/about",
    )

    assert not turn.not_found
    assert len(turn.claims) == 1
    claim = turn.claims[0]
    assert claim.fact_id is None
    assert str(claim.source_url) == "https://example.com/about"
    assert claim.source_type == SourceType.COMPANY_SITE
    assert claim.tier is not None


async def test_pasted_url_that_cannot_be_fetched_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revenue = make_fact(metric="income.revenue", label="Revenue")

    async def _load_facts(report_id: str) -> list[Any]:
        return [revenue]

    async def _fail_fetch(url: str) -> webfetch.FetchedPage:
        raise webfetch.WebfetchBlockedError("Refused: not a public address.")

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)
    monkeypatch.setattr(chat_agent.webfetch, "fetch_page", _fail_fetch)
    _refuse_llm(monkeypatch)

    turn = await chat_agent.answer_question(
        REPORT_ID,
        "When was the company founded?",
        pasted_url="http://169.254.169.254/",
    )

    assert turn.not_found


# --- Rate limiting --------------------------------------------------------


class _FakeRateLimitTable:
    """Serves canned rows regardless of the filter chain — good enough for
    tests that already control exactly what should come back, for both
    `is_rate_limited`'s `.eq().eq().gte()` chain and `_recent_exchange`'s
    `.order().limit()` chain.
    """

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def select(self, columns: str) -> _FakeRateLimitTable:
        return self

    def eq(self, column: str, value: Any) -> _FakeRateLimitTable:
        return self

    def gte(self, column: str, value: Any) -> _FakeRateLimitTable:
        return self

    def order(self, column: str, desc: bool = False) -> _FakeRateLimitTable:
        return self

    def limit(self, count: int) -> _FakeRateLimitTable:
        return self

    def execute(self) -> Any:
        return type("Response", (), {"data": self._rows})()


class _FakeRateLimitClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._table = _FakeRateLimitTable(rows)

    def table(self, name: str) -> _FakeRateLimitTable:
        return self._table


async def test_rate_limit_skipped_when_database_not_configured() -> None:
    # The autouse `_no_database` fixture already sets is_configured to False.
    assert not await chat_agent.is_rate_limited(REPORT_ID)


async def test_rate_limit_allows_when_under_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [{"id": str(i)} for i in range(CHAT_RATE_LIMIT_MAX_TURNS - 1)]
    monkeypatch.setattr(db, "is_configured", lambda: True)
    monkeypatch.setattr(db, "get_client", lambda: _FakeRateLimitClient(rows))

    assert not await chat_agent.is_rate_limited(REPORT_ID)


async def test_rate_limit_blocks_at_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [{"id": str(i)} for i in range(CHAT_RATE_LIMIT_MAX_TURNS)]
    monkeypatch.setattr(db, "is_configured", lambda: True)
    monkeypatch.setattr(db, "get_client", lambda: _FakeRateLimitClient(rows))

    assert await chat_agent.is_rate_limited(REPORT_ID)


async def test_rate_limit_degrades_to_allowed_on_read_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingClient:
        def table(self, name: str) -> Any:
            message = "db unreachable"
            raise RuntimeError(message)

    monkeypatch.setattr(db, "is_configured", lambda: True)
    monkeypatch.setattr(db, "get_client", lambda: _FailingClient())

    assert not await chat_agent.is_rate_limited(REPORT_ID)


# --- Web search (tier 4) --------------------------------------------------


async def test_web_search_is_never_tried_while_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`CHAT_WEB_SEARCH_ENABLED` is off by default — confirms the flag
    actually gates the call, not just the config default."""
    revenue = make_fact(metric="income.revenue", label="Revenue")

    async def _load_facts(report_id: str) -> list[Any]:
        return [revenue]

    async def _refuse_web_search(*args: Any, **kwargs: Any) -> dict[str, Any]:
        message = "web search should not run while CHAT_WEB_SEARCH_ENABLED is False"
        raise AssertionError(message)

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)
    monkeypatch.setattr(chat_agent, "CHAT_WEB_SEARCH_ENABLED", False)
    monkeypatch.setattr(llm, "complete_json_with_web_search", _refuse_web_search)
    _refuse_llm(monkeypatch)

    turn = await chat_agent.answer_question(
        REPORT_ID, "What is the capital of France?"
    )

    assert turn.not_found


async def test_web_search_answers_when_enabled_and_everything_else_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revenue = make_fact(metric="income.revenue", label="Revenue")

    async def _load_facts(report_id: str) -> list[Any]:
        return [revenue]

    async def _search(
        system: str, user: str, schema: dict[str, Any], *, purpose: str
    ) -> dict[str, Any]:
        return {
            "claims": [
                {
                    "text": "The company was founded in 1999.",
                    "source_url": "https://news.example.com/story",
                }
            ],
            "not_found": False,
        }

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)
    monkeypatch.setattr(chat_agent, "CHAT_WEB_SEARCH_ENABLED", True)
    monkeypatch.setattr(llm, "complete_json_with_web_search", _search)
    _refuse_llm(monkeypatch)  # complete_json (not the web-search variant) unused

    turn = await chat_agent.answer_question(
        REPORT_ID, "When was the company founded?"
    )

    assert not turn.not_found
    assert len(turn.claims) == 1
    claim = turn.claims[0]
    assert claim.tier == SourceTier.NEWS
    assert str(claim.source_url) == "https://news.example.com/story"
    assert claim.fact_id is None


async def test_web_search_drops_a_claim_with_an_unusable_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revenue = make_fact(metric="income.revenue", label="Revenue")

    async def _load_facts(report_id: str) -> list[Any]:
        return [revenue]

    async def _search(
        system: str, user: str, schema: dict[str, Any], *, purpose: str
    ) -> dict[str, Any]:
        return {
            "claims": [
                {"text": "This should not survive.", "source_url": "not-a-url"}
            ],
            "not_found": False,
        }

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)
    monkeypatch.setattr(chat_agent, "CHAT_WEB_SEARCH_ENABLED", True)
    monkeypatch.setattr(llm, "complete_json_with_web_search", _search)
    _refuse_llm(monkeypatch)

    turn = await chat_agent.answer_question(
        REPORT_ID, "When was the company founded?"
    )

    assert turn.not_found


async def test_pasted_url_is_tried_before_web_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both a pasted URL and web search are available, the reader's own
    explicit fallback wins — web search is not also consulted."""
    revenue = make_fact(metric="income.revenue", label="Revenue")

    async def _load_facts(report_id: str) -> list[Any]:
        return [revenue]

    async def _fetch_page(url: str) -> webfetch.FetchedPage:
        return webfetch.FetchedPage(url=url, text="Founded in 1999.")

    async def _refuse_web_search(*args: Any, **kwargs: Any) -> dict[str, Any]:
        message = "web search should not run when a pasted_url was supplied"
        raise AssertionError(message)

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)
    monkeypatch.setattr(chat_agent, "CHAT_WEB_SEARCH_ENABLED", True)
    monkeypatch.setattr(chat_agent.webfetch, "fetch_page", _fetch_page)
    monkeypatch.setattr(llm, "complete_json_with_web_search", _refuse_web_search)
    _stub_llm(
        monkeypatch,
        {"claims": [{"text": "Founded in 1999."}], "not_found": False},
    )

    turn = await chat_agent.answer_question(
        REPORT_ID,
        "When was the company founded?",
        pasted_url="https://example.com/about",
    )

    assert not turn.not_found
    assert turn.claims[0].source_type == SourceType.COMPANY_SITE


# --- Small talk -------------------------------------------------------------


async def test_greeting_gets_a_canned_reply_without_touching_facts_or_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _refuse_load(report_id: str) -> list[Any]:
        message = "facts should not be loaded for a greeting"
        raise AssertionError(message)

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _refuse_load)
    _refuse_llm(monkeypatch)

    turn = await chat_agent.answer_question(REPORT_ID, "Hello!")

    assert not turn.not_found
    assert turn.claims == ()
    assert turn.content != ""


async def test_thanks_gets_a_canned_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _refuse_load(report_id: str) -> list[Any]:
        message = "facts should not be loaded for thanks"
        raise AssertionError(message)

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _refuse_load)
    _refuse_llm(monkeypatch)

    turn = await chat_agent.answer_question(REPORT_ID, "thanks!")

    assert turn.content == "You're welcome."


async def test_help_question_gets_a_canned_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _refuse_load(report_id: str) -> list[Any]:
        message = "facts should not be loaded for a help question"
        raise AssertionError(message)

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _refuse_load)
    _refuse_llm(monkeypatch)

    turn = await chat_agent.answer_question(REPORT_ID, "What can you do?")

    assert "valuation" in turn.content.lower()


# --- Peer comparison ----------------------------------------------------


async def test_peer_question_surfaces_a_tier_3_peer_valuation_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Peer valuation multiples (EV/EBITDA, P/E) are tier 3 — market-data
    tainted, per m08_peers's own accounting — and must be citable, not
    dropped, once ChatClaim recognises tier 3 as a valid certified shape."""
    peer_multiple = make_fact(
        metric="peer.comparison.MSFT.ev_to_ebitda",
        label="EV/EBITDA vs MSFT",
        value=15.2,
        display_value="15.2x",
        fiscal_year=2024,
        period_start=None,
        period_end=dt.date(2024, 9, 28),
        tier=SourceTier.MARKET,
        source_type=SourceType.MARKET_DATA,
        is_calculated=True,
        formula="(market cap + total debt - cash) / EBITDA",
    )
    revenue = make_fact(metric="income.revenue", label="Revenue")

    async def _load_facts(report_id: str) -> list[Any]:
        return [peer_multiple, revenue]

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)
    _stub_llm(
        monkeypatch,
        {
            "claims": [
                {
                    "text": "EV/EBITDA versus Microsoft is 15.2x.",
                    "fact_id": peer_multiple.fact_id,
                }
            ],
            "not_found": False,
        },
    )

    turn = await chat_agent.answer_question(
        REPORT_ID, "How does this compare to competitors?"
    )

    assert not turn.not_found
    assert len(turn.claims) == 1
    assert turn.claims[0].tier == SourceTier.MARKET
    assert turn.claims[0].fact_id == peer_multiple.fact_id


async def test_non_peer_question_does_not_pull_in_peer_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ordinary question must not surface peer data it never asked for —
    the trigger words are what decides this is a comparison question."""
    peer_multiple = make_fact(
        metric="peer.comparison.MSFT.ev_to_ebitda",
        label="EV/EBITDA vs MSFT",
        value=15.2,
        display_value="15.2x",
        fiscal_year=2024,
        period_start=None,
        period_end=dt.date(2024, 9, 28),
        tier=SourceTier.MARKET,
        source_type=SourceType.MARKET_DATA,
        is_calculated=True,
        formula="(market cap + total debt - cash) / EBITDA",
    )

    async def _load_facts(report_id: str) -> list[Any]:
        return [peer_multiple]

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)
    _refuse_llm(monkeypatch)

    turn = await chat_agent.answer_question(
        REPORT_ID, "What is the capital of France?"
    )

    assert turn.not_found


# --- Conversation memory --------------------------------------------------


async def test_follow_up_combines_with_the_prior_question_to_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"And last year's?" names no metric on its own — only combined with
    the prior question ("what was revenue") does it match anything."""
    older_revenue = make_fact(
        metric="income.revenue",
        label="Revenue",
        value=350_000_000_000.0,
        display_value="350,000,000,000",
        fiscal_year=2023,
        period_start=dt.date(2022, 10, 1),
        period_end=dt.date(2023, 9, 30),
    )
    newer_revenue = make_fact(
        metric="income.revenue",
        label="Revenue",
        value=391_035_000_000.0,
        display_value="391,035,000,000",
        fiscal_year=2024,
    )

    async def _load_facts(report_id: str) -> list[Any]:
        return [older_revenue, newer_revenue]

    prior_exchange = (
        "What was revenue?",
        "Revenue was 391,035,000,000.",
    )
    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)
    monkeypatch.setattr(
        chat_agent,
        "_recent_exchange",
        lambda report_id: _resolved(prior_exchange),
    )
    _stub_llm(
        monkeypatch,
        {
            "claims": [
                {
                    "text": "Revenue in FY2023 was 350,000,000,000.",
                    "fact_id": older_revenue.fact_id,
                }
            ],
            "not_found": False,
        },
    )

    turn = await chat_agent.answer_question(REPORT_ID, "And last year's?")

    assert not turn.not_found
    assert turn.claims[0].fact_id == older_revenue.fact_id


async def test_no_prior_exchange_means_a_bare_follow_up_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revenue = make_fact(metric="income.revenue", label="Revenue")

    async def _load_facts(report_id: str) -> list[Any]:
        return [revenue]

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)
    monkeypatch.setattr(
        chat_agent, "_recent_exchange", lambda report_id: _resolved(None)
    )
    _refuse_llm(monkeypatch)

    turn = await chat_agent.answer_question(REPORT_ID, "And last year's?")

    assert turn.not_found


def _resolved(value: Any) -> Any:
    """An already-completed coroutine returning `value` — for monkeypatching
    an async function with a plain lambda."""

    async def _coro() -> Any:
        return value

    return _coro()


# --- Remaining-questions counter ------------------------------------------


async def test_turns_remaining_is_none_without_a_database(
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

    assert turn.turns_remaining is None


async def test_turns_remaining_counts_down_with_a_database(
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
    rows = [{"id": str(i)} for i in range(3)]
    monkeypatch.setattr(db, "is_configured", lambda: True)
    monkeypatch.setattr(db, "get_client", lambda: _FakeRateLimitClient(rows))

    turn = await chat_agent.answer_question(REPORT_ID, "What was revenue?")

    assert turn.turns_remaining == CHAT_RATE_LIMIT_MAX_TURNS - 3


# --- Suggested questions --------------------------------------------------


async def test_suggests_only_questions_the_report_can_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revenue = make_fact(metric="income.revenue", label="Revenue")
    fcf = make_fact(
        metric="cashflow.free_cash_flow",
        label="Free cash flow",
        is_calculated=True,
        formula="net cash from operating activities − capital expenditure",
    )

    async def _load_facts(report_id: str) -> list[Any]:
        return [revenue, fcf]

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _load_facts)

    suggestions = await chat_agent.suggest_questions(REPORT_ID)

    assert "What was revenue?" in suggestions
    assert "What was free cash flow?" in suggestions
    # Net income was never in the store, so it must not be suggested.
    assert "What was net income?" not in suggestions
    # Free cash flow being present is what unlocks the valuation suggestion.
    assert "What is this company worth?" in suggestions


async def test_suggests_nothing_when_facts_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raise(report_id: str) -> list[Any]:
        message = "boom"
        raise chat_agent.m06_factstore.FactStoreError(message)

    monkeypatch.setattr(chat_agent.m06_factstore, "load_facts", _raise)

    suggestions = await chat_agent.suggest_questions(REPORT_ID)

    assert suggestions == []
