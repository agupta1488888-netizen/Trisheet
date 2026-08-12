"""Tests for m10 — prose generation and its self-check gate.

m11 is a whole-report gate: one fabricated figure fails verification outright,
which is what "3 of the last 4 live reports failed" traced back to. The
self-check here exists to catch that inside m10, on the section's own facts,
before it ever reaches m11 — so these tests are about the gate holding, not
about the gate running.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config import (
    LLM_WRITER_SELF_CHECK_MAX_RETRIES,
    WRITER_SECTIONS,
    WriterSection,
)
from app.modules import m10_writer
from app.services import llm
from tests.conftest import make_company, make_fact

SECTION = WriterSection(
    section_id="test",
    title="Test section",
    metric_sections=(3,),
    brief="Write about the figures below.",
)


def _answer(*sentences: tuple[str, tuple[str, ...]]) -> dict[str, Any]:
    return {
        "sentences": [
            {"text": text, "fact_ids": list(fact_ids)}
            for text, fact_ids in sentences
        ]
    }


def _stub_llm(
    monkeypatch: pytest.MonkeyPatch, *answers: dict[str, Any]
) -> list[str]:
    """Stubs `complete_json` to return each answer in order.

    Returns the list of prompts sent, in call order, so a test can assert on
    how many completions a section actually cost.
    """
    prompts: list[str] = []
    remaining = list(answers)

    async def _complete(
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        purpose: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        prompts.append(user)
        if not remaining:
            message = "complete_json called more times than answers were stubbed."
            raise AssertionError(message)
        return remaining.pop(0)

    monkeypatch.setattr(llm, "complete_json", _complete)
    return prompts


# --- _facts_for ---------------------------------------------------------------


def test_facts_for_excludes_facts_outside_the_sections_own_prefixes() -> None:
    revenue = make_fact(metric="income.revenue")
    market_cap = make_fact(
        metric="market.capitalization",
        label="Market capitalization",
        period_end=revenue.period_end,
    )

    available = m10_writer._facts_for(SECTION, [revenue, market_cap])

    assert available == [revenue]


# --- Self-check: catching what m11 would also reject --------------------------


async def test_self_check_catches_a_figure_no_fact_supports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revenue = make_fact(metric="income.revenue")
    bad_answer = _answer(
        ("Revenue was 391,035,000,000.", (revenue.fact_id,)),
        ("Gross margin by product was 46.2%.", ()),
    )
    prompts = _stub_llm(
        monkeypatch, *([bad_answer] * (LLM_WRITER_SELF_CHECK_MAX_RETRIES + 1))
    )

    section, warnings = await m10_writer._write_section(
        make_company(), SECTION, [revenue]
    )

    assert [s.text for s in section.sentences] == ["Revenue was 391,035,000,000."]
    assert len(warnings) == 1
    assert "1 sentence(s) stripped" in warnings[0]
    assert len(prompts) == LLM_WRITER_SELF_CHECK_MAX_RETRIES + 1


async def test_self_check_catches_a_cited_but_uncorroborated_figure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revenue = make_fact(metric="income.revenue")
    net_income = make_fact(
        metric="income.net_income",
        label="Net income",
        value=93_736_000_000.0,
        display_value="93,736,000,000",
        period_end=revenue.period_end,
    )
    # States net income's figure but cites revenue's fact id — a real fact
    # supports the number, just not the one this sentence names.
    bad_answer = _answer(
        (
            f"Net income was {net_income.display_value}.",
            (revenue.fact_id,),
        ),
    )
    _stub_llm(monkeypatch, *([bad_answer] * (LLM_WRITER_SELF_CHECK_MAX_RETRIES + 1)))

    section, warnings = await m10_writer._write_section(
        make_company(), SECTION, [revenue, net_income]
    )

    assert section.sentences == ()
    assert section.unavailable_reason == (
        "This section came back empty and was not written."
    )
    assert len(warnings) == 1


async def test_retry_recovers_when_the_second_generation_is_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revenue = make_fact(metric="income.revenue")
    bad_answer = _answer(("Gross margin by product was 46.2%.", ()))
    clean_answer = _answer(("Revenue was 391,035,000,000.", (revenue.fact_id,)))
    prompts = _stub_llm(monkeypatch, bad_answer, clean_answer)

    section, warnings = await m10_writer._write_section(
        make_company(), SECTION, [revenue]
    )

    assert [s.text for s in section.sentences] == ["Revenue was 391,035,000,000."]
    assert warnings == ()
    assert len(prompts) == 2
    # The retry names what was rejected, so the model has something to act on.
    assert "46.2%" in prompts[1]
    assert "could not be verified" in prompts[1]


async def test_a_clean_first_answer_needs_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revenue = make_fact(metric="income.revenue")
    clean_answer = _answer(("Revenue was 391,035,000,000.", (revenue.fact_id,)))
    prompts = _stub_llm(monkeypatch, clean_answer)

    section, warnings = await m10_writer._write_section(
        make_company(), SECTION, [revenue]
    )

    assert [s.text for s in section.sentences] == ["Revenue was 391,035,000,000."]
    assert warnings == ()
    assert len(prompts) == 1


async def test_terminal_strip_keeps_the_rest_of_the_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revenue = make_fact(metric="income.revenue")
    bad_answer = _answer(
        ("Revenue was 391,035,000,000.", (revenue.fact_id,)),
        ("Operating margin by segment was 61.4%.", ()),
    )
    _stub_llm(monkeypatch, *([bad_answer] * (LLM_WRITER_SELF_CHECK_MAX_RETRIES + 1)))

    section, warnings = await m10_writer._write_section(
        make_company(), SECTION, [revenue]
    )

    assert [s.text for s in section.sentences] == ["Revenue was 391,035,000,000."]
    assert len(warnings) == 1


async def test_a_year_reference_is_not_treated_as_a_fabricated_figure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revenue = make_fact(metric="income.revenue", fiscal_year=2024)
    text = f"Fiscal {revenue.fiscal_year} revenue was {revenue.display_value}."
    clean_answer = _answer((text, (revenue.fact_id,)))
    prompts = _stub_llm(monkeypatch, clean_answer)

    section, warnings = await m10_writer._write_section(
        make_company(), SECTION, [revenue]
    )

    assert len(section.sentences) == 1
    assert warnings == ()
    assert len(prompts) == 1


# --- Snapshot: section 1 must see section 3's facts ---------------------------
#
# Snapshot's own brief asks for revenue scale, cash conversion and balance
# sheet shape, but its metric_sections once left out section 3 (income,
# balance, cashflow) entirely — so the model correctly, and unhelpfully, said
# none of that was disclosed. Regression coverage for the fix, not the
# self-check.


def _snapshot_section() -> WriterSection:
    return next(s for s in WRITER_SECTIONS if s.section_id == "snapshot")


def test_snapshot_metric_sections_include_financial_highlights() -> None:
    assert 3 in _snapshot_section().metric_sections


def test_snapshot_facts_for_include_income_facts() -> None:
    revenue = make_fact(metric="income.revenue")

    available = m10_writer._facts_for(_snapshot_section(), [revenue])

    assert revenue in available


async def test_snapshot_writes_from_financial_facts_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The exact reported symptom: a fact set with no profile./market./
    # valuation. facts at all — only what section 3 owns — used to leave
    # Snapshot's fact table empty and the section omitted.
    revenue = make_fact(metric="income.revenue")
    clean_answer = _answer(
        (f"Revenue was {revenue.display_value}.", (revenue.fact_id,)),
    )
    _stub_llm(monkeypatch, clean_answer)

    section, warnings = await m10_writer._write_section(
        make_company(), _snapshot_section(), [revenue]
    )

    assert section.unavailable_reason is None
    assert [s.text for s in section.sentences] == [
        f"Revenue was {revenue.display_value}."
    ]
    assert warnings == ()
