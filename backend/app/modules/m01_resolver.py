"""m01 — ticker and company-name resolution.

Responsibility
    Turn user input — a ticker or a company name — into a `Company`: CIK
    (zero-padded to 10), filer type, SIC code, sector, fiscal year end and
    reporting currency.

    Nothing downstream may assume a domestic filer or a USD reporter. Filer
    type is decided from the annual form the company most recently filed, and
    reporting currency is read from the units its own XBRL uses.

    When the input matches more than one entity, candidates are returned and
    nothing is chosen. Guessing which company the reader meant would put a
    correct-looking report about the wrong company in front of them.

Public interface
    resolve(query, client) -> Resolution
    load_company(client, cik, ticker) -> Company
    load_index(client) -> tuple[IndexEntry, ...]
    normalise_name(name) -> str
"""

from __future__ import annotations

import logging
import re
from typing import Any, NamedTuple

from app.config import (
    ANNUAL_FORM_TO_FILER_TYPE,
    COMPANY_NAME_SUFFIXES,
    COMPANY_TICKERS_TTL_SECONDS,
    CURRENCY_PROBE_CONCEPTS,
    MAX_RESOLUTION_CANDIDATES,
    NON_CURRENCY_UNIT_KEYS,
    SEC_COMPANY_TICKERS_URL,
    SEC_SUBMISSIONS_SHARD_URL_TEMPLATE,
    XBRL_CONCEPT_TTL_SECONDS,
)
from app.models import Candidate, Company, FilerType, Resolution, ResolutionOutcome
from app.services.edgar import (
    EdgarClient,
    EdgarError,
    EdgarNotFoundError,
    company_concept_url,
    pad_cik,
    submissions_url,
)

logger = logging.getLogger(__name__)

CURRENCY_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")
_PUNCTUATION = re.compile(r"[^a-z0-9 ]+")
_WHITESPACE = re.compile(r"\s+")


class ResolutionError(Exception):
    """Input could not be resolved, for a reason worth showing the reader."""


class IndexEntry(NamedTuple):
    """One row of the SEC ticker index."""

    cik: str
    ticker: str
    name: str


# --- Name handling ----------------------------------------------------------


def normalise_name(name: str) -> str:
    """Reduces a company name to a comparable form.

    Drops punctuation and trailing corporate suffixes so that "NIKE, Inc." and
    "Nike" compare equal. Suffixes are stripped repeatedly because names like
    "Acme Holdings Ltd" carry more than one.
    """
    lowered = _PUNCTUATION.sub(" ", name.lower())
    collapsed = _WHITESPACE.sub(" ", lowered).strip()

    words = collapsed.split(" ")
    while len(words) > 1 and words[-1] in COMPANY_NAME_SUFFIXES:
        words.pop()
    return " ".join(words)


# --- Ticker index -----------------------------------------------------------


async def load_index(client: EdgarClient) -> tuple[IndexEntry, ...]:
    """Loads the SEC ticker index. Cached daily; companies list and delist.

    Public because m08 resolves every peer candidate against this same index
    before allowing it into a report. There is one authoritative answer to
    "is this a live filer", and both modules must get it from the same place.
    """
    payload = await client.get_json(
        SEC_COMPANY_TICKERS_URL, ttl=COMPANY_TICKERS_TTL_SECONDS
    )

    entries: list[IndexEntry] = []
    for row in payload.values():
        if not isinstance(row, dict):
            continue
        cik_raw = row.get("cik_str")
        ticker = row.get("ticker")
        title = row.get("title")
        if cik_raw is None or not ticker or not title:
            continue
        entries.append(
            IndexEntry(cik=pad_cik(cik_raw), ticker=str(ticker), name=str(title))
        )

    if not entries:
        message = "The SEC ticker index came back empty. Try again shortly."
        raise ResolutionError(message)
    return tuple(entries)


def _match(query: str, index: tuple[IndexEntry, ...]) -> list[IndexEntry]:
    """Finds index rows for a query, most specific interpretation first."""
    ticker_query = query.upper()
    exact_ticker = [entry for entry in index if entry.ticker.upper() == ticker_query]
    if exact_ticker:
        return exact_ticker

    normalised = normalise_name(query)
    if not normalised:
        return []

    exact_name = [
        entry for entry in index if normalise_name(entry.name) == normalised
    ]
    if exact_name:
        return exact_name

    return [entry for entry in index if normalised in normalise_name(entry.name)]


def _candidates(matches: list[IndexEntry]) -> tuple[Candidate, ...]:
    """One candidate per distinct filer, in the order first encountered."""
    seen: dict[str, IndexEntry] = {}
    for entry in matches:
        seen.setdefault(entry.cik, entry)
    return tuple(
        Candidate(cik=entry.cik, ticker=entry.ticker, name=entry.name)
        for entry in list(seen.values())[:MAX_RESOLUTION_CANDIDATES]
    )


def _preferred_entry(matches: list[IndexEntry], query: str) -> IndexEntry:
    """Picks the ticker to display when one filer has several share classes."""
    ticker_query = query.upper()
    for entry in matches:
        if entry.ticker.upper() == ticker_query:
            return entry
    return min(matches, key=lambda entry: (len(entry.ticker), entry.ticker))


# --- Submissions ------------------------------------------------------------


def _string_column(recent: dict[str, Any], key: str) -> list[str]:
    """Reads one parallel array from the submissions document."""
    column = recent.get(key)
    if not isinstance(column, list):
        return []
    return [str(item) if item is not None else "" for item in column]


def _base_form(form: str) -> str:
    """Strips the amendment suffix: '10-K/A' -> '10-K'."""
    return form.split("/")[0].strip()


def _latest_annual_form(recent: dict[str, Any]) -> tuple[str, str] | None:
    """Returns (form, filing_date) for the most recent annual report."""
    forms = _string_column(recent, "form")
    dates = _string_column(recent, "filingDate")

    best: tuple[str, str] | None = None
    for form, filed in zip(forms, dates, strict=False):
        if _base_form(form) not in ANNUAL_FORM_TO_FILER_TYPE:
            continue
        if best is None or filed > best[1]:
            best = (_base_form(form), filed)
    return best


async def _determine_filer_type(
    client: EdgarClient, submissions: dict[str, Any]
) -> FilerType:
    """Decides filer type from the annual form most recently filed.

    A company that migrated from 20-F to 10-K is classified by what it files
    now, which is why this reads the latest annual form rather than any match.
    """
    filings = submissions.get("filings")
    filings_map = filings if isinstance(filings, dict) else {}

    recent = filings_map.get("recent")
    best = _latest_annual_form(recent if isinstance(recent, dict) else {})

    if best is None:
        # Long filing histories are sharded out of the main document. Only pay
        # for those requests when the recent window held no annual report.
        for shard_name in _shard_names(filings_map):
            shard = await client.get_json(
                SEC_SUBMISSIONS_SHARD_URL_TEMPLATE.format(name=shard_name)
            )
            candidate = _latest_annual_form(shard)
            if candidate is not None and (best is None or candidate[1] > best[1]):
                best = candidate

    if best is None:
        message = (
            "No annual filing found for this company. Trisheet builds profiles "
            "from 10-K, 20-F or 40-F filings, and this filer has none."
        )
        raise ResolutionError(message)

    return FilerType(ANNUAL_FORM_TO_FILER_TYPE[best[0]])


def _shard_names(filings_map: dict[str, Any]) -> list[str]:
    files = filings_map.get("files")
    if not isinstance(files, list):
        return []
    names: list[str] = []
    for shard in files:
        if isinstance(shard, dict) and isinstance(shard.get("name"), str):
            names.append(shard["name"])
    return names


# --- Reporting currency -----------------------------------------------------


def _currency_from_units(units: dict[str, Any]) -> str | None:
    """Picks the currency a filer actually reports in.

    A filer may present a second currency for convenience translation, so the
    code backing the most reported facts wins rather than the first key seen.
    """
    best: tuple[str, int] | None = None
    for key, series in units.items():
        if key in NON_CURRENCY_UNIT_KEYS or not CURRENCY_CODE_PATTERN.match(key):
            continue
        count = len(series) if isinstance(series, list) else 0
        if best is None or count > best[1]:
            best = (key, count)
    return best[0] if best is not None else None


async def _determine_reporting_currency(
    client: EdgarClient, cik: str
) -> str | None:
    """Reads the reporting currency from the filer's own XBRL units.

    Returns None when it cannot be determined. A missing currency renders as
    "Not disclosed"; it is never assumed to be USD.
    """
    for taxonomy, tag in CURRENCY_PROBE_CONCEPTS:
        url = company_concept_url(cik, taxonomy, tag)
        try:
            payload = await client.get_json(url, ttl=XBRL_CONCEPT_TTL_SECONDS)
        except EdgarNotFoundError:
            continue
        except EdgarError:
            logger.warning(
                "Currency probe failed", extra={"cik": cik, "taxonomy": taxonomy}
            )
            continue

        units = payload.get("units")
        if not isinstance(units, dict):
            continue
        currency = _currency_from_units(units)
        if currency is not None:
            return currency

    logger.info("Reporting currency undetermined", extra={"cik": cik})
    return None


# --- Public interface -------------------------------------------------------


async def resolve(query: str, client: EdgarClient) -> Resolution:
    """Resolves a ticker or company name to a single filer, or to candidates."""
    cleaned = query.strip()
    if not cleaned:
        message = "Enter a ticker or a company name."
        raise ResolutionError(message)

    index = await load_index(client)
    matches = _match(cleaned, index)

    if not matches:
        return Resolution(outcome=ResolutionOutcome.NOT_FOUND, query=cleaned)

    distinct_ciks = {entry.cik for entry in matches}
    if len(distinct_ciks) > 1:
        logger.info(
            "Query matched several filers",
            extra={"query": cleaned, "matches": len(distinct_ciks)},
        )
        return Resolution(
            outcome=ResolutionOutcome.AMBIGUOUS,
            query=cleaned,
            candidates=_candidates(matches),
        )

    entry = _preferred_entry(matches, cleaned)
    company = await load_company(client, entry.cik, ticker=entry.ticker)
    return Resolution(
        outcome=ResolutionOutcome.RESOLVED, query=cleaned, company=company
    )


async def load_company(
    client: EdgarClient, cik: str, ticker: str | None = None
) -> Company:
    """Builds a Company from the submissions document for a known CIK."""
    padded = pad_cik(cik)
    submissions = await client.get_json(submissions_url(padded))

    filer_type = await _determine_filer_type(client, submissions)
    currency = await _determine_reporting_currency(client, padded)

    tickers = submissions.get("tickers")
    resolved_ticker = ticker
    if resolved_ticker is None and isinstance(tickers, list) and tickers:
        resolved_ticker = str(tickers[0])

    sic = submissions.get("sic")
    fiscal_year_end = submissions.get("fiscalYearEnd")

    return Company(
        cik=padded,
        ticker=resolved_ticker or "",
        name=str(submissions.get("name", "")),
        filer_type=filer_type,
        sic_code=str(sic) if sic else None,
        sector=_optional_str(submissions.get("sicDescription")),
        fiscal_year_end=str(fiscal_year_end) if fiscal_year_end else None,
        reporting_currency=currency,
        headquarters=_headquarters(submissions),
        exchange=_exchange(submissions),
        state_of_incorporation=_optional_str(
            submissions.get("stateOfIncorporation")
        ),
        employees=await _employee_count(client, padded),
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# --- Identity ---------------------------------------------------------------
# Read off the submissions document EDGAR already served to determine the
# filer type, so none of this costs an extra request. Each one is optional:
# a filer that does not state its address renders "Not disclosed", the same
# rule that governs every other absent value. Nothing here is inferred from
# narrative text — a headquarters guessed from prose would be a fabrication
# wearing a Tier 1 citation.


def _headquarters(submissions: dict[str, Any]) -> str | None:
    """The business address as city and region, or None.

    Deliberately not the street. A profile states where a company is run
    from; the postal detail belongs to the filing, which the reader can open.
    """
    addresses = submissions.get("addresses")
    if not isinstance(addresses, dict):
        return None
    business = addresses.get("business")
    if not isinstance(business, dict):
        return None

    city = _optional_str(business.get("city"))
    region = _optional_str(business.get("stateOrCountry"))
    parts = [part for part in (city, region) if part is not None]
    if not parts:
        return None
    return ", ".join(parts)


def _exchange(submissions: dict[str, Any]) -> str | None:
    """Where the security lists. Every exchange named, not just the first —
    a dual-listed filer is dual-listed, and picking one would say otherwise.
    """
    exchanges = submissions.get("exchanges")
    if not isinstance(exchanges, list):
        return None
    named = [
        text
        for text in (_optional_str(entry) for entry in exchanges)
        if text is not None
    ]
    if not named:
        return None
    return ", ".join(dict.fromkeys(named))


async def _employee_count(client: EdgarClient, cik: str) -> int | None:
    """Headcount from the filer's own dei tagging, or None.

    Most filers state their headcount in the text of Item 1 and never tag it,
    so None is the ordinary outcome rather than an error. It renders as "Not
    disclosed". Reading the number out of prose instead was considered and
    rejected: a figure parsed from a sentence is not a reported figure, and
    this system does not manufacture one.
    """
    url = company_concept_url(cik, "dei", "EntityNumberOfEmployees")
    try:
        payload = await client.get_json(url, ttl=XBRL_CONCEPT_TTL_SECONDS)
    except EdgarNotFoundError:
        return None
    except EdgarError:
        logger.warning("Employee count probe failed", extra={"cik": cik})
        return None

    units = payload.get("units")
    if not isinstance(units, dict):
        return None

    latest: tuple[str, int] | None = None
    for entries in units.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            value = entry.get("val")
            end = entry.get("end")
            if not isinstance(value, int) or not isinstance(end, str):
                continue
            if value < 0:
                continue
            if latest is None or end > latest[0]:
                latest = (end, value)

    return None if latest is None else latest[1]
