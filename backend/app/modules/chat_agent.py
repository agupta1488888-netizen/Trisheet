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


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


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
    """
    q_tokens = _tokens(question)
    if not q_tokens:
        return []

    scored: list[tuple[int, dt.date, Fact]] = []
    for fact in facts:
        overlap = len(q_tokens & _fact_tokens(fact))
        if overlap >= CHAT_FACT_MATCH_MIN_OVERLAP:
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
