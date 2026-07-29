"""m05 — market data. THE ONLY MODULE PERMITTED TO IMPORT A MARKET PROVIDER.

Responsibility
    Fetch price, market capitalisation and trading multiples. Everything it
    emits is Tier 3 and is therefore barred from section 3 by m06 and m11.

Constraint
    If any other file in this repository imports yfinance or a market API
    client, that is a bug. This module is the physical expression of the
    source-tier rule — it is the single choke point through which Tier 3 data
    can enter the system.

Degradation
    Market data is not a hard dependency. When it is unavailable this module
    returns no facts and the report renders without the valuation table.

Public interface
    fetch_market_data(company) -> list[Fact]
"""

from __future__ import annotations

from app.models import Company, Fact


async def fetch_market_data(company: Company) -> list[Fact]:
    """Fetches Tier 3 market figures. Implemented in phase 1.

    Returns an empty list rather than raising when the provider is unavailable.
    """
    raise NotImplementedError
