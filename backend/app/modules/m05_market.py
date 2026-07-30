"""m05 — market data. THE ONLY MODULE PERMITTED TO IMPORT A MARKET PROVIDER.

Responsibility
    Fetch price, market capitalisation and trading multiples. Everything it
    emits is Tier 3 and is therefore barred from section 3 by m06 and m11.

Constraint
    If any other file in this repository reaches a market data provider, that
    is a bug. This module is the physical expression of the source-tier rule —
    it is the single choke point through which Tier 3 data can enter.

    Three walls stop a market figure reaching the financial highlights, and
    they are independent of one another:

    1. Here. Every emitted metric must carry a prefix in
       `MARKET_METRIC_PREFIXES`, which map to section 5. Anything else is
       dropped before it leaves this module, with the rejection logged. A
       future edit that names a fact `income.revenue` produces no fact at all.
    2. m06. A `market_data` source type must claim tier 3, and a tier 3 fact
       offered for a section 3 metric is refused at write time.
    3. m11. A tier 3 fact found anywhere in section 3 fails the report.

    Wall 1 is the one that makes this structural rather than procedural: the
    module cannot express a section 3 fact, so no caller can ask it to.

Provider chain
    Providers are tried in the order `MARKET_PROVIDER_CHAIN` gives. The first
    that returns a usable quote wins; a provider that errors, times out or
    answers with nothing is logged and the chain moves on. A keyed provider
    would be added to this module and nowhere else.

Degradation
    Market data is not a hard dependency. When every provider fails this
    module returns no facts and the report renders without the valuation
    table. Nothing in the public interface raises.

Public interface
    fetch_market_data(company) -> list[Fact]
    fetch_quote(ticker) -> MarketQuote | None
    clear_cache() -> None
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import (
    MARKET_ACCESSION_TEMPLATE,
    MARKET_CACHE_TTL_SECONDS,
    MARKET_MAX_ATTEMPTS_PER_PROVIDER,
    MARKET_METRIC_PREFIXES,
    MARKET_PROVIDER_CHAIN,
    MARKET_REQUEST_TIMEOUT_SECONDS,
    MARKET_USER_AGENT_TEMPLATE,
    STOOQ_QUOTE_URL_TEMPLATE,
    YAHOO_CHART_URL_TEMPLATE,
    get_settings,
)
from app.models import Company, ExtractionMethod, Fact, SourceTier, SourceType

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MarketQuote:
    """One provider's answer, normalised.

    Every figure is optional because providers differ in what they return, and
    a missing figure stays missing. Nothing here is derived: if a provider does
    not report market capitalisation, this module does not multiply a price by
    a share count to invent one.
    """

    ticker: str
    provider: str
    as_of: dt.datetime
    #: Where the figures were read from. This is the fact's provenance.
    source_url: str

    currency: str | None = None
    price: float | None = None
    previous_close: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    market_cap: float | None = None
    volume: float | None = None

    @property
    def is_usable(self) -> bool:
        """A quote with no price is not a quote."""
        return self.price is not None


@dataclass(frozen=True, slots=True)
class _MetricSpec:
    """One market metric and how it is read off a quote."""

    metric: str
    label: str
    attribute: str
    #: "currency" renders with the quote's currency; "count" is a plain number.
    kind: str


#: Metrics emitted, in display order. Every one sits under a prefix in
#: `MARKET_METRIC_PREFIXES`; the guard at emit time enforces that rather than
#: trusting this table to stay correct.
_MARKET_METRICS: tuple[_MetricSpec, ...] = (
    _MetricSpec("market.price", "Share price", "price", "currency"),
    _MetricSpec(
        "market.previous_close", "Previous close", "previous_close", "currency"
    ),
    _MetricSpec("market.day_high", "Day high", "day_high", "currency"),
    _MetricSpec("market.day_low", "Day low", "day_low", "currency"),
    _MetricSpec(
        "market.fifty_two_week_high",
        "52-week high",
        "fifty_two_week_high",
        "currency",
    ),
    _MetricSpec(
        "market.fifty_two_week_low",
        "52-week low",
        "fifty_two_week_low",
        "currency",
    ),
    _MetricSpec(
        "market.market_cap", "Market capitalisation", "market_cap", "currency"
    ),
    _MetricSpec("market.volume", "Volume", "volume", "count"),
)


# --- Cache ------------------------------------------------------------------
# Market data is not immutable, so unlike filings it expires. Fifteen minutes
# is long enough that a report generated twice in one session shows the same
# figures, and short enough that "as of" still means something.


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    quote: MarketQuote
    stored_at: float


_cache: dict[str, _CacheEntry] = {}
_cache_lock = asyncio.Lock()


def clear_cache() -> None:
    """Empties the quote cache. Used by tests and on demand."""
    _cache.clear()


def _cached(ticker: str) -> MarketQuote | None:
    entry = _cache.get(ticker.upper())
    if entry is None:
        return None
    if (time.monotonic() - entry.stored_at) > MARKET_CACHE_TTL_SECONDS:
        return None
    return entry.quote


def _store(ticker: str, quote: MarketQuote) -> None:
    _cache[ticker.upper()] = _CacheEntry(quote=quote, stored_at=time.monotonic())


# --- Providers --------------------------------------------------------------


def _user_agent() -> str:
    """Identifies this client. Some providers refuse an anonymous one."""
    return MARKET_USER_AGENT_TEMPLATE.format(
        contact_email=get_settings().edgar_contact_email or "unknown"
    )


async def _fetch_yahoo(
    client: httpx.AsyncClient, ticker: str
) -> MarketQuote | None:
    """Reads a quote from the chart endpoint's metadata block."""
    url = YAHOO_CHART_URL_TEMPLATE.format(symbol=ticker.upper())
    response = await client.get(url)
    response.raise_for_status()

    payload: Any = response.json()
    if not isinstance(payload, dict):
        return None

    chart = payload.get("chart")
    results = chart.get("result") if isinstance(chart, dict) else None
    if not isinstance(results, list) or not results:
        return None

    first = results[0]
    meta = first.get("meta") if isinstance(first, dict) else None
    if not isinstance(meta, dict):
        return None

    return MarketQuote(
        ticker=ticker.upper(),
        provider="yahoo",
        as_of=dt.datetime.now(dt.UTC),
        source_url=url,
        currency=_string_or_none(meta.get("currency")),
        price=_float_or_none(meta.get("regularMarketPrice")),
        previous_close=_float_or_none(
            meta.get("previousClose") or meta.get("chartPreviousClose")
        ),
        day_high=_float_or_none(meta.get("regularMarketDayHigh")),
        day_low=_float_or_none(meta.get("regularMarketDayLow")),
        fifty_two_week_high=_float_or_none(meta.get("fiftyTwoWeekHigh")),
        fifty_two_week_low=_float_or_none(meta.get("fiftyTwoWeekLow")),
        market_cap=_float_or_none(meta.get("marketCap")),
        volume=_float_or_none(meta.get("regularMarketVolume")),
    )


async def _fetch_stooq(
    client: httpx.AsyncClient, ticker: str
) -> MarketQuote | None:
    """Reads a quote from a CSV endpoint. Price, range and volume only.

    Deliberately thin. This is the failover rung: its job is to keep the price
    line on the page when the first provider is down, not to match it field for
    field. Fields it does not carry stay None and render "Not disclosed".
    """
    url = STOOQ_QUOTE_URL_TEMPLATE.format(symbol=ticker.lower())
    response = await client.get(url)
    response.raise_for_status()

    rows = list(csv.DictReader(io.StringIO(response.text)))
    if not rows:
        return None

    row = rows[0]
    close = _float_or_none(row.get("Close"))
    if close is None:
        # Stooq answers "N/D" for an unknown symbol rather than returning 404.
        return None

    return MarketQuote(
        ticker=ticker.upper(),
        provider="stooq",
        as_of=dt.datetime.now(dt.UTC),
        source_url=url,
        price=close,
        day_high=_float_or_none(row.get("High")),
        day_low=_float_or_none(row.get("Low")),
        volume=_float_or_none(row.get("Volume")),
    )


#: One rung of the chain: given a client and a ticker, answer with a quote or
#: with nothing. Every provider added here must fail by returning None.
_Provider = Callable[[httpx.AsyncClient, str], Awaitable[MarketQuote | None]]

#: The chain. Names in `MARKET_PROVIDER_CHAIN` resolve through this map, so a
#: configured name with no implementation is logged rather than skipped in
#: silence.
_PROVIDERS: dict[str, _Provider] = {
    "yahoo": _fetch_yahoo,
    "stooq": _fetch_stooq,
}


async def fetch_quote(ticker: str) -> MarketQuote | None:
    """Returns a quote from the first provider that answers, or None.

    Serves from the cache while an entry is under its TTL. Never raises:
    failure at every rung is reported as None, which the caller renders as a
    report without a valuation table.
    """
    cleaned = ticker.strip()
    if not cleaned:
        return None

    async with _cache_lock:
        cached = _cached(cleaned)
        if cached is not None:
            logger.debug("Market cache hit", extra={"ticker": cleaned})
            return cached

    quote = await _run_chain(cleaned)
    if quote is not None:
        async with _cache_lock:
            _store(cleaned, quote)
    return quote


async def _run_chain(ticker: str) -> MarketQuote | None:
    """Tries each provider in order until one answers with a usable quote."""
    headers = {
        "User-Agent": _user_agent(),
        "Accept": "application/json, text/csv",
    }

    async with httpx.AsyncClient(
        headers=headers,
        timeout=MARKET_REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
    ) as client:
        for name in MARKET_PROVIDER_CHAIN:
            provider = _PROVIDERS.get(name)
            if provider is None:
                logger.warning(
                    "Configured market provider has no implementation",
                    extra={"provider": name},
                )
                continue

            quote = await _try_provider(client, provider, name, ticker)
            if quote is not None:
                return quote

    logger.warning(
        "No market provider answered; the report renders without market data",
        extra={"ticker": ticker, "providers": list(MARKET_PROVIDER_CHAIN)},
    )
    return None


async def _try_provider(
    client: httpx.AsyncClient,
    provider: _Provider,
    name: str,
    ticker: str,
) -> MarketQuote | None:
    """Runs one provider, retrying transport failures before failing over.

    A provider that answers with no quote is not retried — it answered, and it
    does not know this ticker. Only a failure to get an answer is retried.
    """
    for attempt in range(MARKET_MAX_ATTEMPTS_PER_PROVIDER):
        try:
            quote = await provider(client, ticker)
        except Exception as cause:  # noqa: BLE001 — failover is the whole point
            logger.warning(
                "Market provider failed",
                extra={
                    "provider": name,
                    "ticker": ticker,
                    "attempt": attempt + 1,
                    "error": str(cause),
                },
            )
            continue

        if quote is not None and quote.is_usable:
            logger.info(
                "Market quote resolved",
                extra={"provider": name, "ticker": ticker},
            )
            return quote

        logger.info(
            "Market provider returned no usable quote",
            extra={"provider": name, "ticker": ticker},
        )
        return None

    return None


# --- Fact construction ------------------------------------------------------


async def fetch_market_data(company: Company) -> list[Fact]:
    """Fetches Tier 3 market figures for a company.

    Returns an empty list rather than raising when the provider chain is
    unavailable — market data is optional and the report renders without it.

    Every returned fact is Tier 3, sourced `market_data`, and carries a metric
    belonging to section 5. A fact that would carry any other metric is dropped
    here rather than offered to the fact store.
    """
    if not company.ticker.strip():
        logger.info(
            "No ticker for this filer; market data skipped",
            extra={"cik": company.cik},
        )
        return []

    quote = await fetch_quote(company.ticker)
    if quote is None:
        return []

    accession = MARKET_ACCESSION_TEMPLATE.format(
        provider=quote.provider.upper(),
        as_of=quote.as_of.strftime("%Y%m%dT%H%M%SZ"),
    )
    currency = quote.currency or company.reporting_currency

    facts: list[Fact] = []
    for spec in _MARKET_METRICS:
        value = getattr(quote, spec.attribute, None)
        if value is None:
            # A figure the provider did not report is simply absent. It is
            # never estimated, and a Tier 3 gap needs no marker fact.
            continue

        if not _is_market_metric(spec.metric):
            logger.error(
                "Refusing to emit a non-market metric from the market module",
                extra={"metric": spec.metric, "ticker": quote.ticker},
            )
            continue

        facts.append(
            Fact(
                metric=spec.metric,
                label=spec.label,
                value=float(value),
                display_value=_format(float(value), spec.kind, currency),
                unit=currency if spec.kind == "currency" else "shares",
                period_start=None,
                period_end=quote.as_of.date(),
                fiscal_year=None,
                tier=SourceTier.MARKET,
                source_type=SourceType.MARKET_DATA,
                source_url=quote.source_url,
                accession_no=accession,
                filed_date=quote.as_of.date(),
                extraction_method=ExtractionMethod.MARKET_DATA,
                confidence=1.0,
            )
        )

    logger.info(
        "Market extraction complete",
        extra={
            "cik": company.cik,
            "ticker": company.ticker,
            "provider": quote.provider,
            "facts": len(facts),
        },
    )
    return facts


def _is_market_metric(metric: str) -> bool:
    """True when a metric belongs to a section this module may contribute to.

    This is the structural half of rule 4: section 3's prefixes are not in
    `MARKET_METRIC_PREFIXES`, so no fact built here can be addressed to the
    financial highlights, whatever a future caller asks for.
    """
    return any(metric.startswith(prefix) for prefix in MARKET_METRIC_PREFIXES)


def _format(value: float, kind: str, currency: str | None) -> str:
    """Renders a market figure. Never blank, never a bare float repr."""
    if kind == "count":
        return f"{value:,.0f}"
    rendered = f"{value:,.2f}" if abs(value) < 1000 else f"{value:,.0f}"
    return f"{rendered} {currency}" if currency else rendered


def _float_or_none(value: object) -> float | None:
    """Parses a provider field, or None when it is absent or not a number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
