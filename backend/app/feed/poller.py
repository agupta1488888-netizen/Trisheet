"""The loop that keeps the feed fresh.

Responsibility
    Drive the other four modules on a schedule, forever, without ever taking
    the API down with it.

The schedule
    EDGAR is not a 24-hour source. It accepts and disseminates filings on
    business days between roughly 06:00 and 22:00 Eastern, and publishes
    nothing at all overnight or at a weekend. A loop that polled every ninety
    seconds regardless would spend two thirds of its requests confirming that
    nothing had changed, so outside those hours it drops to a slow heartbeat.

    That is also why the interface reports when it last checked rather than
    claiming to be live: on a Sunday the honest thing for the page to say is
    that the newest filing is Friday's, not that something is broken.

Three things happen per cycle
    Poll          one request per tracked form against the current-events
                  feed, filtered to the universe.
    Enrich        a bounded number of current reports have their press release
                  read. Bounded because this is the feed's real claim on the
                  shared EDGAR budget, and a user waiting for a report must
                  never be queued behind a hundred earnings 8-Ks.
    Reconcile     twice an hour, the whole of today's form index, to catch
                  anything that scrolled off the fast lane's window. This is
                  also when filings past the retention window are dropped.

Failure
    A cycle that raises is logged and the loop continues. EDGAR outages,
    Supabase blips and malformed responses are all normal weather for something
    that runs unattended for months; none of them are grounds for the loop to
    stop, and none of them can reach the API, which never awaits this task.

Public interface
    run_forever(stop) -> None
    run_once() -> CycleReport
    start(loop_stop) -> asyncio.Task | None
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.config import (
    FEED_EDGAR_CLOSE_HOUR_ET,
    FEED_EDGAR_OPEN_HOUR_ET,
    FEED_ENRICHMENT_MAX_PER_CYCLE,
    FEED_FAILURE_LOG_CEILING,
    FEED_FORMS,
    FEED_IDLE_INTERVAL_SECONDS,
    FEED_POLL_INTERVAL_SECONDS,
    FEED_RECONCILE_INTERVAL_SECONDS,
    FEED_RETENTION_DAYS,
    FEED_TIMEZONE,
    get_settings,
)
from app.feed import enrich as enrichment_module
from app.feed import sources, store, universe
from app.logging_config import configure_logging
from app.models import FeedItem
from app.services import db, edgar

logger = logging.getLogger(__name__)

#: Saturday and Sunday, as `date.weekday()` numbers them.
_WEEKEND = frozenset({5, 6})

#: When the poller last completed a cycle against EDGAR, for `GET /feed` to
#: report. Held in this process rather than in the database on purpose: it
#: describes *this* process's polling, and the deployment is a single process
#: precisely because the EDGAR token bucket is not shared across them (see
#: services/edgar.py). A deployment that ever scales out would want this in a
#: table, and would want the rate limiter there first.
_last_cycle_at: dt.datetime | None = None


def last_checked_at() -> dt.datetime | None:
    """When this process last finished a polling cycle, if it has."""
    return _last_cycle_at


@dataclass(slots=True)
class CycleReport:
    """What one cycle did. Returned for tests and the one-shot CLI."""

    polled: int = 0
    stored: int = 0
    enriched: int = 0
    discarded: int = 0
    reconciled: int = 0
    pruned: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _now_eastern() -> dt.datetime:
    """The current time on EDGAR's clock, not the container's.

    A Railway container runs in UTC, so evaluating the publishing window
    against local time would idle the poller through the entire American
    filing day and wake it up at night.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return dt.datetime.now(ZoneInfo(FEED_TIMEZONE))
    except ZoneInfoNotFoundError:
        # Without a tz database the safe assumption is that EDGAR is open:
        # polling a little when nothing is happening costs a few requests,
        # whereas assuming it is closed would mean never polling at all.
        logger.warning(
            "Time zone database is unavailable; assuming EDGAR is publishing",
            extra={"zone": FEED_TIMEZONE},
        )
        return dt.datetime.now(dt.UTC).replace(
            hour=(FEED_EDGAR_OPEN_HOUR_ET + FEED_EDGAR_CLOSE_HOUR_ET) // 2
        )


def is_publishing(moment: dt.datetime | None = None) -> bool:
    """Whether EDGAR is inside its dissemination window.

    Deliberately ignores federal holidays. Getting them wrong in either
    direction is worse than the cost of being right by accident: polling four
    times on Thanksgiving is a rounding error, while a hardcoded holiday table
    would go stale and silently stop the feed on a day filings were made.
    """
    at = moment or _now_eastern()
    if at.weekday() in _WEEKEND:
        return False
    return FEED_EDGAR_OPEN_HOUR_ET <= at.hour < FEED_EDGAR_CLOSE_HOUR_ET


# --- The three phases of a cycle --------------------------------------------


async def _poll(
    client: edgar.EdgarClient, tracked: universe.Universe, report: CycleReport
) -> None:
    """Reads the current-events feed for each tracked form."""
    fresh: list[FeedItem] = []
    seen: set[str] = set()

    for form in FEED_FORMS:
        try:
            xml = await client.get_text(sources.current_events_url(form))
        except edgar.EdgarError as cause:
            report.errors.append(f"current events {form}: {cause}")
            continue

        for filing in sources.parse_current_events(xml):
            report.polled += 1
            entry = tracked.entry(filing.cik)
            if entry is None or filing.accession_no in seen:
                continue
            if filing.base_form not in FEED_FORMS:
                # `type=8-K` matches 8-K12B and friends by prefix.
                continue
            if filing.items is not None and not filing.items:
                # EDGAR listed the items and none are reportable. Never stored,
                # so it never has to be discarded later.
                continue
            seen.add(filing.accession_no)
            fresh.append(
                sources.to_feed_item(
                    filing, ticker=entry.ticker, company_name=entry.name
                )
            )

    if fresh:
        report.stored += store.insert_new(fresh)


async def _enrich(client: edgar.EdgarClient, report: CycleReport) -> None:
    """Reads press releases for a bounded number of unenriched filings.

    Sequential by design. Concurrency here would multiply the feed's
    instantaneous claim on the token bucket, which is the one thing this loop
    must not do to a user waiting on a report.
    """
    pending = store.unenriched(FEED_ENRICHMENT_MAX_PER_CYCLE)
    for item in pending:
        try:
            result = await enrichment_module.enrich(client, item)
        except edgar.EdgarError as cause:
            report.errors.append(f"enrich {item.accession_no}: {cause}")
            continue

        if result.discard:
            store.discard(item.accession_no)
            report.discarded += 1
            continue

        store.record_enrichment(item.accession_no, result)
        report.enriched += 1


async def _reconcile(
    client: edgar.EdgarClient, tracked: universe.Universe, report: CycleReport
) -> None:
    """Sweeps today's form index, then drops filings past the retention window.

    The index for a given day is rewritten as filings arrive, so sweeping
    today's is what catches a filing that scrolled off the fast lane's
    hundred-entry window during a burst.
    """
    today = _now_eastern().date()
    try:
        text = await client.get_text(sources.daily_index_url(today))
    except (edgar.EdgarNotFoundError, edgar.EdgarForbiddenError):
        # No index for today yet — normal before the first filing of the day,
        # and at a weekend. Not an error.
        #
        # 403 counts as absence here, and it is the only status that ever
        # happens: the Archives answer a daily index that has not been built
        # with a refusal rather than a 404, so catching only NotFound made
        # every Sunday and every pre-index morning report a failed cycle.
        #
        # Swallowing a refusal is safe *for this one URL* because it cannot
        # hide a genuine block. The poll phase runs first and reads four
        # current-events feeds from the same host on the same client; if SEC
        # were actually refusing this deployment, those would already have
        # filled `report.errors` before reconciliation was reached.
        logger.debug(
            "No daily index published yet", extra={"date": today.isoformat()}
        )
        text = ""
    except edgar.EdgarError as cause:
        report.errors.append(f"daily index: {cause}")
        text = ""

    if text:
        fresh: list[FeedItem] = []
        for filing in sources.parse_daily_index(text):
            entry = tracked.entry(filing.cik)
            if entry is None:
                continue
            fresh.append(
                sources.to_feed_item(
                    filing, ticker=entry.ticker, company_name=entry.name
                )
            )
        report.reconciled = len(fresh)
        if fresh:
            report.stored += store.insert_new(fresh)

    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=FEED_RETENTION_DAYS)
    report.pruned = store.prune(cutoff)


# --- The loop ---------------------------------------------------------------


async def run_once(*, reconcile: bool = True) -> CycleReport:
    """One full cycle. Raises nothing that `run_forever` would not survive."""
    global _last_cycle_at  # noqa: PLW0603 — one process-wide clock by design

    report = CycleReport()
    tracked = universe.load()
    client = edgar.get_client()

    await _poll(client, tracked, report)
    await _enrich(client, report)
    if reconcile:
        await _reconcile(client, tracked, report)

    # Recorded even when the cycle collected errors: the page's question is
    # "when did this deployment last reach EDGAR", and a cycle that read three
    # of four forms did reach it. A cycle that raised outright does not get
    # here, which is the distinction that matters.
    _last_cycle_at = dt.datetime.now(dt.UTC)
    return report


async def run_forever(stop: asyncio.Event) -> None:
    """Polls until `stop` is set.

    Never raises. A cycle that fails is counted and the loop sleeps as usual —
    an EDGAR outage should not end the process, and a database that comes back
    in ten minutes should find the poller still running.
    """
    consecutive_failures = 0
    last_reconciled = 0.0

    logger.info(
        "Filing feed poller started",
        extra={"universe": universe.load().size},
    )

    while not stop.is_set():
        publishing = is_publishing()
        now = asyncio.get_running_loop().time()
        due = now - last_reconciled >= FEED_RECONCILE_INTERVAL_SECONDS

        try:
            report = await run_once(reconcile=publishing and due)
        except Exception:  # noqa: BLE001 — the loop outlives every failure
            consecutive_failures += 1
            if consecutive_failures <= FEED_FAILURE_LOG_CEILING:
                logger.warning("Feed cycle failed", exc_info=True)
        else:
            if publishing and due:
                last_reconciled = now

            if report.ok:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures <= FEED_FAILURE_LOG_CEILING:
                    logger.warning(
                        "Feed cycle completed with errors",
                        extra={"errors": report.errors},
                    )

            # Only when something changed. A cycle that confirms nothing has
            # been filed is the common case and is not worth a line every
            # ninety seconds for the life of the deployment.
            if report.stored or report.enriched or report.discarded:
                logger.info(
                    "Feed cycle complete",
                    extra={
                        "stored": report.stored,
                        "enriched": report.enriched,
                        "discarded": report.discarded,
                        "pruned": report.pruned,
                    },
                )

        delay = (
            FEED_POLL_INTERVAL_SECONDS if publishing else FEED_IDLE_INTERVAL_SECONDS
        )
        # Waiting on the event rather than sleeping means shutdown is immediate
        # instead of taking up to fifteen minutes.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=delay)

    logger.info("Filing feed poller stopped")


def start(stop: asyncio.Event) -> asyncio.Task[None] | None:
    """Starts the poller if it is configured to run, else explains why not.

    Returns None rather than raising: a feed that cannot start is a degraded
    landing page, not a reason for the API to refuse to serve.
    """
    settings = get_settings()

    if not settings.feed_enabled:
        logger.info("Filing feed is disabled; not polling")
        return None
    if not settings.edgar_configured:
        logger.warning(
            "Filing feed cannot poll without an EDGAR contact email",
            extra={"setting": "EDGAR_CONTACT_EMAIL"},
        )
        return None
    if not db.is_configured():
        logger.warning(
            "Filing feed cannot poll without a database to write to",
            extra={"setting": "SUPABASE_URL"},
        )
        return None

    try:
        universe.load()
    except universe.UniverseSeedError:
        logger.exception("Filing feed universe could not be loaded; not polling")
        return None

    return asyncio.create_task(run_forever(stop), name="feed-poller")


# --- One-shot operator entry point ------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Runs a single cycle and reports what it did.

    For verifying a deployment's configuration without waiting on the loop, and
    for seeing exactly how many EDGAR requests one cycle costs.
    """
    parser = argparse.ArgumentParser(
        prog="python -m app.feed.poller",
        description="Run one cycle of the live filing feed poller.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle and exit. The only supported mode; the "
        "continuous loop runs inside the API process.",
    )
    parser.add_argument(
        "--no-reconcile",
        action="store_true",
        help="Skip the daily-index sweep and the retention prune.",
    )
    arguments = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)

    if not settings.edgar_configured:
        sys.stdout.write(
            "No SEC contact email is configured. Set EDGAR_CONTACT_EMAIL in "
            "backend/.env; SEC refuses requests that do not identify a "
            "contact.\n"
        )
        return 1
    if not db.is_configured():
        sys.stdout.write(
            "No database is configured. Set SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY; the feed has nowhere to write.\n"
        )
        return 1

    async def _cycle() -> CycleReport:
        try:
            return await run_once(reconcile=not arguments.no_reconcile)
        finally:
            await edgar.close_client()

    report = asyncio.run(_cycle())

    sys.stdout.write(f"universe    {universe.load().size}\n")
    sys.stdout.write(f"polled      {report.polled}\n")
    sys.stdout.write(f"stored      {report.stored}\n")
    sys.stdout.write(f"enriched    {report.enriched}\n")
    sys.stdout.write(f"discarded   {report.discarded}\n")
    sys.stdout.write(f"reconciled  {report.reconciled}\n")
    sys.stdout.write(f"pruned      {report.pruned}\n")
    for failure in report.errors:
        sys.stdout.write(f"error       {failure}\n")

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
