"""Persistence for the live filing feed.

Responsibility
    Read and write `feed_items`. The only module in this package that touches
    the database.

Why every write is an upsert that ignores conflicts
    Both polling lanes re-see the same filings constantly — that is the point
    of having two of them. `accession_no` is the primary key and a filing is
    immutable, so re-seeing one is a no-op rather than an update. This is what
    makes the reconciliation sweep safe to run over a whole day's index every
    half hour: it can only ever add what the fast lane missed, and can never
    overwrite the precise dissemination timestamp the fast lane recorded with
    the date-only one the index carries.

    Enrichment is the single exception, and it updates named columns only.

Degradation
    Unlike the report pipeline, which can run against EDGAR alone and hold its
    run record in memory, this package is pointless without a database: the
    whole purpose is for one process's polling to serve every later request.
    So these functions raise `DatabaseError`, and the poller treats a failed
    cycle as a failed cycle. The API's read path catches it and returns an
    empty page rather than a 500.

Public interface
    insert_new(items) -> int
    unenriched(limit) -> list[FeedItem]
    record_enrichment(accession_no, enrichment) -> None
    discard(accession_no) -> None
    recent(limit, form, cik) -> list[FeedItem]
    latest_filed_at() -> datetime | None
    prune(before) -> int
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from typing import Any

from app.config import DEVELOPMENT_ITEMS
from app.feed.enrich import Enrichment
from app.models import FeedItem
from app.services import db

logger = logging.getLogger(__name__)

FEED_ITEMS_TABLE = "feed_items"

#: Columns read back when rebuilding a `FeedItem`. Explicit, so that a schema
#: addition cannot silently start arriving as an unexpected key.
_COLUMNS = (
    "accession_no,cik,ticker,company_name,form,filed_at,items,item_labels,"
    "headline,result_sentences,guidance_sentences,source_url,exhibit_url,"
    "enriched_at"
)


def _row(item: FeedItem) -> dict[str, Any]:
    """A feed item as the columns of its table."""
    return {
        "accession_no": item.accession_no,
        "cik": item.cik,
        "ticker": item.ticker,
        "company_name": item.company_name,
        "form": item.form,
        "filed_at": item.filed_at.isoformat(),
        "items": list(item.items),
        "item_labels": list(item.item_labels),
        "headline": item.headline,
        "result_sentences": list(item.result_sentences),
        "guidance_sentences": list(item.guidance_sentences),
        "source_url": str(item.source_url),
        "exhibit_url": str(item.exhibit_url) if item.exhibit_url else None,
        "enriched_at": item.enriched_at.isoformat() if item.enriched_at else None,
    }


def _item(row: dict[str, Any]) -> FeedItem | None:
    """One row as a feed item, or None when the row cannot be trusted.

    A row that fails validation is skipped rather than raised over: the feed is
    a display surface, and one unparseable row must not empty the page.
    """
    try:
        return FeedItem.model_validate(
            {
                "accession_no": row["accession_no"],
                "cik": row["cik"],
                "ticker": row.get("ticker"),
                "company_name": row["company_name"],
                "form": row["form"],
                "filed_at": row["filed_at"],
                "items": tuple(row.get("items") or ()),
                "item_labels": tuple(row.get("item_labels") or ()),
                "headline": row["headline"],
                "source_url": row["source_url"],
                "result_sentences": tuple(row.get("result_sentences") or ()),
                "guidance_sentences": tuple(row.get("guidance_sentences") or ()),
                "exhibit_url": row.get("exhibit_url"),
                "enriched_at": row.get("enriched_at"),
            }
        )
    except (KeyError, ValueError):
        logger.warning(
            "Skipping unreadable feed row",
            extra={"accession_no": row.get("accession_no")},
        )
        return None


def _fail(action: str, cause: Exception) -> db.DatabaseUnavailableError:
    """One typed failure for every transport error supabase-py can raise."""
    logger.warning("Feed store could not %s", action, exc_info=cause)
    return db.DatabaseUnavailableError(f"Could not {action}: {cause}")


def insert_new(items: Sequence[FeedItem]) -> int:
    """Stores filings not already known. Returns how many rows were written.

    `ignore_duplicates` makes this `on conflict do nothing`, which is what lets
    both lanes offer the same filing without either one clobbering the other.

    Raises:
        DatabaseError: the write could not be attempted or was refused.
    """
    if not items:
        return 0

    client = db.get_client()
    try:
        response = (
            client.table(FEED_ITEMS_TABLE)
            .upsert(
                [_row(item) for item in items],
                on_conflict="accession_no",
                ignore_duplicates=True,
            )
            .execute()
        )
    except Exception as cause:  # noqa: BLE001 — supabase-py raises untyped
        raise _fail("store filings", cause) from cause

    return len(getattr(response, "data", None) or [])


def unenriched(limit: int) -> list[FeedItem]:
    """Current reports whose press release has not been read, newest first.

    Newest first, not oldest: if the queue is ever deeper than one cycle can
    drain, the filings a reader is most likely to be looking at are the ones
    that should be upgraded first.

    Raises:
        DatabaseError: the read could not be attempted or was refused.
    """
    client = db.get_client()
    try:
        response = (
            client.table(FEED_ITEMS_TABLE)
            .select(_COLUMNS)
            .is_("enriched_at", "null")
            .order("filed_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as cause:  # noqa: BLE001 — supabase-py raises untyped
        raise _fail("read the enrichment queue", cause) from cause

    rows = getattr(response, "data", None) or []
    return [item for row in rows if (item := _item(row))]


def record_enrichment(accession_no: str, enrichment: Enrichment) -> None:
    """Writes what reading a filing produced.

    Raises:
        DatabaseError: the write could not be attempted or was refused.
    """
    client = db.get_client()
    try:
        (
            client.table(FEED_ITEMS_TABLE)
            .update(
                {
                    "items": list(enrichment.items),
                    "item_labels": [
                        DEVELOPMENT_ITEMS[item] for item in enrichment.items
                    ],
                    "headline": enrichment.headline,
                    "result_sentences": list(enrichment.result_sentences),
                    "guidance_sentences": list(enrichment.guidance_sentences),
                    "exhibit_url": enrichment.exhibit_url,
                    "enriched_at": enrichment.enriched_at.isoformat(),
                }
            )
            .eq("accession_no", accession_no)
            .execute()
        )
    except Exception as cause:  # noqa: BLE001 — supabase-py raises untyped
        raise _fail("record enrichment", cause) from cause


def discard(accession_no: str) -> None:
    """Removes a filing that turned out to have nothing reportable in it.

    Raises:
        DatabaseError: the write could not be attempted or was refused.
    """
    client = db.get_client()
    try:
        (
            client.table(FEED_ITEMS_TABLE)
            .delete()
            .eq("accession_no", accession_no)
            .execute()
        )
    except Exception as cause:  # noqa: BLE001 — supabase-py raises untyped
        raise _fail("discard a filing", cause) from cause


def recent(
    limit: int, *, form: str | None = None, cik: str | None = None
) -> list[FeedItem]:
    """The newest filings, optionally narrowed to one form or one filer.

    Raises:
        DatabaseError: the read could not be attempted or was refused.
    """
    client = db.get_client()
    try:
        query = client.table(FEED_ITEMS_TABLE).select(_COLUMNS)
        if form is not None:
            query = query.eq("form", form)
        if cik is not None:
            query = query.eq("cik", cik)
        response = query.order("filed_at", desc=True).limit(limit).execute()
    except Exception as cause:  # noqa: BLE001 — supabase-py raises untyped
        raise _fail("read the feed", cause) from cause

    rows = getattr(response, "data", None) or []
    return [item for row in rows if (item := _item(row))]


def latest_filed_at() -> dt.datetime | None:
    """When the most recent stored filing was disseminated, if there is one.

    Raises:
        DatabaseError: the read could not be attempted or was refused.
    """
    client = db.get_client()
    try:
        response = (
            client.table(FEED_ITEMS_TABLE)
            .select("filed_at")
            .order("filed_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as cause:  # noqa: BLE001 — supabase-py raises untyped
        raise _fail("read the newest filing", cause) from cause

    rows = getattr(response, "data", None) or []
    if not rows:
        return None
    try:
        return dt.datetime.fromisoformat(rows[0]["filed_at"])
    except (KeyError, ValueError):
        return None


def prune(before: dt.datetime) -> int:
    """Drops filings older than the retention window. Returns rows removed.

    Raises:
        DatabaseError: the write could not be attempted or was refused.
    """
    client = db.get_client()
    try:
        response = (
            client.table(FEED_ITEMS_TABLE)
            .delete()
            .lt("filed_at", before.isoformat())
            .execute()
        )
    except Exception as cause:  # noqa: BLE001 — supabase-py raises untyped
        raise _fail("prune old filings", cause) from cause

    return len(getattr(response, "data", None) or [])
