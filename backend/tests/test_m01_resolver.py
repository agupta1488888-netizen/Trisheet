"""Tests for m01: ticker resolution, ambiguity, filer type and currency."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.models import FilerType, ResolutionOutcome
from app.modules.m01_resolver import (
    ResolutionError,
    normalise_name,
    resolve,
)
from app.services.edgar import EdgarClient, ResponseCache, TokenBucket

USER_AGENT = "Trisheet tests@example.com"

TICKER_INDEX = {
    "0": {"cik_str": 320187, "ticker": "NKE", "title": "NIKE, Inc."},
    "1": {"cik_str": 1046179, "ticker": "TSM", "title": "TAIWAN SEMICONDUCTOR"},
    "2": {"cik_str": 1594805, "ticker": "SHOP", "title": "Shopify Inc."},
    "3": {"cik_str": 111111, "ticker": "ACME", "title": "Acme Industries Inc."},
    "4": {"cik_str": 222222, "ticker": "ACMH", "title": "Acme Holdings Corp"},
    "5": {"cik_str": 333333, "ticker": "BRK-A", "title": "Berkshire Hathaway Inc"},
    "6": {"cik_str": 333333, "ticker": "BRK-B", "title": "Berkshire Hathaway Inc"},
    # Two unrelated filers whose names normalise identically.
    "7": {"cik_str": 444444, "ticker": "ZEBA", "title": "Zebra Systems Inc."},
    "8": {"cik_str": 555555, "ticker": "ZEBB", "title": "Zebra Systems Corp"},
}


def _submissions(
    *,
    cik: int,
    name: str,
    tickers: list[str],
    forms: list[str],
    dates: list[str],
    sic: str = "3021",
    sic_description: str = "Rubber & Plastics Footwear",
    fiscal_year_end: str = "0531",
    files: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "cik": cik,
        "name": name,
        "tickers": tickers,
        "sic": sic,
        "sicDescription": sic_description,
        "fiscalYearEnd": fiscal_year_end,
        "filings": {
            "recent": {"form": forms, "filingDate": dates},
            "files": files or [],
        },
    }


class FakeEdgar:
    """Serves canned EDGAR documents and records what was requested."""

    def __init__(self) -> None:
        self.routes: dict[str, httpx.Response] = {}
        self.requested: list[str] = []

    def json_route(self, url_fragment: str, payload: dict[str, Any]) -> None:
        self.routes[url_fragment] = httpx.Response(
            200, content=json.dumps(payload).encode()
        )

    def status_route(self, url_fragment: str, status: int) -> None:
        self.routes[url_fragment] = httpx.Response(status)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requested.append(url)
        for fragment, response in self.routes.items():
            if fragment in url:
                return httpx.Response(
                    response.status_code, content=response.content
                )
        return httpx.Response(404)


def _client(fake: FakeEdgar, tmp_path: Path) -> EdgarClient:
    return EdgarClient(
        user_agent=USER_AGENT,
        cache=ResponseCache(tmp_path),
        limiter=TokenBucket(1_000),
        transport=httpx.MockTransport(fake.handler),
    )


def _base_fake() -> FakeEdgar:
    fake = FakeEdgar()
    fake.json_route("company_tickers.json", TICKER_INDEX)
    return fake


# --- Name normalisation -----------------------------------------------------


def test_normalise_name_drops_punctuation_and_suffixes() -> None:
    assert normalise_name("NIKE, Inc.") == "nike"
    assert normalise_name("Acme Holdings Ltd") == "acme"
    assert normalise_name("Shopify Inc.") == "shopify"


def test_normalise_name_keeps_a_single_word_that_is_a_suffix() -> None:
    # "Group" alone is the whole name; stripping it would leave nothing.
    assert normalise_name("Group") == "group"


# --- Ticker resolution ------------------------------------------------------


async def test_resolves_a_ticker_to_a_domestic_filer(tmp_path: Path) -> None:
    fake = _base_fake()
    fake.json_route(
        "CIK0000320187.json",
        _submissions(
            cik=320187,
            name="NIKE, Inc.",
            tickers=["NKE"],
            forms=["10-K", "10-Q", "8-K"],
            dates=["2025-07-24", "2025-04-03", "2025-03-20"],
        ),
    )
    fake.json_route(
        "us-gaap/Assets.json", {"units": {"USD": [{"val": 1}, {"val": 2}]}}
    )

    async with _client(fake, tmp_path) as client:
        resolution = await resolve("nke", client)

    assert resolution.outcome is ResolutionOutcome.RESOLVED
    assert resolution.company is not None
    assert resolution.company.cik == "0000320187"
    assert resolution.company.ticker == "NKE"
    assert resolution.company.filer_type is FilerType.DOMESTIC
    assert resolution.company.reporting_currency == "USD"
    assert resolution.company.fiscal_year_end == "0531"
    assert resolution.company.sic_code == "3021"


async def test_resolves_a_company_name(tmp_path: Path) -> None:
    fake = _base_fake()
    fake.json_route(
        "CIK0001594805.json",
        _submissions(
            cik=1594805,
            name="Shopify Inc.",
            tickers=["SHOP"],
            forms=["40-F"],
            dates=["2025-02-11"],
        ),
    )
    fake.json_route("ifrs-full/Assets.json", {"units": {"USD": [{"val": 1}]}})

    async with _client(fake, tmp_path) as client:
        resolution = await resolve("Shopify", client)

    assert resolution.outcome is ResolutionOutcome.RESOLVED
    assert resolution.company is not None
    assert resolution.company.cik == "0001594805"


async def test_unknown_input_is_not_found(tmp_path: Path) -> None:
    fake = _base_fake()

    async with _client(fake, tmp_path) as client:
        resolution = await resolve("zzzznotacompany", client)

    assert resolution.outcome is ResolutionOutcome.NOT_FOUND
    assert resolution.company is None
    assert resolution.candidates == ()


async def test_blank_input_is_refused(tmp_path: Path) -> None:
    fake = _base_fake()

    async with _client(fake, tmp_path) as client:
        with pytest.raises(ResolutionError, match="ticker or a company name"):
            await resolve("   ", client)


# --- Ambiguity --------------------------------------------------------------


async def test_ambiguous_name_returns_candidates_and_picks_nothing(
    tmp_path: Path,
) -> None:
    fake = _base_fake()

    async with _client(fake, tmp_path) as client:
        resolution = await resolve("Zebra Systems", client)

    assert resolution.outcome is ResolutionOutcome.AMBIGUOUS
    assert resolution.company is None
    assert {candidate.ticker for candidate in resolution.candidates} == {
        "ZEBA",
        "ZEBB",
    }
    # No submissions document was fetched: nothing was resolved.
    assert not any("submissions" in url for url in fake.requested)


async def test_an_exact_ticker_beats_a_name_that_merely_contains_it(
    tmp_path: Path,
) -> None:
    """"ACME" is a ticker and also the start of another filer's name.

    Reading it as the ticker is the more specific interpretation, so it wins
    rather than being reported as ambiguous.
    """
    fake = _base_fake()
    fake.json_route(
        "CIK0000111111.json",
        _submissions(
            cik=111111,
            name="Acme Industries Inc.",
            tickers=["ACME"],
            forms=["10-K"],
            dates=["2025-02-20"],
        ),
    )
    fake.json_route("us-gaap/Assets.json", {"units": {"USD": [{"val": 1}]}})

    async with _client(fake, tmp_path) as client:
        resolution = await resolve("ACME", client)

    assert resolution.outcome is ResolutionOutcome.RESOLVED
    assert resolution.company is not None
    assert resolution.company.cik == "0000111111"


async def test_share_classes_of_one_filer_are_not_ambiguous(
    tmp_path: Path,
) -> None:
    """BRK-A and BRK-B are one entity, so this resolves rather than asking."""
    fake = _base_fake()
    fake.json_route(
        "CIK0000333333.json",
        _submissions(
            cik=333333,
            name="Berkshire Hathaway Inc",
            tickers=["BRK-A", "BRK-B"],
            forms=["10-K"],
            dates=["2025-02-24"],
        ),
    )
    fake.json_route("us-gaap/Assets.json", {"units": {"USD": [{"val": 1}]}})

    async with _client(fake, tmp_path) as client:
        resolution = await resolve("Berkshire Hathaway", client)

    assert resolution.outcome is ResolutionOutcome.RESOLVED
    assert resolution.company is not None
    assert resolution.company.cik == "0000333333"


# --- Filer type -------------------------------------------------------------


async def test_20f_filer_is_foreign(tmp_path: Path) -> None:
    fake = _base_fake()
    fake.json_route(
        "CIK0001046179.json",
        _submissions(
            cik=1046179,
            name="TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD",
            tickers=["TSM"],
            forms=["20-F", "6-K"],
            dates=["2025-04-16", "2025-07-17"],
            sic="3674",
            sic_description="Semiconductors & Related Devices",
        ),
    )
    fake.status_route("us-gaap/Assets.json", 404)
    fake.json_route("ifrs-full/Assets.json", {"units": {"TWD": [{"val": 1}]}})

    async with _client(fake, tmp_path) as client:
        resolution = await resolve("TSM", client)

    assert resolution.company is not None
    assert resolution.company.filer_type is FilerType.FOREIGN
    assert resolution.company.reporting_currency == "TWD"


async def test_40f_filer_is_canadian(tmp_path: Path) -> None:
    fake = _base_fake()
    fake.json_route(
        "CIK0001594805.json",
        _submissions(
            cik=1594805,
            name="Shopify Inc.",
            tickers=["SHOP"],
            forms=["40-F", "6-K"],
            dates=["2025-02-11", "2025-05-08"],
        ),
    )
    fake.status_route("us-gaap/Assets.json", 404)
    fake.json_route("ifrs-full/Assets.json", {"units": {"USD": [{"val": 1}]}})

    async with _client(fake, tmp_path) as client:
        resolution = await resolve("SHOP", client)

    assert resolution.company is not None
    assert resolution.company.filer_type is FilerType.CANADIAN
    # A Canadian filer reporting in USD: filer type and currency are separate.
    assert resolution.company.reporting_currency == "USD"


async def test_migrated_filer_is_classified_by_its_latest_annual_form(
    tmp_path: Path,
) -> None:
    """A company that moved from 20-F to 10-K files 10-K now."""
    fake = _base_fake()
    fake.json_route(
        "CIK0000111111.json",
        _submissions(
            cik=111111,
            name="Acme Industries Inc.",
            tickers=["ACME"],
            forms=["20-F", "10-K"],
            dates=["2021-03-01", "2025-02-20"],
        ),
    )
    fake.json_route("us-gaap/Assets.json", {"units": {"USD": [{"val": 1}]}})

    async with _client(fake, tmp_path) as client:
        resolution = await resolve("ACME", client)

    assert resolution.company is not None
    assert resolution.company.filer_type is FilerType.DOMESTIC


async def test_annual_form_is_looked_up_in_shards_when_recent_has_none(
    tmp_path: Path,
) -> None:
    fake = _base_fake()
    fake.json_route(
        "CIK0000111111.json",
        _submissions(
            cik=111111,
            name="Acme Industries Inc.",
            tickers=["ACME"],
            forms=["8-K", "10-Q"],
            dates=["2025-06-01", "2025-05-01"],
            files=[{"name": "CIK0000111111-submissions-001.json"}],
        ),
    )
    fake.json_route(
        "CIK0000111111-submissions-001.json",
        {"form": ["20-F"], "filingDate": ["2019-04-01"]},
    )
    fake.status_route("us-gaap/Assets.json", 404)
    fake.json_route("ifrs-full/Assets.json", {"units": {"EUR": [{"val": 1}]}})

    async with _client(fake, tmp_path) as client:
        resolution = await resolve("ACME", client)

    assert resolution.company is not None
    assert resolution.company.filer_type is FilerType.FOREIGN
    assert resolution.company.reporting_currency == "EUR"


async def test_filer_with_no_annual_report_is_refused_not_guessed(
    tmp_path: Path,
) -> None:
    fake = _base_fake()
    fake.json_route(
        "CIK0000111111.json",
        _submissions(
            cik=111111,
            name="Acme Industries Inc.",
            tickers=["ACME"],
            forms=["8-K", "10-Q"],
            dates=["2025-06-01", "2025-05-01"],
        ),
    )

    async with _client(fake, tmp_path) as client:
        with pytest.raises(ResolutionError, match="No annual filing found"):
            await resolve("ACME", client)


# --- Reporting currency -----------------------------------------------------


async def test_convenience_translation_does_not_win_over_the_real_currency(
    tmp_path: Path,
) -> None:
    """The code backing the most facts is the one the filer reports in."""
    fake = _base_fake()
    fake.json_route(
        "CIK0001046179.json",
        _submissions(
            cik=1046179,
            name="TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD",
            tickers=["TSM"],
            forms=["20-F"],
            dates=["2025-04-16"],
        ),
    )
    fake.status_route("us-gaap/Assets.json", 404)
    fake.json_route(
        "ifrs-full/Assets.json",
        {
            "units": {
                "USD": [{"val": 1}],
                "TWD": [{"val": 1}, {"val": 2}, {"val": 3}],
                "shares": [{"val": 9}] * 99,
            }
        },
    )

    async with _client(fake, tmp_path) as client:
        resolution = await resolve("TSM", client)

    assert resolution.company is not None
    assert resolution.company.reporting_currency == "TWD"


async def test_undetermined_currency_is_none_not_usd(tmp_path: Path) -> None:
    fake = _base_fake()
    fake.json_route(
        "CIK0000320187.json",
        _submissions(
            cik=320187,
            name="NIKE, Inc.",
            tickers=["NKE"],
            forms=["10-K"],
            dates=["2025-07-24"],
        ),
    )
    fake.status_route("us-gaap/Assets.json", 404)
    fake.status_route("ifrs-full/Assets.json", 404)

    async with _client(fake, tmp_path) as client:
        resolution = await resolve("NKE", client)

    assert resolution.company is not None
    assert resolution.company.reporting_currency is None
