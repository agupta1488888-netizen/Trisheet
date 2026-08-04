"""m13_sources — reads links the reader attached to a report request.

Responsibility
    A reader requesting a report may attach a link: an investor-relations
    page, a press release, any other resource they consider relevant. This
    module reads those pages and quotes what they say, so the report can cite
    a source the filings do not contain.

Why this cannot produce a Fact
    `SOURCE_TYPE_TIERS` maps `company_site` to tier 2, and
    `SECTION_3_ALLOWED_TIERS` admits tier 2. So a `Fact` built from a pasted
    page would pass `m06_factstore._admit` and be rendered in the financial
    highlights table, beside figures traced to a specific accession number.
    That is precisely the claim the product exists to make, so the separation
    is structural rather than a rule someone must remember: this module does
    not import `Fact`, produces `SourceNote` instead, and `SourceNote` is a
    shape the fact store cannot accept.

    The consequence worth stating plainly: nothing here can put a number into
    the financial highlights table, no matter what the page says or what the
    model does with it.

What is not verified
    That the link is the company's own site. `Company` carries no website
    field and EDGAR submissions supply none, so there is no deterministic
    check available — and per CLAUDE.md tier enforcement is code, never
    prompting, which rules out asking a model to vouch for it. Every note is
    therefore marked `is_user_supplied`, and the interface says "supplied by
    you, not verified" rather than implying a check that did not happen.

Degradation
    Never raises. A page that will not fetch, a model that will not answer, a
    page with nothing relevant on it — each yields no notes for that link and
    leaves the rest of the run untouched. Rule 6: only EDGAR is a hard
    dependency, and a report whose supplied link was unreachable is still a
    report.

    These are three different failures, and the pipeline step has to be able
    to tell them apart: a page that could not be fetched, a page the model
    call itself failed on, and a page that was read and answered but had
    nothing worth recording. `read_sources` returns which links hit which of
    the first two alongside the notes, rather than collapsing all three into
    an empty list and forcing the caller to guess. See `_LinkStatus`.

Public interface
    read_sources(urls, ticker) -> SourceReadResult
"""

from __future__ import annotations

import asyncio
import datetime as dt
import enum
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.config import (
    SOURCE_LINK_MAX_PAGE_CHARS,
    SOURCE_LINKS_MAX,
    SOURCE_NOTES_MAX_PER_URL,
)
from app.models import SourceNote, SourceTier, SourceType
from app.services import llm, webfetch

logger = logging.getLogger(__name__)


class _LinkStatus(enum.Enum):
    """What happened to one supplied link — three outcomes, not two.

    `MODEL_FAILED` and `OK`-with-no-notes both leave `notes` empty for that
    link, but they are not the same finding: one is Anthropic's API refusing
    or erroring on a perfectly good page, the other is the model genuinely
    reporting the page has nothing to say. Collapsing them cost real time to
    diagnose once already — a production run kept reporting "nothing to add"
    for a page that, read locally seconds later with the same code, produced
    six accurate notes. The distinction exists so that never has to be
    re-diagnosed by hand again.
    """

    #: The page was read and the model answered, with or without notes.
    OK = "ok"
    #: `webfetch.fetch_page` itself failed — refused, timed out, blocked.
    UNREACHABLE = "unreachable"
    #: The page was read fine; the model call raised.
    MODEL_FAILED = "model_failed"


@dataclass(frozen=True, slots=True)
class SourceReadResult:
    """What reading the supplied links produced.

    `notes` empty has three different causes a reader needs told apart: a
    link that could not be reached at all, a link the model call itself
    failed on, and a link that was read and answered but had nothing worth
    recording — a cookie banner, a login wall, a page that says nothing about
    the company. `unreachable_urls` and `model_failed_urls` name which links
    hit the first two cases, so the pipeline step can report what actually
    happened rather than a message honest about all three and useful for
    none of them.
    """

    notes: list[SourceNote] = field(default_factory=list)
    unreachable_urls: list[str] = field(default_factory=list)
    model_failed_urls: list[str] = field(default_factory=list)

#: The rules. Adapted from `chat_agent._COMPANY_SITE_SYSTEM_PROMPT`, which
#: solves the same problem for one chat turn: a page is not a filing, and the
#: only safe thing to do with it is repeat what it actually says.
SYSTEM_PROMPT = """\
You read one web page and record what it says about a company. This page was \
supplied by the reader. It is not a government filing — it is self-reported, \
not audited, and may be promotional.

Rules:

1. Record only what the page actually states. Never calculate, never recall a \
figure from memory, never estimate, never infer what a company like this \
usually reports.
2. Prefer statements that a filing would not already carry: strategy, \
products, customers, partnerships, announcements, management commentary.
3. If the page says nothing substantive about the company — a login wall, a \
cookie notice, an error page, an index of links — set not_found to true and \
return an empty notes array. An empty answer is correct and expected; do not \
manufacture something to fill the array.
4. Each note is one complete sentence, and must stand on its own without the \
page in front of the reader. Sentence case, no markdown, no bullet points, \
no headings. Do not use the word "AI". Do not address the reader.
5. Never present anything on this page as audited, filed, or confirmed.
"""

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "One complete sentence stating "
                        "something this page says about the company.",
                    }
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
        "not_found": {
            "type": "boolean",
            "description": "True when the page says nothing substantive "
            "about the company.",
        },
    },
    "required": ["notes", "not_found"],
    "additionalProperties": False,
}


def _user_prompt(ticker: str, page: webfetch.FetchedPage) -> str:
    excerpt = page.text[:SOURCE_LINK_MAX_PAGE_CHARS]
    return f"Ticker: {ticker}\n\nPage ({page.url}):\n{excerpt}\n"


def _note_texts(answer: dict[str, Any]) -> list[str]:
    """The model's notes, or nothing. Mirrors `_company_site_claim_texts`."""
    if answer.get("not_found") is True:
        return []
    raw = answer.get("notes")
    if not isinstance(raw, list):
        return []
    return [
        item["text"].strip()
        for item in raw
        if isinstance(item, dict)
        and isinstance(item.get("text"), str)
        and item["text"].strip()
    ][:SOURCE_NOTES_MAX_PER_URL]


async def _read_one(
    url: str, ticker: str
) -> tuple[list[SourceNote], _LinkStatus]:
    """Reads one page. Returns `(notes, status)` rather than raising."""
    try:
        page = await webfetch.fetch_page(url)
    except webfetch.WebfetchError as cause:
        logger.warning(
            "Supplied link could not be read",
            extra={"url": url},
            exc_info=cause,
        )
        return [], _LinkStatus.UNREACHABLE

    try:
        answer = await llm.complete_json(
            SYSTEM_PROMPT,
            _user_prompt(ticker, page),
            _SCHEMA,
            purpose="sources:read",
        )
    except llm.LlmError as cause:
        # Logged at warning with the real cause, since the pipeline feed can
        # only ever say "the model call failed" — this is where "failed with
        # what" actually lives.
        logger.warning(
            "Supplied link could not be summarised",
            extra={"url": page.url},
            exc_info=cause,
        )
        return [], _LinkStatus.MODEL_FAILED

    fetched_at = dt.datetime.now(dt.UTC)
    notes: list[SourceNote] = []
    for text in _note_texts(answer):
        try:
            notes.append(
                SourceNote(
                    text=text,
                    # The URL actually reached, after redirects — citing what
                    # was typed would point at something that was not read.
                    source_url=page.url,
                    source_type=SourceType.COMPANY_SITE,
                    tier=SourceTier.COMPANY,
                    fetched_at=fetched_at,
                )
            )
        except ValidationError as cause:
            # A note the model returned that this shape cannot represent is
            # dropped, never coerced — the same discipline m10 and the chat
            # apply to a citation that does not check out.
            logger.warning(
                "Dropped a source note that could not be represented",
                extra={"url": page.url},
                exc_info=cause,
            )

    logger.info(
        "Supplied link read", extra={"url": page.url, "notes": len(notes)}
    )
    return notes, _LinkStatus.OK


async def read_sources(
    urls: Sequence[str], ticker: str
) -> SourceReadResult:
    """Reads every supplied link and returns what they say.

    Args:
        urls: Links the reader attached. More than `SOURCE_LINKS_MAX` are
            ignored — the request model caps this too, but a cap enforced
            only at the boundary is one an internal caller can bypass.
        ticker: For the prompt's context line only. Nothing about the answer
            is trusted to match it.

    Never raises: a link that cannot be read contributes no notes, and the
    report is assembled without it.
    """
    accepted = list(urls)[:SOURCE_LINKS_MAX]
    if not accepted:
        return SourceReadResult()

    # Concurrent because each is a separate host and they are independent;
    # `return_exceptions` so one failure cannot cancel the others, though
    # `_read_one` is already written not to raise.
    results = await asyncio.gather(
        *(_read_one(url, ticker) for url in accepted),
        return_exceptions=True,
    )

    notes: list[SourceNote] = []
    unreachable: list[str] = []
    model_failed: list[str] = []
    for url, result in zip(accepted, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("Supplied link raised unexpectedly", exc_info=result)
            unreachable.append(url)
            continue
        url_notes, status = result
        notes.extend(url_notes)
        if status is _LinkStatus.UNREACHABLE:
            unreachable.append(url)
        elif status is _LinkStatus.MODEL_FAILED:
            model_failed.append(url)
    return SourceReadResult(
        notes=notes, unreachable_urls=unreachable, model_failed_urls=model_failed
    )


__all__ = ["SourceReadResult", "read_sources"]
