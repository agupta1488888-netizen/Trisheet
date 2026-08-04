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
    estimate) — never presenting the estimate as a filed figure. A
    sensitivity question ("how sensitive is this to the discount rate?") or a
    scenario question ("what's the bull and bear case?") runs the same DCF
    several times instead of once — `project_dcf_sensitivity` /
    `project_dcf_scenarios` — and the reply becomes one assumption claim per
    discount-rate point or named case, still built from the one set of
    certified inputs. None of this ever reaches the model: every valuation
    reply, plain or multi-point, is composed in Python from `DcfResult` /
    `DcfSensitivityResult` / `DcfScenarioResult` alone. See
    `_answer_valuation_question`.

    When tiers 1-2 come up empty, links supplied with the report request
    are tried first among the fallbacks. `m13_sources` already read and
    quoted those pages when the report was generated, so answering from
    them costs one model call rather than a fetch plus a call, and they are
    the sources this reader chose for this report. A miss falls through to
    the fallbacks below rather than ending the turn. Nothing on this path
    can produce a figure for the statements: a `SourceNote` is not a `Fact`
    and never becomes one. See `_answer_from_source_notes`.

    When that comes up empty too and the reader supplied a company URL
    (`ChatRequest.pasted_url`), `services.webfetch` reads that one page —
    guarded against being pointed somewhere unsafe — and the model answers
    from its text alone, cited as tier 2 / `SourceType.COMPANY_SITE`: real,
    but self-reported rather than filed. This never runs unprompted; a
    pasted URL is read only as the fallback it was supplied for, never
    consulted when the report's own facts already answered the question. See
    `_answer_from_company_site`.

    General web search is the last resort — tier 4, tried only when nothing
    above answered, and only when `CHAT_WEB_SEARCH_ENABLED` is on (off by
    default; a deployment turns it on only once the rate limit above is
    known to be enforced, since this is the one tier that spends money on a
    source outside this system's own data). Every claim it produces names
    the exact page it came from and is rendered least like a filing. See
    `_answer_from_web_search`.

    Absolute last resort, past even web search: an ungrounded general-purpose
    reply, tried only when everything above (including web search) came up
    empty, and only when `CHAT_GENERAL_CHAT_ENABLED` is on — off by default,
    same reasoning as web search, one step further, since this tier isn't
    grounded in any of this system's own data at all. See
    `_answer_with_general_chat`.

A comparison question ("how does this compare to competitors?") widens the
    tier 1-2 candidate pool to include every stored peer fact (metric prefix
    "peer.") rather than fetching a peer's filings fresh — see
    `_is_peer_question`/`_with_peer_facts`.

A greeting, "thanks", or a "what can you do" question is answered from a
    fixed reply, never run through the cascade above — see
    `_small_talk_reply`. This is deliberately narrow: anything else still
    goes through the whole cascade, honest "not found" included.

A question asking this assistant to make the call for the reader ("suggest",
    "recommend", "should I buy") is answered from a fixed redirect explaining
    what it does instead — see `_is_advice_question`/`_ADVICE_REPLY`. Checked
    only after the valuation/sensitivity/scenario gate, so a compound
    question like "suggest me a valuation" still gets the real valuation
    (which already ends in its own not-a-recommendation line) rather than
    being intercepted here.

The immediately preceding exchange is used two ways for a follow-up like
    "and last year's?", which names no metric on its own: re-resolving the
    topic from the previous question alone when the new message matches
    nothing by itself, and shown to the model as context for what the
    follow-up refers to — never as something a figure may be answered from.
    See `_recent_exchange`.

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
    answer_question(report_id, message, pasted_url=None) -> ChatTurn
    is_rate_limited(report_id) -> bool
    suggest_questions(report_id) -> list[str]
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.config import (
    CHAT_FACT_MATCH_MIN_OVERLAP,
    CHAT_GENERAL_CHAT_ENABLED,
    CHAT_RATE_LIMIT_MAX_TURNS,
    CHAT_RATE_LIMIT_WINDOW_MINUTES,
    CHAT_TOOL_RESULT_FACT_LIMIT,
    CHAT_WEB_SEARCH_ENABLED,
    CHAT_WEBFETCH_MAX_PAGE_CHARS,
)
from app.models import (
    ChatClaim,
    ChatRole,
    ChatTurn,
    Fact,
    ReportDocument,
    SourceNote,
    SourceTier,
    SourceType,
)
from app.modules import m06_factstore, m07_analysis
from app.modules.m10_writer import render_fact_table
from app.services import db, llm, runlog, webfetch

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
7. A previous question and answer may be supplied for context. Use them \
only to understand what a follow-up like "and last year's?" refers to — \
never as a source for a figure. Every figure you state must still cite a \
fact id from this turn's own table, never the previous answer's wording.
"""

#: Used instead of `SYSTEM_PROMPT` for a question asking for an evaluative
#: read (a trend, a peer comparison, "any red flags") rather than a specific
#: number — see `_is_insight_question`. Same seven rules, plus one bounded
#: addition permitting a single interpretive closing sentence. The boundary
#: this draws — describing what filed data shows, never what a reader should
#: do about it — mirrors `_NO_RECOMMENDATION_TEXT` in the valuation path.
INSIGHT_SYSTEM_PROMPT = """\
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
7. A previous question and answer may be supplied for context. Use them \
only to understand what a follow-up like "and last year's?" refers to — \
never as a source for a figure. Every figure you state must still cite a \
fact id from this turn's own table, never the previous answer's wording.
8. You may add ONE additional closing sentence stating plainly what the \
supplied facts together suggest — a trend, a gap versus a peer figure in \
the table, a concentration — but only when the facts actually support a \
clear reading. If they do not, do not add one; answer with the figures \
alone. This sentence must still cite a single fact id like every other \
claim, and must never use investment-recommendation language: no "buy", \
"sell", "hold", "should invest", "undervalued", "overvalued", "price \
target", "good investment", "bad investment", "outperform"/"underperform" \
as a rating, or any similar verdict. State what the data shows, never what \
the reader should do about it.
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


#: Phrases that make a claim read as investment advice rather than a
#: description of filed data. Checked as substrings against the model's own
#: claim text (lowercased) — `INSIGHT_SYSTEM_PROMPT` is the one place this
#: system lets the model write a sentence beyond restating a supplied
#: figure, so this is the one place a banned-phrase filter earns its keep.
#: Runs unconditionally, not just in insight mode: prompt discipline alone
#: is not proof, the same reasoning `_parse_claims` already applies to a
#: cited fact id that was never actually supplied.
_RECOMMENDATION_PHRASES = (
    "should buy", "should sell", "should invest", "buy this stock",
    "sell this stock", "strong buy", "strong sell", "buy rating",
    "sell rating", "hold rating", "price target", "undervalued",
    "overvalued", "good investment", "bad investment", "worth buying",
    "worth investing", "outperform", "underperform",
)


def _reads_as_recommendation(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _RECOMMENDATION_PHRASES)


def _parse_claims(
    answer: dict[str, Any], allowed: dict[str, Fact]
) -> list[ChatClaim]:
    """Turns the model's answer into claims, dropping unsupplied citations.

    Mirrors `m10_writer._parse_sentences`: a cited id that was not in the
    table offered to the model is removed rather than trusted. A claim that
    reads as investment advice (`_reads_as_recommendation`) is dropped the
    same way, regardless of whether it also cites a real fact id.
    """
    if answer.get("not_found") is True:
        return []

    raw = answer.get("claims")
    if not isinstance(raw, list):
        return []

    claims: list[ChatClaim] = []
    unknown: list[str] = []
    recommendations = 0

    for item in raw:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        if _reads_as_recommendation(text):
            recommendations += 1
            continue

        fact_id = item.get("fact_id")
        if not isinstance(fact_id, str) or fact_id not in allowed:
            if isinstance(fact_id, str):
                unknown.append(fact_id)
            continue

        fact = allowed[fact_id]
        try:
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
        except ValidationError as cause:
            # A fact whose tier a chat claim cannot represent (there is
            # none today, but the invariant is enforced in code, not by
            # this call site remembering to check first) is dropped rather
            # than trusted — the same "unsupplied id" discipline just
            # above, for a shape mismatch instead of a missing citation.
            logger.warning(
                "Chat dropped a claim whose fact could not be represented",
                extra={"fact_id": fact_id},
                exc_info=cause,
            )

    if unknown:
        logger.warning(
            "Chat dropped citations to facts that were not supplied",
            extra={"unknown_fact_ids": unknown},
        )
    if recommendations:
        logger.warning(
            "Chat dropped a claim that read as investment advice",
            extra={"count": recommendations},
        )

    return claims


def _build_user_prompt(
    ticker: str,
    message: str,
    candidates: Sequence[Fact],
    *,
    prior: tuple[str, str] | None = None,
) -> str:
    history = (
        f"Previous question: {prior[0]}\nPrevious answer: {prior[1]}\n\n"
        if prior is not None
        else ""
    )
    return (
        f"Ticker: {ticker}\n\n"
        f"{history}"
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


# --- Small talk -----------------------------------------------------------
# A greeting or a "what can you do" is not a data question, and running it
# through the fact cascade would only ever end in a cold "not found". This
# is deliberately narrow — three fixed situations, three fixed replies — not
# a general chatbot: anything that isn't one of these still goes through the
# whole cascade above, honest "not found" included.

_GREETINGS = frozenset({"hi", "hello", "hey", "hiya", "yo", "greetings"})
_THANKS = frozenset({"thanks", "thank you", "thx", "ty", "cheers"})
#: Substring checks, not exact matches — "what can you help with" and "what
#: can you do" both contain "what can you".
_HELP_PHRASES = (
    "what can you do",
    "what can you help",
    "what can i ask",
    "how does this work",
    "what do you do",
)

_HELP_REPLY = (
    "Ask about a figure in this report — revenue, margins, cash flow and "
    "similar are cited to the exact filing they came from. Ask a valuation "
    "question (\"what is this worth?\") for a discounted-cash-flow estimate, "
    "clearly marked wherever it's an assumption rather than a filed figure. "
    "If the report doesn't have what you need, you can add a company URL "
    "for that page to be read instead."
)


def _small_talk_reply(message: str) -> str | None:
    """A canned reply for a greeting, thanks, or a "what can you do"
    question — or None, for everything else, which continues to the fact
    cascade exactly as before."""
    normalised = message.strip().lower().rstrip("!.? ")
    if normalised in _GREETINGS:
        return (
            "Hello. Ask a question about this report's figures, or ask "
            "what it can value."
        )
    if normalised in _THANKS:
        return "You're welcome."
    if any(phrase in normalised for phrase in _HELP_PHRASES):
        return _HELP_REPLY
    return None


# --- Advice redirect --------------------------------------------------------
# A question asking this assistant to make the call for the reader —
# "suggest", "recommend", "should I buy" — rather than asking about
# something this system can actually compute. Checked from `_route_question`
# only *after* the valuation/sensitivity/scenario gate: a compound question
# like "suggest me a valuation" still gets the real valuation (which already
# ends in its own not-a-recommendation line, see `_no_recommendation_claim`)
# rather than being intercepted here. This exists so a pure "what should I
# do" question gets an honest, specific redirect instead of a bare "not
# found" — the same reasoning `_HELP_REPLY` already applies to "what can you
# do" — never a fabricated suggestion.

_ADVICE_TRIGGER_WORDS = frozenset(
    {"suggest", "recommend", "recommendation", "recommendations", "advise", "advice"}
)
#: Substring checks, not exact matches — mirrors `_HELP_PHRASES`.
_ADVICE_TRIGGER_PHRASES = (
    "should i buy", "should i sell", "should i invest", "your opinion",
    "what do you think", "good investment", "bad investment", "good buy",
    "worth buying", "worth investing",
)

_ADVICE_REPLY = (
    "This report doesn't give investment recommendations — it won't tell "
    "you whether to buy, sell, or hold. It can run a valuation estimate "
    "(\"what is this worth?\"), show how that estimate moves under "
    "different assumptions (\"how sensitive is this to the discount "
    "rate?\", \"what's the bull and bear case?\"), or read out what a trend "
    "or peer comparison actually shows (\"is the margin trend "
    "concerning?\"). Ask any of those instead."
)


def _is_advice_question(question: str) -> bool:
    normalised = question.lower()
    return bool(_tokens(question) & _ADVICE_TRIGGER_WORDS) or any(
        phrase in normalised for phrase in _ADVICE_TRIGGER_PHRASES
    )


# --- Peer comparison --------------------------------------------------------
# Peer facts (metric prefix "peer.") are already stored for a report that ran
# with peers enabled — m08_peers computes and cites them the same as any
# other derived figure. Nothing here fetches a peer's filings fresh; a
# comparison question only widens which already-stored facts are offered to
# the model, the same reuse `_wider_candidates` does for a filer's own gated
# -out metrics.

_PEER_TRIGGER_WORDS = frozenset(
    {"competitor", "competitors", "peer", "peers", "compare", "comparison", "versus"}
)


def _is_peer_question(question: str) -> bool:
    return bool(_tokens(question) & _PEER_TRIGGER_WORDS)


def _with_peer_facts(candidates: list[Fact], all_facts: Sequence[Fact]) -> list[Fact]:
    """Adds every stored peer fact to the candidate list, deduplicated.

    Plain token overlap between "how does this compare to competitors" and a
    peer fact's own metric/label is not reliable — the trigger words above
    decide the question is about peers; once decided, every peer fact this
    report has is worth offering, not just the ones that happened to share a
    word with the question.
    """
    seen = {fact.fact_id for fact in candidates}
    peer_facts = [
        fact
        for fact in all_facts
        if fact.metric.startswith("peer.") and fact.fact_id not in seen
    ]
    return (candidates + peer_facts)[:CHAT_TOOL_RESULT_FACT_LIMIT]


# --- Interpretive insight ---------------------------------------------------
# A question asking for an evaluative read (is this healthy/risky/concerning)
# rather than a specific number. Deliberately excludes forward-looking words
# ("outlook", "will") — interpreting what the filed data already shows is in
# scope; predicting what happens next is a different, larger feature this
# does not attempt. A comparison question already implies the reader wants
# this evaluative read too (see `_route_question`), so `_is_peer_question`
# also opts in without needing its own separate wording here.

_INSIGHT_TRIGGER_WORDS = frozenset(
    {
        "concerning", "concern", "concerns", "healthy", "unhealthy",
        "worrying", "worrisome", "risky",
    }
)
#: Substring checks, not exact matches — mirrors `_HELP_PHRASES`.
_INSIGHT_TRIGGER_PHRASES = ("red flag",)


def _is_insight_question(question: str) -> bool:
    normalised = question.lower()
    return bool(_tokens(question) & _INSIGHT_TRIGGER_WORDS) or any(
        phrase in normalised for phrase in _INSIGHT_TRIGGER_PHRASES
    )


# --- Conversation memory -----------------------------------------------
# Only the single immediately preceding exchange — enough to resolve a
# follow-up like "and last year's?" that names no metric on its own, without
# growing the prompt (and its cost) with a whole transcript. Used two ways:
# re-matched from alone (not concatenated with the new message — that would
# only dilute the overlap against a fact) when the new message matches
# nothing by itself, and shown to the model as context for what a follow-up
# refers to — never as something the model may answer a new figure from;
# `SYSTEM_PROMPT` still requires every figure to cite a fact id from the
# table it was actually given this turn.


async def _recent_exchange(report_id: str) -> tuple[str, str] | None:
    """The immediately preceding question and its answer, or None when there
    isn't one (no database configured, or this is the first question)."""
    if not db.is_configured():
        return None
    try:
        client = db.get_client()
        response = (
            client.table(CHAT_MESSAGES_TABLE)
            .select("role,content")
            .eq("report_id", report_id)
            .order("turn_index", desc=True)
            .limit(2)
            .execute()
        )
    except db.DatabaseError as cause:
        logger.warning(
            "Recent exchange could not be read",
            extra={"report_id": report_id},
            exc_info=cause,
        )
        return None
    except Exception as cause:  # noqa: BLE001 — degrade rather than block chat
        logger.warning(
            "Recent exchange could not be read",
            extra={"report_id": report_id},
            exc_info=cause,
        )
        return None

    rows = getattr(response, "data", None) or []
    prior_question: str | None = None
    prior_answer: str | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        role = row.get("role")
        content = row.get("content")
        if not isinstance(content, str):
            continue
        if role == str(ChatRole.USER) and prior_question is None:
            prior_question = content
        elif role == str(ChatRole.ASSISTANT) and prior_answer is None:
            prior_answer = content

    if prior_question is None or prior_answer is None:
        return None
    return prior_question, prior_answer


# --- Company website (tier 2 fallback) ---------------------------------
# Reached only when tiers 1-2 over the report's own facts come up empty and
# the reader supplied a URL. The page is fetched through `services.webfetch`,
# which is what actually enforces "somewhere safe" — this module only
# decides *whether* to fetch, never *how*.

_COMPANY_SITE_SYSTEM_PROMPT = """\
You answer one question about a company, using only the text of one web \
page supplied below. This page is the company's own website, not a \
government filing — self-reported, not audited.

Rules:

1. Use only what the page actually says. Never calculate, never recall a \
figure from memory, never estimate, never assume what a company like this \
typically reports.
2. If the page does not actually answer the question, set not_found to \
true and return an empty claims array. Do not answer a different question \
than the one asked, and do not fill a gap with a typical or \
plausible-sounding number.
3. Each claim is one complete sentence. Sentence case, no markdown, no \
bullet points, no headings. Do not use the word "AI". Do not address the \
reader.
4. Be brief. Answer the question, nothing more.
"""

_COMPANY_SITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "One sentence answering part of the "
                        "question, using only the page's own words.",
                    }
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
        "not_found": {
            "type": "boolean",
            "description": "True when the page does not answer the "
            "question asked.",
        },
    },
    "required": ["claims", "not_found"],
    "additionalProperties": False,
}


def _company_site_user_prompt(
    ticker: str, message: str, page: webfetch.FetchedPage
) -> str:
    excerpt = page.text[:CHAT_WEBFETCH_MAX_PAGE_CHARS]
    return (
        f"Ticker: {ticker}\n\n"
        f"Question: {message}\n\n"
        f"Page ({page.url}):\n{excerpt}\n"
    )


def _company_site_claim_texts(answer: dict[str, Any]) -> list[str]:
    if answer.get("not_found") is True:
        return []
    raw = answer.get("claims")
    if not isinstance(raw, list):
        return []
    return [
        item["text"].strip()
        for item in raw
        if isinstance(item, dict)
        and isinstance(item.get("text"), str)
        and item["text"].strip()
    ]


async def _answer_from_company_site(
    report_id: str, ticker: str, message: str, pasted_url: str, user_turn: ChatTurn
) -> ChatTurn:
    """Reads one reader-supplied company URL and answers from its text alone.

    Reached only as a fallback, from `answer_question`, after tiers 1-2 over
    the report's own facts came up empty.
    """
    try:
        page = await webfetch.fetch_page(pasted_url)
    except webfetch.WebfetchError as cause:
        logger.warning(
            "Company-site fetch failed",
            extra={"report_id": report_id, "url": pasted_url},
            exc_info=cause,
        )
        assistant_turn = _not_found_turn(f"That page could not be read: {cause}")
        await _persist(report_id, user_turn, assistant_turn)
        return assistant_turn

    try:
        answer = await llm.complete_json(
            _COMPANY_SITE_SYSTEM_PROMPT,
            _company_site_user_prompt(ticker, message, page),
            _COMPANY_SITE_SCHEMA,
            purpose="chat:company_site",
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

    texts = _company_site_claim_texts(answer)
    if not texts:
        assistant_turn = _not_found_turn(
            "That page does not answer this question."
        )
        await _persist(report_id, user_turn, assistant_turn)
        return assistant_turn

    claims = [
        ChatClaim(
            text=text,
            tier=SourceTier.COMPANY,
            source_url=page.url,
            source_type=SourceType.COMPANY_SITE,
        )
        for text in texts
    ]
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
        tool_calls=[{"tool": "fetch_page", "url": page.url}],
        highest_tier_used=2,
    )
    return assistant_turn


# --- Links the reader supplied with the request (tier 2) -----------------
# Read once, when the report was generated (`m13_sources`), and carried on
# the assembled document. Reached before a pasted URL or a web search:
# these pages were already fetched and already quoted, so answering from
# them costs one model call rather than a fetch plus a call — and they are
# the sources this reader chose for this report, which makes them a better
# fallback than the open web.
#
# Nothing here can produce a figure for the financial statements. A
# `SourceNote` is not a `Fact` and never becomes one; see `m13_sources`.

_SOURCE_NOTES_SYSTEM_PROMPT = """\
You answer one question about a company using notes already read off pages \
the reader supplied when they requested this report. These are not \
government filings — they are self-reported pages, and nothing verified \
that they belong to the company.

Rules:

1. Use only what the supplied notes actually say. Never calculate, never \
recall a figure from memory, never estimate.
2. Every claim you make must name the single note id it rests on, in that \
claim's note_id field. Never cite an id that is not in the list.
3. If the notes do not actually answer the question — even if they are on a \
related topic — set not_found to true and return an empty claims array.
4. Never present a note as filed, audited or confirmed. These pages were \
supplied by the reader, not verified.
5. Each claim is one complete sentence. Sentence case, no markdown, no \
bullet points, no headings. Do not use the word "AI". Do not address the \
reader.
6. Be brief. Answer the question, nothing more.
"""

_SOURCE_NOTES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "One sentence answering part of the "
                        "question.",
                    },
                    "note_id": {
                        "type": "string",
                        "description": "Id of the one supplied note this "
                        "claim rests on.",
                    },
                },
                "required": ["text", "note_id"],
                "additionalProperties": False,
            },
        },
        "not_found": {
            "type": "boolean",
            "description": "True when the supplied notes do not answer the "
            "question asked.",
        },
    },
    "required": ["claims", "not_found"],
    "additionalProperties": False,
}


def _stored_source_notes(report_id: str) -> list[SourceNote]:
    """Source notes on this report's assembled document, if it is still held.

    The document lives in memory for the life of the worker, the same way
    `runlog.get_report` does — so this degrades to no notes rather than
    failing when it has been evicted.
    """
    document = runlog.document_for(report_id)
    if not isinstance(document, ReportDocument):
        return []
    return list(document.source_notes)


def _render_source_notes(notes: Sequence[SourceNote]) -> str:
    """The notes as an id-labelled list, the way `render_fact_table` renders
    facts — an id the model must cite, resolved back in code afterwards."""
    return "\n".join(
        f"[{index}] {note.text} (read from {note.source_url})"
        for index, note in enumerate(notes)
    )


def _parse_note_claims(
    answer: dict[str, Any], notes: Sequence[SourceNote]
) -> list[ChatClaim]:
    """Claims whose cited note id actually exists. Mirrors `_parse_claims`."""
    if answer.get("not_found") is True:
        return []

    raw = answer.get("claims")
    if not isinstance(raw, list):
        return []

    claims: list[ChatClaim] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue

        note_id = item.get("note_id")
        if not isinstance(note_id, str):
            continue
        try:
            note = notes[int(note_id)]
        except (ValueError, IndexError):
            logger.warning(
                "Chat dropped a claim citing a note that was not supplied",
                extra={"note_id": note_id},
            )
            continue

        claims.append(
            ChatClaim(
                text=text.strip(),
                tier=note.tier,
                source_url=note.source_url,
                source_type=note.source_type,
            )
        )
    return claims


async def _answer_from_source_notes(
    report_id: str,
    ticker: str,
    message: str,
    notes: Sequence[SourceNote],
    user_turn: ChatTurn,
) -> ChatTurn | None:
    """Answers from links supplied with the request, or None to continue the
    cascade — an unanswerable question here is not an answer, it is a miss."""
    try:
        answer = await llm.complete_json(
            _SOURCE_NOTES_SYSTEM_PROMPT,
            (
                f"Ticker: {ticker}\n\n"
                f"Question: {message}\n\n"
                f"Notes available:\n{_render_source_notes(notes)}\n"
            ),
            _SOURCE_NOTES_SCHEMA,
            purpose="chat:source_notes",
        )
    except llm.LlmError as cause:
        logger.warning(
            "Chat model call failed",
            extra={"report_id": report_id},
            exc_info=cause,
        )
        return None

    claims = _parse_note_claims(answer, notes)
    if not claims:
        return None

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
        tool_calls=[{"tool": "source_notes", "considered": len(notes)}],
        highest_tier_used=2,
    )
    return assistant_turn


# --- General web search (tier 4, last resort) ----------------------------
# Reached only when tiers 1-2 came up empty, no pasted URL was offered (or a
# pasted URL was tried and this is chosen instead — see `answer_question`'s
# ordering), and `CHAT_WEB_SEARCH_ENABLED` is on. The search itself runs
# server-side inside Anthropic's own infrastructure — `llm.complete_json_with
# _web_search` is the only call in this module that grants it, and this is
# the only place its result reaches a claim.
#
# Every claim requires the model to name the exact URL it used, in the
# schema-constrained answer itself — not extracted from the tool's own
# citation blocks, whose exact shape this module does not depend on. A claim
# whose url does not parse is dropped, the same as an unknown fact id is
# dropped elsewhere in this module.

_WEB_SEARCH_SYSTEM_PROMPT = """\
You answer one question about a company using web search. This is the \
least reliable source available to you — not a filing, not the company's \
own site — so it is used only to state plainly what a page says, never to \
speculate.

Rules:

1. Search only for what the question actually asks. Never state a figure \
that is not written on a page you actually found.
2. Every claim must name the exact url of the page it came from, in that \
claim's source_url field. A claim with no real page behind it must not be \
made at all.
3. If nothing you find actually answers the question, set not_found to \
true and return an empty claims array. Do not answer a related-but-different \
question, and do not state something as fact because it sounds plausible.
4. Each claim is one complete sentence. Sentence case, no markdown, no \
bullet points, no headings. Do not use the word "AI". Do not address the \
reader.
5. Be brief. Answer the question, nothing more.
"""

_WEB_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "One sentence answering part of the "
                        "question.",
                    },
                    "source_url": {
                        "type": "string",
                        "description": "The exact url of the page this "
                        "claim came from.",
                    },
                },
                "required": ["text", "source_url"],
                "additionalProperties": False,
            },
        },
        "not_found": {
            "type": "boolean",
            "description": "True when nothing found answers the question "
            "asked.",
        },
    },
    "required": ["claims", "not_found"],
    "additionalProperties": False,
}


def _web_search_claims(answer: dict[str, Any]) -> list[ChatClaim]:
    if answer.get("not_found") is True:
        return []
    raw = answer.get("claims")
    if not isinstance(raw, list):
        return []

    claims: list[ChatClaim] = []
    dropped: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        url = item.get("source_url")
        if not isinstance(text, str) or not text.strip():
            continue
        if not isinstance(url, str) or not url.strip():
            continue
        try:
            claims.append(
                ChatClaim(
                    text=text.strip(),
                    tier=SourceTier.NEWS,
                    source_url=url.strip(),
                    source_type=SourceType.NEWS,
                )
            )
        except ValidationError:
            dropped.append(url)

    if dropped:
        logger.warning(
            "Dropped web-search claims with an unusable url",
            extra={"urls": dropped},
        )
    return claims


async def _answer_from_web_search(
    report_id: str, ticker: str, message: str, user_turn: ChatTurn
) -> ChatTurn:
    """Tier 4, the last resort: Anthropic's hosted web search.

    Reached only from `answer_question`, only after every trusted source
    (tiers 1-2, and a pasted company URL when one was offered) has come up
    empty, and only when `CHAT_WEB_SEARCH_ENABLED` is on.
    """
    try:
        answer = await llm.complete_json_with_web_search(
            _WEB_SEARCH_SYSTEM_PROMPT,
            f"Ticker: {ticker}\n\nQuestion: {message}\n",
            _WEB_SEARCH_SCHEMA,
            purpose="chat:web_search",
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

    claims = _web_search_claims(answer)
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
        tool_calls=[{"tool": "web_search"}],
        highest_tier_used=4,
    )
    return assistant_turn


# --- General chat (ungrounded, gated, absolute last resort) ---------------
# Reached only from `answer_question`, only after every other tier —
# facts, valuation, peer/insight widening, source notes, a pasted URL, and
# web search — has come up empty, and only when `CHAT_GENERAL_CHAT_ENABLED`
# is on (off by default, same reasoning as `CHAT_WEB_SEARCH_ENABLED`, one
# step further: this tier isn't grounded in any of this system's own data at
# all, not even a fetched page). This is the one place the "never use the
# word AI" rule is deliberately not enforced — a genuinely open-ended
# assistant that refuses to answer "are you an AI?" honestly is a worse, not
# safer, general assistant. Every other chat path, and the report view
# itself, keep that rule exactly as before.

GENERAL_CHAT_SYSTEM_PROMPT = """\
You are a helpful, general-purpose conversational assistant embedded in the \
Trisheet company-report chat panel. This particular question is not about \
the report or any filed financial data — reached only after Trisheet's own \
data, peer facts, and any supplied company page came up empty for it — so \
answer it as a knowledgeable general assistant would.

Rules:
1. Be helpful, direct, and honest, as in an ordinary conversation. You may \
answer honestly if asked what you are.
2. Never state a specific number or fact about the report's own company as \
if it were verified — you were not given its filed data this turn. If the \
question turns out to be about the company after all, say so and suggest \
the reader ask again using report language (a metric name, "valuation", \
"compare to peers").
3. Sentence case, plain prose — no markdown, no bullet points, no headings.
4. Keep answers reasonably brief, as a chat reply, not an essay.
"""

_GENERAL_CHAT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reply": {
            "type": "string",
            "description": "A brief, direct reply to the reader's message.",
        },
    },
    "required": ["reply"],
    "additionalProperties": False,
}


async def _answer_with_general_chat(
    report_id: str, message: str, user_turn: ChatTurn
) -> ChatTurn:
    """The absolute last resort: an ungrounded, general-purpose reply.

    Reached only from `answer_question`, only after every trusted and
    semi-trusted source above has come up empty, and only when
    `CHAT_GENERAL_CHAT_ENABLED` is on.
    """
    try:
        answer = await llm.complete_json(
            GENERAL_CHAT_SYSTEM_PROMPT,
            message,
            _GENERAL_CHAT_SCHEMA,
            purpose="chat:general",
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

    reply = answer.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        assistant_turn = _not_found_turn()
        await _persist(report_id, user_turn, assistant_turn)
        return assistant_turn

    assistant_turn = _new_turn(
        ChatRole.ASSISTANT, claims=(), content=reply.strip(), not_found=False
    )
    await _persist(
        report_id,
        user_turn,
        assistant_turn,
        tool_calls=[{"tool": "general_chat"}],
        highest_tier_used=None,
    )
    return assistant_turn


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


#: A sensitivity question asks how the same DCF estimate moves across
#: discount rates, rather than for a single point estimate. Both the noun
#: ("sensitivity analysis") and the adjective ("how sensitive is this") are
#: kept — `_tokens` matching is exact-token, not stemmed, and the suggested
#: chip in `suggest_questions` uses the adjective form.
_SENSITIVITY_TRIGGER_WORDS = frozenset({"sensitivity", "sensitive"})

#: A scenario question asks for named bull/bear cases rather than a single
#: point estimate. Checked against confirmed to collide with nothing else in
#: this codebase before being added (see the m07/config comments these words
#: are new alongside).
_SCENARIO_TRIGGER_WORDS = frozenset({"scenario", "scenarios", "bull", "bear"})


def _is_sensitivity_question(question: str) -> bool:
    return bool(_tokens(question) & _SENSITIVITY_TRIGGER_WORDS)


def _is_scenario_question(question: str) -> bool:
    return bool(_tokens(question) & _SCENARIO_TRIGGER_WORDS)


def _money(value: float, unit: str | None) -> str:
    suffix = f" {unit}" if unit else ""
    return f"{value:,.0f}{suffix}"


def _dcf_input_claims(
    *,
    base_fcf_fact_id: str | None,
    net_debt_fact_id: str | None,
    shares_fact_id: str | None,
    facts_by_id: dict[str, Fact],
) -> list[ChatClaim]:
    """Certified claims for the real facts the calculation actually used.

    Takes the three fact ids directly rather than a whole `DcfResult` —
    `DcfSensitivityResult` and `DcfScenarioResult` carry the same three ids
    at their own top level (the real inputs never change between points or
    cases, only the assumption does), so one function serves all three
    valuation shapes without a duck-typed or unioned parameter.
    """
    claims: list[ChatClaim] = []
    for fact_id, label in (
        (base_fcf_fact_id, "Free cash flow"),
        (net_debt_fact_id, "Net debt"),
        (shares_fact_id, "Diluted shares outstanding"),
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


def _dcf_sensitivity_claims(
    result: m07_analysis.DcfSensitivityResult, base_fact: Fact | None
) -> list[ChatClaim]:
    """One assumption claim per discount-rate point.

    Mirrors `_dcf_assumption_claims`: every point is a modelling choice, not
    a filed figure, so every claim here is `is_assumption=True`. The real
    inputs behind every point (free cash flow, net debt, shares) don't change
    between points — they're covered once by `_dcf_input_claims` at the call
    site, never repeated per point.
    """
    unit = base_fact.unit if base_fact is not None else None
    claims: list[ChatClaim] = []
    for point in result.points:
        rate_text = _pct(point.discount_rate.value)
        if point.result.unavailable_reason is not None:
            claims.append(
                ChatClaim(
                    text=(
                        f"At a {rate_text} discount rate: "
                        f"{point.result.unavailable_reason}"
                    ),
                    is_assumption=True,
                    assumption_note=point.discount_rate.note,
                )
            )
            continue

        value_text = (
            f"estimated value per share {_money(point.result.value_per_share, unit)}"
            if point.result.value_per_share is not None
            else (
                "estimated enterprise value "
                f"{_money(point.result.enterprise_value or 0.0, unit)}"
            )
        )
        claims.append(
            ChatClaim(
                text=f"At a {rate_text} discount rate: {value_text}.",
                is_assumption=True,
                assumption_note=point.discount_rate.note,
            )
        )
    return claims


def _dcf_scenario_claims(
    result: m07_analysis.DcfScenarioResult, base_fact: Fact | None
) -> list[ChatClaim]:
    """One assumption claim per named growth scenario.

    Mirrors `_dcf_sensitivity_claims` for the free-cash-flow growth axis
    instead of the discount-rate axis.
    """
    unit = base_fact.unit if base_fact is not None else None
    claims: list[ChatClaim] = []
    for case in result.cases:
        growth_text = _pct(case.fcf_growth_rate.value)
        label = f"{case.name.capitalize()} case ({growth_text} free cash flow growth)"
        if case.result.unavailable_reason is not None:
            claims.append(
                ChatClaim(
                    text=f"{label}: {case.result.unavailable_reason}",
                    is_assumption=True,
                    assumption_note=case.fcf_growth_rate.note,
                )
            )
            continue

        value_text = (
            f"estimated value per share {_money(case.result.value_per_share, unit)}"
            if case.result.value_per_share is not None
            else (
                "estimated enterprise value "
                f"{_money(case.result.enterprise_value or 0.0, unit)}"
            )
        )
        claims.append(
            ChatClaim(
                text=f"{label}: {value_text}.",
                is_assumption=True,
                assumption_note=case.fcf_growth_rate.note,
            )
        )
    return claims


#: Appended to every valuation answer. A reader asking "is this worth
#: investing in" is asking for a verdict; the estimate above is the closest
#: this system gets, and repeating the same DCF figures for a rephrased
#: question ("is it worth it?" right after "is this worth investing in?")
#: reads as broken unless the reply is explicit about why there is no
#: sharper answer coming.
_NO_RECOMMENDATION_TEXT = (
    "This is an estimate, not a recommendation — the report does not judge "
    "whether the company is worth investing in."
)


def _no_recommendation_claim() -> ChatClaim:
    return ChatClaim(
        text=_NO_RECOMMENDATION_TEXT,
        is_assumption=True,
        assumption_note=(
            "Not a sourced figure — states what this report does and does "
            "not do."
        ),
    )


async def _finish_valuation_turn(
    report_id: str, user_turn: ChatTurn, claims: list[ChatClaim], tool_name: str
) -> ChatTurn:
    """Builds and persists the assistant turn shared by every valuation
    branch (plain / sensitivity / scenario) — the only difference between
    them is which claims were built and which m07 function produced them."""
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
        tool_calls=[{"tool": tool_name}],
        highest_tier_used=None,
    )
    return assistant_turn


async def _answer_valuation_question(
    report_id: str, all_facts: Sequence[Fact], user_turn: ChatTurn, message: str
) -> ChatTurn:
    """Answers a valuation question with a DCF scenario over this filer's
    real facts, mixing certified inputs with clearly labelled assumptions.

    A plain valuation question runs one DCF at the default/supplied
    assumptions. A sensitivity question (`_is_sensitivity_question`) instead
    re-runs it across several discount rates; a scenario question
    (`_is_scenario_question`) re-runs it across named bear/base/bull growth
    cases. Both reuse `project_dcf` itself via `project_dcf_sensitivity` /
    `project_dcf_scenarios` in m07_analysis — no DCF maths lives twice.
    """
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
    facts_by_id = {fact.fact_id: fact for fact in combined}

    if _is_sensitivity_question(message):
        sensitivity = m07_analysis.project_dcf_sensitivity(combined)
        if sensitivity.unavailable_reason is not None:
            assistant_turn = _not_found_turn(sensitivity.unavailable_reason)
            await _persist(report_id, user_turn, assistant_turn)
            return assistant_turn

        base_fact = (
            facts_by_id.get(sensitivity.base_fcf_fact_id)
            if sensitivity.base_fcf_fact_id is not None
            else None
        )
        claims = (
            _dcf_input_claims(
                base_fcf_fact_id=sensitivity.base_fcf_fact_id,
                net_debt_fact_id=sensitivity.net_debt_fact_id,
                shares_fact_id=sensitivity.shares_fact_id,
                facts_by_id=facts_by_id,
            )
            + _dcf_sensitivity_claims(sensitivity, base_fact)
            + [_no_recommendation_claim()]
        )
        return await _finish_valuation_turn(
            report_id, user_turn, claims, "project_dcf_sensitivity"
        )

    if _is_scenario_question(message):
        scenario = m07_analysis.project_dcf_scenarios(combined)
        if scenario.unavailable_reason is not None:
            assistant_turn = _not_found_turn(scenario.unavailable_reason)
            await _persist(report_id, user_turn, assistant_turn)
            return assistant_turn

        base_fact = (
            facts_by_id.get(scenario.base_fcf_fact_id)
            if scenario.base_fcf_fact_id is not None
            else None
        )
        claims = (
            _dcf_input_claims(
                base_fcf_fact_id=scenario.base_fcf_fact_id,
                net_debt_fact_id=scenario.net_debt_fact_id,
                shares_fact_id=scenario.shares_fact_id,
                facts_by_id=facts_by_id,
            )
            + _dcf_scenario_claims(scenario, base_fact)
            + [_no_recommendation_claim()]
        )
        return await _finish_valuation_turn(
            report_id, user_turn, claims, "project_dcf_scenarios"
        )

    result = m07_analysis.project_dcf(combined)

    if result.unavailable_reason is not None:
        assistant_turn = _not_found_turn(result.unavailable_reason)
        await _persist(report_id, user_turn, assistant_turn)
        return assistant_turn

    base_fact = (
        facts_by_id.get(result.base_fcf_fact_id)
        if result.base_fcf_fact_id is not None
        else None
    )
    claims = (
        _dcf_input_claims(
            base_fcf_fact_id=result.base_fcf_fact_id,
            net_debt_fact_id=result.net_debt_fact_id,
            shares_fact_id=result.shares_fact_id,
            facts_by_id=facts_by_id,
        )
        + _dcf_assumption_claims(result, base_fact)
        + [_no_recommendation_claim()]
    )
    return await _finish_valuation_turn(report_id, user_turn, claims, "project_dcf")


async def answer_question(
    report_id: str, message: str, pasted_url: str | None = None
) -> ChatTurn:
    """Answers one question about a completed report.

    Args:
        report_id: The report the question is about.
        message: The reader's question.
        pasted_url: A company URL the reader supplied. Read only as a
            fallback, when tiers 1-2 over the report's own facts come up
            empty — never consulted otherwise.

    Never raises: every failure mode returns a `ChatTurn` stating what
    happened, so the endpoint has nothing to catch.
    """
    turn = await _route_question(report_id, message, pasted_url)
    remaining = await _turns_remaining(report_id)
    if remaining is None:
        return turn
    return turn.model_copy(update={"turns_remaining": remaining})


async def _route_question(
    report_id: str, message: str, pasted_url: str | None = None
) -> ChatTurn:
    """The cascade itself. Wrapped by `answer_question`, which attaches the
    rate-limit count uniformly to whatever turn this produces."""
    report = runlog.get_report(report_id)
    ticker = report.ticker if report is not None else report_id

    user_turn = _new_turn(
        ChatRole.USER, claims=(), content=message, not_found=False
    )

    small_talk = _small_talk_reply(message)
    if small_talk is not None:
        assistant_turn = _new_turn(
            ChatRole.ASSISTANT, claims=(), content=small_talk, not_found=False
        )
        await _persist(report_id, user_turn, assistant_turn)
        return assistant_turn

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

    if (
        _is_valuation_question(message)
        or _is_sensitivity_question(message)
        or _is_scenario_question(message)
    ):
        return await _answer_valuation_question(
            report_id, all_facts, user_turn, message
        )

    if _is_advice_question(message):
        assistant_turn = _new_turn(
            ChatRole.ASSISTANT, claims=(), content=_ADVICE_REPLY, not_found=False
        )
        await _persist(report_id, user_turn, assistant_turn)
        return assistant_turn

    prior = await _recent_exchange(report_id)

    highest_tier: int | None = None
    candidates = _match_facts(message, all_facts)
    if not candidates and prior is not None:
        # A follow-up like "and last year's?" names no metric on its own —
        # re-resolving the topic from the previous question alone is what
        # lets it match. Concatenating the two strings instead would only
        # dilute the overlap: the follow-up's own words ("last", "year")
        # share nothing with the fact, so appending them raises the token
        # count without raising how much of it actually overlaps.
        candidates = _match_facts(prior[0], all_facts)
    if candidates:
        highest_tier = 1
    else:
        candidates = _wider_candidates(all_facts, message)
        if not candidates and prior is not None:
            candidates = _wider_candidates(all_facts, prior[0])
        if candidates:
            highest_tier = 2

    if _is_peer_question(message):
        candidates = _with_peer_facts(candidates, all_facts)
        if candidates and highest_tier is None:
            highest_tier = 1

    # A comparison question already implies the reader wants the evaluative
    # read `_is_insight_question` unlocks, so it opts in too, without needing
    # its own separate wording — and gets the same peer widening, since an
    # insight question ("any red flags in the margin trend?") benefits from
    # the same peer context even when it never says "peer" or "compare".
    insight_mode = _is_insight_question(message) or _is_peer_question(message)
    if insight_mode:
        candidates = _with_peer_facts(candidates, all_facts)
        if candidates and highest_tier is None:
            highest_tier = 1

    if not candidates:
        # Links supplied with the report request come first among the
        # fallbacks: already fetched, already quoted, and chosen by this
        # reader for this report. A miss here falls through rather than
        # ending the turn — having supplied a link should never cost the
        # reader the fallbacks they would otherwise have had.
        notes = _stored_source_notes(report_id)
        if notes:
            from_notes = await _answer_from_source_notes(
                report_id, ticker, message, notes, user_turn
            )
            if from_notes is not None:
                return from_notes

        # Tier 2 (a pasted company URL) and tier 4 (general web search) are
        # alternate last resorts, not chained attempts within one request —
        # a pasted URL is the reader's own explicit fallback and is trusted
        # as such; web search is only reached when they did not supply one.
        if pasted_url is not None:
            return await _answer_from_company_site(
                report_id, ticker, message, pasted_url, user_turn
            )
        if CHAT_WEB_SEARCH_ENABLED:
            return await _answer_from_web_search(
                report_id, ticker, message, user_turn
            )
        # Absolute last resort, and gated off by default — web search is
        # tried first when both are on, since it is at least reading real
        # pages; general chat is grounded in nothing at all.
        if CHAT_GENERAL_CHAT_ENABLED:
            return await _answer_with_general_chat(report_id, message, user_turn)
        assistant_turn = _not_found_turn()
        await _persist(report_id, user_turn, assistant_turn)
        return assistant_turn

    allowed = {fact.fact_id: fact for fact in candidates}

    try:
        answer = await llm.complete_json(
            INSIGHT_SYSTEM_PROMPT if insight_mode else SYSTEM_PROMPT,
            _build_user_prompt(ticker, message, candidates, prior=prior),
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


# --- Suggested questions -------------------------------------------------
# Deterministic, not model-generated: a suggestion is offered only when the
# metric behind it is genuinely present in this report's own stored facts,
# so a suggestion can never promise an answer the assistant cannot give.

#: Metric to question template, in priority order.
_SUGGESTION_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("income.revenue", "What was revenue?"),
    ("income.net_income", "What was net income?"),
    ("cashflow.free_cash_flow", "What was free cash flow?"),
    ("derived.net_debt", "What is the net debt?"),
    ("liquidity.current_ratio", "What is the current ratio?"),
)

#: A wall of chips is not a help.
_MAX_SUGGESTIONS = 4


async def suggest_questions(report_id: str) -> list[str]:
    """Example questions this report's own stored data can actually answer.

    Only checks what is already stored — not the wider, freshly-derived
    metrics tier 2 can reach — so this stays a cheap read, not a recompute,
    for what is only ever a UI hint.
    """
    try:
        all_facts = await m06_factstore.load_facts(report_id)
    except m06_factstore.FactStoreError:
        return []

    present = {fact.metric for fact in all_facts}
    suggestions = [
        question
        for metric, question in _SUGGESTION_TEMPLATES
        if metric in present
    ]

    if "cashflow.free_cash_flow" in present:
        suggestions.append("What is this company worth?")
        suggestions.append("How sensitive is this to the discount rate?")
        suggestions.append("What's the bull and bear case?")

    return suggestions[:_MAX_SUGGESTIONS]


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


async def _recent_assistant_turn_count(report_id: str) -> int | None:
    """Assistant turns this report has used in the current rolling window,
    or None when no database is configured — the one count `is_rate_limited`
    and `_turns_remaining` both need, kept in one place so the two can never
    disagree about what "recent" means.
    """
    if not db.is_configured():
        return None

    window_start = dt.datetime.now(dt.UTC) - dt.timedelta(
        minutes=CHAT_RATE_LIMIT_WINDOW_MINUTES
    )
    try:
        client = db.get_client()
        response = (
            client.table(CHAT_MESSAGES_TABLE)
            .select("id")
            .eq("report_id", report_id)
            .eq("role", str(ChatRole.ASSISTANT))
            .gte("created_at", window_start.isoformat())
            .execute()
        )
    except db.DatabaseError as cause:
        logger.warning(
            "Recent turn count could not be read; allowing the request",
            extra={"report_id": report_id},
            exc_info=cause,
        )
        return None
    except Exception as cause:  # noqa: BLE001 — degrade rather than block chat
        logger.warning(
            "Recent turn count could not be read; allowing the request",
            extra={"report_id": report_id},
            exc_info=cause,
        )
        return None

    rows = getattr(response, "data", None) or []
    return len(rows)


async def is_rate_limited(report_id: str) -> bool:
    """True when this report has already used its turn budget for the
    current rolling window.

    Called from `main.py` before any LLM call, so a request over budget
    costs nothing beyond one read. Never rate-limits when no database is
    configured — the same "optional dependency degrades" rule every other
    read in this system follows. That does mean an unconfigured deployment
    has no rate limit of any kind; closing that gap is a deployment
    requirement, not something this function can enforce on its own.
    """
    count = await _recent_assistant_turn_count(report_id)
    return count is not None and count >= CHAT_RATE_LIMIT_MAX_TURNS


async def _turns_remaining(report_id: str) -> int | None:
    """Questions left in the current rate-limit window, after this turn.

    Attached to the `ChatTurn` returned from `answer_question` so the
    interface can show a plain count rather than a surprise wall. None when
    no database is configured, the same case `is_rate_limited` cannot
    enforce a limit for either — the frontend simply omits the count then.
    """
    count = await _recent_assistant_turn_count(report_id)
    if count is None:
        return None
    return max(0, CHAT_RATE_LIMIT_MAX_TURNS - count)


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


__all__ = ["answer_question", "is_rate_limited", "suggest_questions"]
