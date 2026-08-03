"""chat_agent — answers a question about a completed report.

Responsibility
    A reader looking at a finished report asks a question. This module answers
    it from facts that already carry provenance, and nothing else.

The cascade
    Two tiers, checked in order, stopping at the first that has an answer:

    1. Facts already stored for this report. Matching is plain Python token
       overlap between the question and each fact's metric/label — a search
       problem, not something an LLM is asked to decide, per the same
       reasoning CLAUDE.md gives for tier enforcement generally: this is code,
       not prompting.
    2. Metrics derivable from the same reported facts under every metric group
       `m07_analysis` knows, not just the ones the filer's sector template
       would emit into the report. A bank's report never shows a current
       ratio; the reported facts can still support computing one if a reader
       asks for it directly.

    A question that asks for a valuation estimate (worth, DCF, intrinsic
    value) is answered differently: `m07_analysis.project_dcf` runs over the
    same reported and derived facts, and the reply mixes certified claims
    (the real free cash flow, net debt and share count it used) with
    assumption claims (the discount and growth rates, and the resulting
    estimate) — never presenting the estimate as a filed figure. See
    `_answer_valuation_question`.

    Company websites and general web search are separate, deliberately
    deferred phases. No tool here reaches either, and `ChatClaim` itself
    cannot represent a tier outside 1-2 (see `models.ChatClaim`).

Why this needs no tool-calling loop
    Choosing which fact answers a question is a matching problem over
    structured data the code already has in hand — there is nothing for a
    model to decide by calling a tool that Python cannot decide faster and
    more cheaply by matching directly. The model's only job, once candidate
    facts are found, is exactly m10's job: write a sentence about supplied
    figures and cite the ids it used. `_parse_claims` enforces that the same
    way `m10_writer._parse_sentences` does — an id that was not supplied is
    dropped, not trusted.

Degradation
    A fact-store read failure, a model failure, or a persistence failure each
    produce a `ChatTurn` stating what happened, never a raised exception and
    never a fabricated answer. Rule 6 ("never render a blank screen or a
    stack trace") applies to one chat turn exactly as it applies to a report.

Public interface
    answer_question(report_id, message) -> ChatTurn
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from app.config import (
    CHAT_FACT_MATCH_MIN_OVERLAP,
    CHAT_TOOL_RESULT_FACT_LIMIT,
)
from app.models import ChatClaim, ChatRole, ChatTurn, Fact
from app.modules import m06_factstore, m07_analysis
from app.modules.m10_writer import render_fact_table
from app.services import db, llm, runlog

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)

CHAT_MESSAGES_TABLE = "chat_messages"

NOT_FOUND_TEXT = "Not found in this report's filed data."
MODEL_UNAVAILABLE_TEXT = (
    "The assistant could not answer just now. Try again in a moment."
)
DATA_UNAVAILABLE_TEXT = (
    "This report's data could not be read right now. Try again in a moment."
)

_WORD_RE = re.compile(r"[a-z0-9]+")

#: The rules. Adapted from m10_writer.SYSTEM_PROMPT for one answer instead of
#: a section, with the same enforcement mirrored in code by `_parse_claims`.
SYSTEM_PROMPT = """\
You answer one question about a company report, using only facts that have \
already been extracted from a filing or computed in Python.

Rules:

1. Use only the values in the supplied fact table. Never calculate, never \
recall a figure from memory, never estimate, never interpolate.
2. Write a figure exactly as its display value gives it.
3. Every claim you make must name the single fact id it rests on, in that \
claim's fact_id field. Never cite an id that is not in the table.
4. If the supplied facts do not actually answer the question — even if they \
are on a related topic — set not_found to true and return an empty claims \
array. Do not answer a different question than the one asked.
5. Sentence case. Plain sentences, no markdown, no bullet points, no \
headings. Do not use the word "AI". Do not address the reader.
6. Be brief. Answer the question, nothing more.
"""

_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "One complete sentence answering part "
                        "of the question.",
                    },
                    "fact_id": {
                        "type": "string",
                        "description": "Id of the one supplied fact this "
                        "claim rests on.",
                    },
                },
                "required": ["text", "fact_id"],
                "additionalProperties": False,
            },
        },
        "not_found": {
            "type": "boolean",
            "description": "True when the supplied facts do not answer the "
            "question asked.",
        },
    },
    "required": ["claims", "not_found"],
    "additionalProperties": False,
}


#: Words that carry no metric identity of their own. Stripped from the
#: question before matching, so "What was revenue?" is judged on "revenue"
#: alone rather than being penalised for not also saying "income" or "what".
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "at", "be", "by", "company", "did", "do",
        "does", "for", "from", "has", "have", "how", "in", "is", "it", "its",
        "of", "on", "report", "that", "the", "this", "to", "was", "were",
        "what", "which", "with", "you", "your",
    }
)


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _question_tokens(question: str) -> set[str]:
    return _tokens(question) - _STOPWORDS


def _fact_tokens(fact: Fact) -> set[str]:
    """Words a question might use to refer to this fact.

    The metric's own path segments (dotted, underscored) plus its label, so a
    question about "free cash flow" matches `derived.free_cash_flow` labelled
    "Free cash flow" by either wording.
    """
    tail_words = fact.metric.replace(".", " ").replace("_", " ")
    return _tokens(tail_words) | _tokens(fact.label)


def _match_facts(question: str, facts: Sequence[Fact]) -> list[Fact]:
    """Facts whose metric or label overlaps the question enough to matter.

    Plain token overlap, not similarity scoring or embeddings — deliberately
    the simplest thing that works, because what matters is that this decision
    is legible and made in code, not that it is clever.

    The overlap required scales down to the question's own length: a
    single-word question like "revenue" can only ever overlap by 1 no matter
    how it is asked, so demanding `CHAT_FACT_MATCH_MIN_OVERLAP` regardless
    would make every single-metric question unmatchable by construction. A
    longer question still needs the fuller overlap, which is what keeps a
    stray shared word from misfiring a match.
    """
    q_tokens = _question_tokens(question)
    if not q_tokens:
        return []
    required = min(CHAT_FACT_MATCH_MIN_OVERLAP, len(q_tokens))

    scored: list[tuple[int, dt.date, Fact]] = []
    for fact in facts:
        overlap = len(q_tokens & _fact_tokens(fact))
        if overlap >= required:
            scored.append((overlap, fact.period_end, fact))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [fact for _, _, fact in scored[:CHAT_TOOL_RESULT_FACT_LIMIT]]


def _parse_claims(
    answer: dict[str, Any], allowed: dict[str, Fact]
) -> list[ChatClaim]:
    """Turns the model's answer into claims, dropping unsupplied citations.

    Mirrors `m10_writer._parse_sentences`: a cited id that was not in the
    table offered to the model is removed rather than trusted.
    """
    if answer.get("not_found") is True:
        return []

    raw = answer.get("claims")
    if not isinstance(raw, list):
        return []

    claims: list[ChatClaim] = []
    unknown: list[str] = []

    for item in raw:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue

        fact_id = item.get("fact_id")
        if not isinstance(fact_id, str) or fact_id not in allowed:
            if isinstance(fact_id, str):
                unknown.append(fact_id)
            continue

        fact = allowed[fact_id]
        claims.append(
            ChatClaim(
                text=text.strip(),
                tier=fact.tier,
                fact_id=fact.fact_id,
                source_url=fact.source_url,
                source_type=fact.source_type,
                accession_no=fact.accession_no,
                filed_date=fact.filed_date,
                not_found=False,
            )
        )

    if unknown:
        logger.warning(
            "Chat dropped citations to facts that were not supplied",
            extra={"unknown_fact_ids": unknown},
        )

    return claims


def _build_user_prompt(
    ticker: str, message: str, candidates: Sequence[Fact]
) -> str:
    return (
        f"Ticker: {ticker}\n\n"
        f"Question: {message}\n\n"
        f"Facts available:\n{render_fact_table(candidates)}\n"
    )


def _new_turn(
    role: ChatRole, *, claims: Sequence[ChatClaim], content: str, not_found: bool
) -> ChatTurn:
    return ChatTurn(
        id=str(uuid.uuid4()),
        role=role,
        claims=tuple(claims),
        content=content,
        not_found=not_found,
        created_at=dt.datetime.now(dt.UTC),
    )


def _not_found_turn(text: str = NOT_FOUND_TEXT) -> ChatTurn:
    claim = ChatClaim(text=text, not_found=True)
    return _new_turn(
        ChatRole.ASSISTANT, claims=(claim,), content=text, not_found=True
    )


def _wider_candidates(all_facts: Sequence[Fact], message: str) -> list[Fact]:
    """Tier 2: matches over every metric group, not just this filer's sector
    template, computed from the same reported (non-calculated) facts."""
    raw_facts = [fact for fact in all_facts if not fact.is_calculated]
    if not raw_facts:
        return []
    wider = m07_analysis.analyse(
        raw_facts, sic_code=None, groups=m07_analysis.ALL_METRIC_GROUPS
    ).facts
    return _match_facts(message, wider)


# --- Valuation (DCF) ------------------------------------------------------
# "Value", deliberately excluded from the trigger words below, is too generic
# — it appears in ordinary fact labels ("value of total assets") that are not
# valuation questions at all. The words kept are ones a reader uses only when
# actually asking for an intrinsic-value estimate.
_VALUATION_TRIGGER_WORDS = frozenset(
    {"dcf", "discounted", "intrinsic", "worth", "valuation", "valuations", "valued"}
)


def _is_valuation_question(question: str) -> bool:
    return bool(_tokens(question) & _VALUATION_TRIGGER_WORDS)


def _money(value: float, unit: str | None) -> str:
    suffix = f" {unit}" if unit else ""
    return f"{value:,.0f}{suffix}"


def _dcf_input_claims(
    result: m07_analysis.DcfResult, facts_by_id: dict[str, Fact]
) -> list[ChatClaim]:
    """Certified claims for the real facts the calculation actually used."""
    claims: list[ChatClaim] = []
    for fact_id, label in (
        (result.base_fcf_fact_id, "Free cash flow"),
        (result.net_debt_fact_id, "Net debt"),
        (result.shares_fact_id, "Diluted shares outstanding"),
    ):
        if fact_id is None:
            continue
        fact = facts_by_id.get(fact_id)
        if fact is None:
            continue
        period = fact.fiscal_year if fact.fiscal_year is not None else fact.period_end
        claims.append(
            ChatClaim(
                text=f"{label} (FY{period}): {fact.display_value}.",
                tier=fact.tier,
                fact_id=fact.fact_id,
                source_url=fact.source_url,
                source_type=fact.source_type,
                accession_no=fact.accession_no,
                filed_date=fact.filed_date,
            )
        )
    return claims


def _dcf_assumption_claims(
    result: m07_analysis.DcfResult, base_fact: Fact | None
) -> list[ChatClaim]:
    """Assumption claims: the rates used, and the resulting estimate.

    Both are `is_assumption` — the estimate is grouped with its assumptions
    rather than presented as a plain figure, because its correctness depends
    entirely on them: a different discount rate changes the number even
    though every real input behind it stays the same.
    """
    unit = base_fact.unit if base_fact is not None else None

    assumptions_text = (
        f"This estimate assumes a {_pct(result.discount_rate.value)} discount "
        f"rate, {_pct(result.fcf_growth_rate.value)} free cash flow growth "
        f"for {result.projection_years} years, and a "
        f"{_pct(result.terminal_growth_rate.value)} terminal growth rate."
    )
    assumptions_note = " ".join(
        (
            result.discount_rate.note,
            result.fcf_growth_rate.note,
            result.terminal_growth_rate.note,
            "None of these are filed figures.",
        )
    )

    result_parts = [
        f"Estimated enterprise value: {_money(result.enterprise_value or 0.0, unit)}."
    ]
    if result.equity_value is not None:
        result_parts.append(
            f"Estimated equity value: {_money(result.equity_value, unit)}."
        )
    if result.value_per_share is not None:
        result_parts.append(
            f"Estimated value per share: {_money(result.value_per_share, unit)}."
        )

    return [
        ChatClaim(
            text=assumptions_text,
            is_assumption=True,
            assumption_note=assumptions_note,
        ),
        ChatClaim(
            text=" ".join(result_parts),
            is_assumption=True,
            assumption_note=(
                "A discounted-cash-flow estimate, not a reported or audited "
                "figure — it depends entirely on the assumptions above, not "
                "on filed data alone."
            ),
        ),
    ]


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


async def _answer_valuation_question(
    report_id: str, all_facts: Sequence[Fact], user_turn: ChatTurn
) -> ChatTurn:
    """Answers a valuation question with a DCF scenario over this filer's
    real facts, mixing certified inputs with clearly labelled assumptions."""
    raw_facts = [fact for fact in all_facts if not fact.is_calculated]
    # `all_facts` may already carry the metrics `project_dcf` needs, computed
    # once when the report itself was generated — the wider recompute below
    # only fills in what the filer's own sector template left out, it does
    # not replace what is already there.
    wider = (
        m07_analysis.analyse(
            raw_facts, sic_code=None, groups=m07_analysis.ALL_METRIC_GROUPS
        ).facts
        if raw_facts
        else ()
    )
    combined = [*all_facts, *wider]
    result = m07_analysis.project_dcf(combined)

    if result.unavailable_reason is not None:
        assistant_turn = _not_found_turn(result.unavailable_reason)
        await _persist(report_id, user_turn, assistant_turn)
        return assistant_turn

    facts_by_id = {fact.fact_id: fact for fact in combined}
    base_fact = (
        facts_by_id.get(result.base_fcf_fact_id)
        if result.base_fcf_fact_id is not None
        else None
    )
    claims = _dcf_input_claims(result, facts_by_id) + _dcf_assumption_claims(
        result, base_fact
    )

    assistant_turn = _new_turn(
        ChatRole.ASSISTANT,
        claims=claims,
        content=" ".join(claim.text for claim in claims),
        not_found=False,
    )
    await _persist(
        report_id,
        user_turn,
        assistant_turn,
        tool_calls=[{"tool": "project_dcf"}],
        highest_tier_used=None,
    )
    return assistant_turn


async def answer_question(report_id: str, message: str) -> ChatTurn:
    """Answers one question about a completed report.

    Never raises: every failure mode returns a `ChatTurn` stating what
    happened, so the endpoint has nothing to catch.
    """
    report = runlog.get_report(report_id)
    ticker = report.ticker if report is not None else report_id

    user_turn = _new_turn(
        ChatRole.USER, claims=(), content=message, not_found=False
    )

    try:
        all_facts = await m06_factstore.load_facts(report_id)
    except m06_factstore.FactStoreError as cause:
        logger.warning(
            "Chat could not read facts",
            extra={"report_id": report_id},
            exc_info=cause,
        )
        assistant_turn = _not_found_turn(DATA_UNAVAILABLE_TEXT)
        await _persist(report_id, user_turn, assistant_turn)
        return assistant_turn

    if _is_valuation_question(message):
        return await _answer_valuation_question(report_id, all_facts, user_turn)

    highest_tier: int | None = None
    candidates = _match_facts(message, all_facts)
    if candidates:
        highest_tier = 1
    else:
        candidates = _wider_candidates(all_facts, message)
        if candidates:
            highest_tier = 2

    if not candidates:
        assistant_turn = _not_found_turn()
        await _persist(report_id, user_turn, assistant_turn)
        return assistant_turn

    allowed = {fact.fact_id: fact for fact in candidates}

    try:
        answer = await llm.complete_json(
            SYSTEM_PROMPT,
            _build_user_prompt(ticker, message, candidates),
            _ANSWER_SCHEMA,
            purpose="chat:answer",
        )
    except llm.LlmError as cause:
        logger.warning(
            "Chat model call failed",
            extra={"report_id": report_id},
            exc_info=cause,
        )
        assistant_turn = _not_found_turn(MODEL_UNAVAILABLE_TEXT)
        await _persist(report_id, user_turn, assistant_turn)
        return assistant_turn

    claims = _parse_claims(answer, allowed)
    if not claims:
        assistant_turn = _not_found_turn()
        await _persist(report_id, user_turn, assistant_turn)
        return assistant_turn

    assistant_turn = _new_turn(
        ChatRole.ASSISTANT,
        claims=claims,
        content=" ".join(claim.text for claim in claims),
        not_found=False,
    )
    await _persist(
        report_id,
        user_turn,
        assistant_turn,
        tool_calls=[
            {"tier": highest_tier, "candidates_considered": len(candidates)}
        ],
        highest_tier_used=highest_tier,
    )
    return assistant_turn


# --- Persistence --------------------------------------------------------


def _claim_row(claim: ChatClaim) -> dict[str, Any]:
    return {
        "text": claim.text,
        "tier": int(claim.tier) if claim.tier is not None else None,
        "fact_id": claim.fact_id,
        "source_url": str(claim.source_url) if claim.source_url else None,
        "source_type": (
            str(claim.source_type)
            if claim.source_type is not None
            else None
        ),
        "accession_no": claim.accession_no,
        "filed_date": (
            claim.filed_date.isoformat() if claim.filed_date else None
        ),
        "not_found": claim.not_found,
    }


def _turn_row(
    report_id: str,
    turn_index: int,
    turn: ChatTurn,
    *,
    tool_calls: list[dict[str, Any]] | None,
    highest_tier_used: int | None,
) -> dict[str, Any]:
    return {
        "report_id": report_id,
        "turn_index": turn_index,
        "role": str(turn.role),
        "content": turn.content,
        "claims": (
            [_claim_row(claim) for claim in turn.claims]
            if turn.role is ChatRole.ASSISTANT
            else None
        ),
        "tool_calls": tool_calls if tool_calls is not None else [],
        "highest_tier_used": highest_tier_used,
        "not_found": turn.not_found,
        "created_at": turn.created_at.isoformat(),
    }


def _next_turn_index(client: Client, report_id: str) -> int:
    response = (
        client.table(CHAT_MESSAGES_TABLE)
        .select("turn_index")
        .eq("report_id", report_id)
        .order("turn_index", desc=True)
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if rows and isinstance(rows[0], dict) and rows[0].get("turn_index") is not None:
        return int(rows[0]["turn_index"]) + 1
    return 0


async def _persist(
    report_id: str,
    user_turn: ChatTurn,
    assistant_turn: ChatTurn,
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    highest_tier_used: int | None = None,
) -> None:
    """Writes both turns of an exchange, or logs and moves on.

    Persistence is a sink here exactly as it is in m06: a chat turn that could
    not be written down is still a chat turn the reader received, so a write
    failure degrades rather than turns into a 500 for a question that was, in
    fact, answered.
    """
    if not db.is_configured():
        return

    try:
        client = db.get_client()
        base_index = _next_turn_index(client, report_id)
        rows = [
            _turn_row(
                report_id,
                base_index,
                user_turn,
                tool_calls=None,
                highest_tier_used=None,
            ),
            _turn_row(
                report_id,
                base_index + 1,
                assistant_turn,
                tool_calls=tool_calls,
                highest_tier_used=highest_tier_used,
            ),
        ]
        client.table(CHAT_MESSAGES_TABLE).insert(rows).execute()
    except db.DatabaseError as cause:
        logger.warning(
            "Chat turn could not be persisted",
            extra={"report_id": report_id},
            exc_info=cause,
        )
    except Exception as cause:  # noqa: BLE001 — surfaced as a typed failure
        logger.warning(
            "Chat turn could not be persisted",
            extra={"report_id": report_id},
            exc_info=cause,
        )


__all__ = ["answer_question"]
