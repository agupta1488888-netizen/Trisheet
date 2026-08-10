"""m05 — deriving a market capitalisation from a price and a share count.

Neither endpoint in `MARKET_PROVIDER_CHAIN` returns a capitalisation: Yahoo's
chart metadata carries no `marketCap` key and Stooq's quote CSV has no such
column. So `market.market_cap` is only ever populated by derivation, and every
multiple built on it — price to earnings, enterprise value to EBITDA, and the
whole valuation table — is unavailable when that derivation fails.

The invariant these cover: a derived capitalisation is tier 3 however sound its
share count, is dated at the quote rather than at the balance sheet, and is
never produced from a share count that was estimated, missing or absent.
"""

from __future__ import annotations

import datetime as dt

from app.config import (
    MARKET_CAP_LABEL,
    MARKET_CAP_METRIC,
    MARKET_METRIC_PREFIXES,
    SECTION_METRIC_PREFIXES,
)
from app.models import ExtractionMethod, Fact, SourceTier, SourceType
from app.modules import m05_market
from tests.conftest import make_fact

QUOTE_ACCESSION = "MARKET-YAHOO-20260805T084611Z"
QUOTE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/AAPL"
AS_OF = dt.date(2026, 8, 5)


def price_fact(*, value: float = 309.38, unit: str = "USD") -> Fact:
    """A quote, shaped exactly as `fetch_market_data` emits one."""
    return make_fact(
        metric="market.price",
        label="Share price",
        value=value,
        display_value=f"{value:,.2f} {unit}",
        unit=unit,
        period_start=None,
        period_end=AS_OF,
        fiscal_year=None,
        tier=SourceTier.MARKET,
        source_type=SourceType.MARKET_DATA,
        source_url=QUOTE_URL,
        accession_no=QUOTE_ACCESSION,
        filed_date=AS_OF,
        extraction_method=ExtractionMethod.MARKET_DATA,
        confidence=1.0,
        resolved_tag=None,
        taxonomy=None,
    )


def share_fact(
    *,
    metric: str = "balance.shares_outstanding_cover",
    value: float = 15_000_000_000.0,
    period_end: dt.date = dt.date(2025, 10, 17),
    confidence: float = 0.9,
) -> Fact:
    return make_fact(
        metric=metric,
        label="Shares outstanding, cover page",
        value=value,
        display_value=f"{value:,.0f}",
        unit="shares",
        period_start=None,
        period_end=period_end,
        fiscal_year=2025,
        confidence=confidence,
    )


def test_it_multiplies_the_price_by_the_share_count() -> None:
    derived = m05_market.derive_market_cap([price_fact(), share_fact()])

    assert derived is not None
    assert derived.metric == MARKET_CAP_METRIC
    assert derived.label == MARKET_CAP_LABEL
    assert derived.value == 309.38 * 15_000_000_000.0


def test_it_prefers_the_cover_page_count_over_the_balance_sheet_one() -> None:
    """The cover is dated at filing, the balance sheet at period end.

    Multiplying today's price by the older count would understate a filer that
    has been buying back stock since its year end, which is most of them.
    """
    cover = share_fact(value=15_000_000_000.0)
    balance = share_fact(
        metric="balance.shares_outstanding",
        value=15_400_000_000.0,
        period_end=dt.date(2025, 9, 27),
    )

    derived = m05_market.derive_market_cap([price_fact(), balance, cover])

    assert derived is not None
    assert derived.value == 309.38 * 15_000_000_000.0


def test_it_falls_back_to_the_balance_sheet_count() -> None:
    balance = share_fact(
        metric="balance.shares_outstanding", value=15_400_000_000.0
    )

    derived = m05_market.derive_market_cap([price_fact(), balance])

    assert derived is not None
    assert derived.value == 309.38 * 15_400_000_000.0


def test_it_defers_to_a_capitalisation_a_provider_reported() -> None:
    """A figure read from a source beats one assembled from two."""
    reported = make_fact(
        metric=MARKET_CAP_METRIC,
        label=MARKET_CAP_LABEL,
        value=4_600_000_000_000.0,
        display_value="4,600,000,000,000 USD",
        unit="USD",
        period_start=None,
        period_end=AS_OF,
        fiscal_year=None,
        tier=SourceTier.MARKET,
        source_type=SourceType.MARKET_DATA,
        source_url=QUOTE_URL,
        accession_no=QUOTE_ACCESSION,
        filed_date=AS_OF,
        extraction_method=ExtractionMethod.MARKET_DATA,
        resolved_tag=None,
        taxonomy=None,
    )

    assert (
        m05_market.derive_market_cap([price_fact(), share_fact(), reported])
        is None
    )


def test_it_refuses_without_a_price() -> None:
    assert m05_market.derive_market_cap([share_fact()]) is None


def test_it_refuses_without_a_share_count() -> None:
    assert m05_market.derive_market_cap([price_fact()]) is None


def test_it_refuses_a_share_count_that_is_not_positive() -> None:
    """A zero count would render a capitalisation of nothing as a real figure."""
    assert (
        m05_market.derive_market_cap([price_fact(), share_fact(value=0.0)])
        is None
    )


def test_it_refuses_when_the_share_count_is_not_disclosed() -> None:
    absent = make_fact(
        metric="balance.shares_outstanding",
        label="Shares outstanding",
        value=None,
        display_value="Not disclosed",
        unit=None,
        extraction_method=ExtractionMethod.NOT_DISCLOSED,
        resolved_tag=None,
    )

    assert m05_market.derive_market_cap([price_fact(), absent]) is None


def test_it_claims_the_tier_of_its_least_trustworthy_input() -> None:
    """A filed share count does not launder a quote into a filed figure."""
    derived = m05_market.derive_market_cap([price_fact(), share_fact()])

    assert derived is not None
    assert derived.tier is SourceTier.MARKET
    assert derived.confidence == 0.9


def test_it_is_dated_and_sourced_at_the_quote() -> None:
    """A capitalisation is "as of" a price, not as of a balance sheet."""
    derived = m05_market.derive_market_cap([price_fact(), share_fact()])

    assert derived is not None
    assert derived.period_end == AS_OF
    assert derived.filed_date == AS_OF
    assert derived.accession_no == QUOTE_ACCESSION
    assert derived.source_type is SourceType.MARKET_DATA
    # No fiscal year: it belongs to the day it was priced, not to a reporting
    # period, so it never lands in a column of figures read off a filing.
    assert derived.fiscal_year is None


def test_it_carries_the_formula_that_produced_it() -> None:
    derived = m05_market.derive_market_cap([price_fact(), share_fact()])

    assert derived is not None
    assert derived.is_calculated
    assert derived.extraction_method is ExtractionMethod.CALCULATED
    assert derived.formula is not None
    # The share count's own date travels in the formula, because the price and
    # the count are measured on different days and the reader is owed both.
    assert "2025-10-17" in derived.formula


def test_the_metric_cannot_address_the_financial_highlights() -> None:
    """Wall 1 of the tier rule, asserted rather than assumed.

    A derived capitalisation is a real stored fact that counts toward
    compliance, so it has to be barred from section 3 structurally, exactly as
    a fetched one is.
    """
    assert any(
        MARKET_CAP_METRIC.startswith(prefix) for prefix in MARKET_METRIC_PREFIXES
    )
    assert not any(
        MARKET_CAP_METRIC.startswith(prefix)
        for prefix in SECTION_METRIC_PREFIXES[3]
    )
