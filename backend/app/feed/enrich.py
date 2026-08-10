"""Reading a filing's press release.

Responsibility
    Turn a bare current report into one that says something. Two requests at
    most: the filing's index page, which lists its item numbers and its
    exhibits, and the EX-99.1 press release itself, which carries the numbers
    the press will report tomorrow.

    This is where the feed's "news" comes from, and it is worth being precise
    about what that means. Nothing here is written, summarised or paraphrased.
    A sentence in the feed is a sentence the company published, quoted whole,
    linked to the filing it was quoted from. That is the only kind of claim
    this product makes anywhere, and a landing page is not an exception.

    Guidance is separated from results by `m09.parse_release`, not by anything
    written here — the same function the report uses, so a forward-looking
    statement is classified identically in both places and can never be
    rendered as a reported figure in either.

Degradation
    Enrichment never fails a filing. An index page that cannot be read, an
    exhibit that 404s, a release with nothing quotable in it — each costs the
    quotations and nothing else, and the attempt is still recorded as made.

    That last part is deliberate. Leaving a permanently-unreadable filing
    marked unenriched would put it at the head of an oldest-first queue
    forever, and it would starve every filing behind it.

Public interface
    enrich(client, item) -> Enrichment
    parse_items(index_html) -> tuple[str, ...]
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field

from app.config import (
    EARNINGS_RELEASE_EXHIBIT,
    EARNINGS_RELEASE_ITEM,
    MAX_FILING_TEXT_BYTES,
)
from app.feed.sources import headline_for
from app.models import FeedItem
from app.modules.m02_discovery import parse_exhibits
from app.modules.m09_developments import keep_items, parse_release
from app.services import document
from app.services.edgar import EdgarClient, EdgarError

logger = logging.getLogger(__name__)

#: `Item 2.02: Results of Operations and Financial Condition`, as the filing
#: index page states it. The same shape the Atom summary uses.
_INDEX_ITEM = re.compile(r"Item\s+(?P<number>\d{1,2}\.\d{2})")


@dataclass(frozen=True, slots=True)
class Enrichment:
    """What reading a filing produced.

    `discard` is the one outcome that is not an upgrade: EDGAR listed the
    filing's items, and none of them are items a company profile reports. An
    8-K whose only content is its own exhibit index is noise, and m09 already
    drops it from the report timeline for exactly this reason.
    """

    items: tuple[str, ...]
    headline: str
    result_sentences: tuple[str, ...] = ()
    guidance_sentences: tuple[str, ...] = ()
    exhibit_url: str | None = None
    discard: bool = False
    #: A factory, not a literal: a bare `dt.datetime.now(dt.UTC)` default would
    #: be evaluated once when this class is defined and every enrichment for
    #: the life of the process would claim to have happened at import time.
    enriched_at: dt.datetime = field(
        default_factory=lambda: dt.datetime.now(dt.UTC)
    )


def parse_items(index_html: str) -> tuple[str, ...]:
    """The item numbers a filing index page lists, filtered to the ones kept.

    Uses m09's `keep_items`, so the feed and the report agree about which items
    are worth reporting and in what order a reader sees them.
    """
    return keep_items(_INDEX_ITEM.findall(index_html))


async def _read_exhibit(client: EdgarClient, url: str) -> str | None:
    """The press release as text, or None when it cannot be read."""
    try:
        body = await client.get_bytes(url)
    except EdgarError:
        logger.warning("Could not read press release exhibit", extra={"url": url})
        return None

    if len(body) > MAX_FILING_TEXT_BYTES:
        logger.warning(
            "Press release exhibit is larger than the parse ceiling",
            extra={"url": url, "bytes": len(body)},
        )
        return None

    return document.html_to_text(body.decode("utf-8", errors="replace")) or None


async def enrich(client: EdgarClient, item: FeedItem) -> Enrichment:
    """Reads a filing's index page, and its press release when it has one.

    Costs one request, or two when the filing reports results. A filing whose
    items are already known from the Atom feed and do not include a results
    announcement still costs the index request, because the exhibit list is
    only on that page — but it never costs the second.
    """
    now = dt.datetime.now(dt.UTC)
    known_items = item.items

    try:
        index_html = await client.get_text(str(item.source_url))
    except EdgarError:
        # The filing stays in the feed with the headline it already has. It is
        # marked enriched regardless, so it leaves the queue.
        logger.warning(
            "Could not read filing index",
            extra={"accession_no": item.accession_no},
        )
        return Enrichment(
            items=known_items, headline=item.headline, enriched_at=now
        )

    # The Atom feed is authoritative when it spoke; the index page is what
    # answers for a filing found through the daily index, which says nothing
    # about items.
    items = known_items or parse_items(index_html)

    if not items:
        # Nothing the filer listed is reportable. For a current report that is
        # grounds for dropping it; for a 10-Q there were never items to list
        # and the form's own name is the headline.
        return Enrichment(
            items=(),
            headline=item.headline,
            discard=item.form.startswith("8-K"),
            enriched_at=now,
        )

    headline = headline_for(item.form, items)

    if EARNINGS_RELEASE_ITEM not in items:
        return Enrichment(items=items, headline=headline, enriched_at=now)

    exhibit = next(
        (
            candidate
            for candidate in parse_exhibits(index_html)
            if candidate.exhibit_type == EARNINGS_RELEASE_EXHIBIT
        ),
        None,
    )
    if exhibit is None:
        return Enrichment(items=items, headline=headline, enriched_at=now)

    url = str(exhibit.url)
    text = await _read_exhibit(client, url)
    if text is None:
        return Enrichment(
            items=items, headline=headline, exhibit_url=url, enriched_at=now
        )

    extract = parse_release(text)
    return Enrichment(
        items=items,
        headline=headline,
        result_sentences=extract.results,
        guidance_sentences=extract.guidance,
        exhibit_url=url,
        enriched_at=now,
    )
