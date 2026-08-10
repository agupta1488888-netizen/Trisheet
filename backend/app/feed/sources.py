"""EDGAR's two firehose formats, turned into filings.

Responsibility
    Parse the current-events Atom feed and the daily form index into
    `ParsedFiling` records. Pure: text in, records out. Nothing here fetches,
    stores or decides what to do next.

Why two formats
    The Atom feed is the fast lane. It carries a real dissemination timestamp
    and — the reason it is worth preferring — the 8-K item numbers themselves,
    in the summary EDGAR renders for each entry. That means a current report's
    headline is known the moment it is seen, without fetching the filing.

    The daily index is the safety net. It is one file covering an entire day,
    so nothing filed can escape it, but it carries only a date and says nothing
    about items. A filing that arrives this way is marked `items=None` —
    unknown, not empty — and enrichment resolves it later.

    That distinction matters. `items=()` means EDGAR listed items and none were
    worth reporting, which is grounds for discarding the filing. `items=None`
    means nobody has looked yet, which is grounds for looking.

Public interface
    parse_current_events(xml) -> tuple[ParsedFiling, ...]
    parse_daily_index(text) -> tuple[ParsedFiling, ...]
    daily_index_url(day) -> str
    current_events_url(form) -> str
    headline_for(form, items) -> str
    to_feed_item(filing, entry) -> FeedItem
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass

from lxml import etree

from app.config import (
    AMENDMENT_FORM_SUFFIX,
    DEVELOPMENT_ITEMS,
    FEED_AMENDMENT_SUFFIX,
    FEED_CURRENT_EVENTS_COUNT,
    FEED_FORM_LABELS,
    FEED_FORMS,
    FEED_TIMEZONE,
    SEC_CURRENT_EVENTS_URL_TEMPLATE,
    SEC_DAILY_INDEX_URL_TEMPLATE,
)
from app.models import FeedItem
from app.modules.m09_developments import keep_items
from app.services.edgar import pad_cik

logger = logging.getLogger(__name__)

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

#: `8-K - NexMetals Mining Corp. (0000795800) (Filer)` — the form, the company
#: and the CIK, in EDGAR's own title format for a current-events entry.
_ENTRY_TITLE = re.compile(
    r"^\s*(?P<form>.+?)\s+-\s+(?P<name>.+?)\s+\((?P<cik>\d{4,10})\)"
)

#: `urn:tag:sec.gov,2008:accession-number=0001493152-26-036682`
_ENTRY_ACCESSION = re.compile(r"accession-number=(?P<accession>[\d-]+)")

#: `<br>Item 7.01: Regulation FD Disclosure` inside an entry's summary. The
#: numbers are what matter; the labels are re-derived from DEVELOPMENT_ITEMS so
#: that the feed and the report say the same thing about the same item.
_SUMMARY_ITEM = re.compile(r"Item\s+(?P<number>\d{1,2}\.\d{2})")

#: One data row of a daily form index. Parsed by shape rather than by fixed
#: column offsets: the column widths in these files have changed over the years
#: and the header itself wraps, but a form type never contains two consecutive
#: spaces and a company name is always separated from the CIK by at least two.
_INDEX_ROW = re.compile(
    r"^(?P<form>\S(?:.*?\S)?)\s{2,}(?P<name>\S(?:.*?\S)?)\s{2,}"
    r"(?P<cik>\d{1,10})\s+(?P<date>\d{8})\s+(?P<file>edgar/\S+)\s*$"
)

#: Lines of a daily index that are preamble rather than filings.
_INDEX_PREAMBLE = (
    "Description:",
    "Last Data Received:",
    "Comments:",
    "Anonymous FTP:",
    "Form Type",
    "Date Filed",
    "---",
)

_ARCHIVES_FILING_INDEX = (
    "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/"
    "{accession_dashed}-index.htm"
)


@dataclass(frozen=True, slots=True)
class ParsedFiling:
    """One filing as EDGAR announced it, before anything has been looked up."""

    accession_no: str
    cik: str
    company_name: str
    form: str
    filed_at: dt.datetime
    source_url: str
    #: None when the source did not say. Empty when it said, and none of the
    #: items were worth reporting.
    items: tuple[str, ...] | None

    @property
    def base_form(self) -> str:
        """The form without its amendment suffix. `8-K/A` -> `8-K`."""
        return self.form.split(AMENDMENT_FORM_SUFFIX, 1)[0].strip()

    @property
    def is_amendment(self) -> bool:
        return AMENDMENT_FORM_SUFFIX in self.form


# --- URLs -------------------------------------------------------------------


def current_events_url(form: str) -> str:
    """The current-events feed for one form type, newest first."""
    return SEC_CURRENT_EVENTS_URL_TEMPLATE.format(
        form=form, count=FEED_CURRENT_EVENTS_COUNT
    )


def daily_index_url(day: dt.date) -> str:
    """The form index for one day."""
    quarter = (day.month - 1) // 3 + 1
    return SEC_DAILY_INDEX_URL_TEMPLATE.format(
        year=day.year, quarter=quarter, date=day.strftime("%Y%m%d")
    )


def _filing_index_url(cik: str, accession_no: str) -> str:
    """The human-readable index page for a filing.

    Built rather than taken from the Atom link, so that a filing discovered
    through the daily index — which carries only a path to the raw submission
    text — addresses the same page as one discovered through Atom.
    """
    return _ARCHIVES_FILING_INDEX.format(
        cik_int=str(int(cik)),
        accession_nodash=accession_no.replace("-", ""),
        accession_dashed=accession_no,
    )


# --- Headlines --------------------------------------------------------------


def headline_for(form: str, items: tuple[str, ...] | None) -> str:
    """One line describing a filing, in EDGAR's words or the form's own name.

    Never invented. When the filer listed items, the headline is what EDGAR
    publishes those items to mean; otherwise it is what the form is. A filing
    is never described by anything neither of those two say.
    """
    base = form.split(AMENDMENT_FORM_SUFFIX, 1)[0].strip()
    suffix = FEED_AMENDMENT_SUFFIX if AMENDMENT_FORM_SUFFIX in form else ""

    labels = tuple(DEVELOPMENT_ITEMS[item] for item in items or ())
    if not labels:
        return FEED_FORM_LABELS.get(base, f"{base} filed") + suffix
    if len(labels) == 1:
        return labels[0] + suffix
    joined = ", ".join(label.lower() for label in labels[1:])
    return f"{labels[0]}; {joined}{suffix}"


# --- Current-events Atom ----------------------------------------------------


def _parse_entry(entry: etree._Element) -> ParsedFiling | None:
    """One Atom entry, or None when it is not a filing this feed can use."""
    title = entry.findtext("atom:title", namespaces=_ATOM_NS) or ""
    matched = _ENTRY_TITLE.match(title)
    if matched is None:
        return None

    identifier = entry.findtext("atom:id", namespaces=_ATOM_NS) or ""
    accession = _ENTRY_ACCESSION.search(identifier)
    if accession is None:
        return None

    updated = entry.findtext("atom:updated", namespaces=_ATOM_NS) or ""
    try:
        filed_at = dt.datetime.fromisoformat(updated)
    except ValueError:
        return None
    if filed_at.tzinfo is None:
        return None

    # The summary lists the filer's own item numbers for a current report, and
    # is absent or itemless for every other form. `keep_items` is m09's, so a
    # filing described one way in the feed cannot be described another way in a
    # report built from the same 8-K.
    summary = entry.findtext("atom:summary", namespaces=_ATOM_NS) or ""
    raw_items = _SUMMARY_ITEM.findall(summary)
    items = keep_items(raw_items) if raw_items else None

    cik = pad_cik(matched.group("cik"))
    accession_no = accession.group("accession")
    return ParsedFiling(
        accession_no=accession_no,
        cik=cik,
        company_name=matched.group("name").strip(),
        form=matched.group("form").strip(),
        filed_at=filed_at,
        source_url=_filing_index_url(cik, accession_no),
        items=items,
    )


def parse_current_events(xml: str | bytes) -> tuple[ParsedFiling, ...]:
    """Reads EDGAR's current-events feed.

    A malformed document yields nothing and a malformed entry is skipped: this
    runs unattended every ninety seconds, and one bad entry must not cost the
    other ninety-nine.
    """
    payload = xml.encode("utf-8") if isinstance(xml, str) else xml
    try:
        # recover=True because EDGAR declares ISO-8859-1 and occasionally emits
        # a byte that is not valid in it. Losing the feed over one character in
        # one company name is not a trade worth making.
        root = etree.fromstring(payload, parser=etree.XMLParser(recover=True))
    except etree.XMLSyntaxError:
        logger.warning("Current-events feed could not be parsed")
        return ()
    if root is None:
        return ()

    filings: list[ParsedFiling] = []
    for entry in root.findall("atom:entry", namespaces=_ATOM_NS):
        parsed = _parse_entry(entry)
        if parsed is not None:
            filings.append(parsed)
    return tuple(filings)


# --- Daily form index -------------------------------------------------------


def parse_daily_index(text: str) -> tuple[ParsedFiling, ...]:
    """Reads a daily form index.

    The index states a date and not a time, so `filed_at` is midnight Eastern
    on that date — deliberately the earliest instant it could have been filed,
    so a reconciled filing sorts below the precisely-timestamped ones from the
    same day rather than jumping above them.
    """
    zone = _eastern()
    filings: list[ParsedFiling] = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(_INDEX_PREAMBLE):
            continue

        matched = _INDEX_ROW.match(line)
        if matched is None:
            continue

        form = matched.group("form").strip()
        if form.split(AMENDMENT_FORM_SUFFIX, 1)[0].strip() not in FEED_FORMS:
            continue

        accession = matched.group("file").rsplit("/", 1)[-1].removesuffix(".txt")
        if not accession:
            continue

        try:
            filed_date = dt.datetime.strptime(matched.group("date"), "%Y%m%d")
        except ValueError:
            continue

        cik = pad_cik(matched.group("cik"))
        filings.append(
            ParsedFiling(
                accession_no=accession,
                cik=cik,
                company_name=matched.group("name").strip(),
                form=form,
                filed_at=filed_date.replace(tzinfo=zone),
                source_url=_filing_index_url(cik, accession),
                items=None,
            )
        )

    return tuple(filings)


def _eastern() -> dt.tzinfo:
    """EDGAR's own clock, falling back to UTC if the tz database is absent.

    A missing tz database is a packaging problem on some Windows installs, and
    it must not take the feed down: an eight-hour error in the assumed filing
    time of a reconciled filing is a cosmetic ordering issue, whereas raising
    here would lose the whole reconciliation sweep.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(FEED_TIMEZONE)
    except ZoneInfoNotFoundError:
        logger.warning(
            "Time zone database is unavailable; assuming UTC for filing dates",
            extra={"zone": FEED_TIMEZONE},
        )
        return dt.UTC


# --- Conversion -------------------------------------------------------------


def to_feed_item(
    filing: ParsedFiling,
    *,
    ticker: str | None,
    company_name: str | None = None,
) -> FeedItem:
    """A parsed filing as the row that will be stored and rendered.

    `company_name` overrides what EDGAR's firehose said, because the tracked
    universe carries the name a reader recognises — "Apple Inc." rather than
    the shouted "APPLE INC" some entries use.
    """
    items = filing.items or ()
    return FeedItem(
        accession_no=filing.accession_no,
        cik=filing.cik,
        ticker=ticker,
        company_name=company_name or filing.company_name,
        form=filing.form,
        filed_at=filing.filed_at,
        items=items,
        item_labels=tuple(DEVELOPMENT_ITEMS[item] for item in items),
        headline=headline_for(filing.form, filing.items),
        source_url=filing.source_url,
    )
