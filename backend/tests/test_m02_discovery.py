"""Tests for m02: form filtering, amendment precedence and 8-K exhibits."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.models import Company, FilerType
from app.modules.m02_discovery import (
    DiscoveryError,
    apply_amendment_precedence,
    as_filings,
    build_manifest,
    parse_exhibits,
    superseded_filings,
)
from app.services.edgar import EdgarClient, ResponseCache, TokenBucket

USER_AGENT = "Trisheet tests@example.com"
CIK = "0000320187"

COMPANY = Company(
    cik=CIK,
    ticker="NKE",
    name="NIKE, Inc.",
    filer_type=FilerType.DOMESTIC,
    reporting_currency="USD",
)

_ARCHIVE_DIR = "/Archives/edgar/data/320187/000032018725000012"

INDEX_HTML = f"""
<html><body>
<table summary="Document Format Files">
  <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>
  <tr>
    <td>1</td><td>8-K</td>
    <td><a href="/ix?doc={_ARCHIVE_DIR}/a8k.htm">a8k.htm</a></td>
    <td>8-K</td>
  </tr>
  <tr>
    <td>2</td><td>Press release dated June 26, 2025</td>
    <td><a href="{_ARCHIVE_DIR}/ex991.htm">ex991.htm</a></td>
    <td>EX-99.1</td>
  </tr>
  <tr>
    <td>3</td><td>Investor presentation</td>
    <td><a href="{_ARCHIVE_DIR}/ex992.htm">ex992.htm</a></td>
    <td>EX-99.2</td>
  </tr>
  <tr>
    <td>4</td><td>Inline XBRL</td>
    <td><a href="{_ARCHIVE_DIR}/x.xml">x.xml</a></td>
    <td>EX-101.INS</td>
  </tr>
</table>
</body></html>
"""


def _submissions(
    *,
    accessions: list[str],
    forms: list[str],
    filed: list[str],
    reported: list[str] | None = None,
    documents: list[str] | None = None,
    items: list[str] | None = None,
    files: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    count = len(accessions)
    return {
        "cik": int(CIK),
        "filings": {
            "recent": {
                "accessionNumber": accessions,
                "form": forms,
                "filingDate": filed,
                "reportDate": reported if reported is not None else [""] * count,
                "primaryDocument": (
                    documents if documents is not None else ["doc.htm"] * count
                ),
                "items": items if items is not None else [""] * count,
            },
            "files": files or [],
        },
    }


class FakeEdgar:
    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, bytes]] = {}
        self.requested: list[str] = []

    def json_route(self, fragment: str, payload: dict[str, Any]) -> None:
        self.routes[fragment] = (200, json.dumps(payload).encode())

    def text_route(self, fragment: str, body: str) -> None:
        self.routes[fragment] = (200, body.encode())

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requested.append(url)
        for fragment, (status, body) in self.routes.items():
            if fragment in url:
                return httpx.Response(status, content=body)
        return httpx.Response(404)


def _client(fake: FakeEdgar, tmp_path: Path) -> EdgarClient:
    return EdgarClient(
        user_agent=USER_AGENT,
        cache=ResponseCache(tmp_path),
        limiter=TokenBucket(1_000),
        transport=httpx.MockTransport(fake.handler),
    )


# --- Form filtering ---------------------------------------------------------


async def test_manifest_keeps_only_permitted_forms(tmp_path: Path) -> None:
    fake = FakeEdgar()
    fake.json_route(
        f"CIK{CIK}.json",
        _submissions(
            accessions=[
                "0000320187-25-000010",
                "0000320187-25-000011",
                "0000320187-25-000012",
                "0000320187-25-000013",
            ],
            forms=["10-K", "4", "S-8", "DEF 14A"],
            filed=["2025-07-24", "2025-07-20", "2025-07-18", "2025-07-15"],
        ),
    )

    async with _client(fake, tmp_path) as client:
        manifest = await build_manifest(COMPANY, client, include_exhibits=False)

    assert {ref.base_form for ref in manifest} == {"10-K", "DEF 14A"}


async def test_manifest_is_newest_first(tmp_path: Path) -> None:
    fake = FakeEdgar()
    fake.json_route(
        f"CIK{CIK}.json",
        _submissions(
            accessions=["0000320187-23-000010", "0000320187-25-000011"],
            forms=["10-K", "10-Q"],
            filed=["2023-07-20", "2025-04-03"],
        ),
    )

    async with _client(fake, tmp_path) as client:
        manifest = await build_manifest(COMPANY, client, include_exhibits=False)

    assert [ref.filed_date for ref in manifest] == [
        dt.date(2025, 4, 3),
        dt.date(2023, 7, 20),
    ]


async def test_a_filer_with_no_permitted_forms_is_refused(tmp_path: Path) -> None:
    fake = FakeEdgar()
    fake.json_route(
        f"CIK{CIK}.json",
        _submissions(
            accessions=["0000320187-25-000010"],
            forms=["4"],
            filed=["2025-07-24"],
        ),
    )

    async with _client(fake, tmp_path) as client:
        with pytest.raises(DiscoveryError, match="No filings found"):
            await build_manifest(COMPANY, client, include_exhibits=False)


async def test_filings_without_a_filing_date_are_dropped(tmp_path: Path) -> None:
    fake = FakeEdgar()
    fake.json_route(
        f"CIK{CIK}.json",
        _submissions(
            accessions=["0000320187-25-000010", "0000320187-25-000011"],
            forms=["10-K", "10-Q"],
            filed=["", "2025-04-03"],
        ),
    )

    async with _client(fake, tmp_path) as client:
        manifest = await build_manifest(COMPANY, client, include_exhibits=False)

    assert [ref.base_form for ref in manifest] == ["10-Q"]


async def test_malformed_accession_numbers_are_dropped(tmp_path: Path) -> None:
    fake = FakeEdgar()
    fake.json_route(
        f"CIK{CIK}.json",
        _submissions(
            accessions=["not-an-accession", "0000320187-25-000011"],
            forms=["10-K", "10-Q"],
            filed=["2025-07-24", "2025-04-03"],
        ),
    )

    async with _client(fake, tmp_path) as client:
        manifest = await build_manifest(COMPANY, client, include_exhibits=False)

    assert [ref.base_form for ref in manifest] == ["10-Q"]


# --- Amendment precedence ---------------------------------------------------


async def test_amendment_supersedes_the_original_for_the_same_period(
    tmp_path: Path,
) -> None:
    fake = FakeEdgar()
    fake.json_route(
        f"CIK{CIK}.json",
        _submissions(
            accessions=["0000320187-25-000010", "0000320187-25-000020"],
            forms=["10-K", "10-K/A"],
            filed=["2025-07-24", "2025-09-30"],
            reported=["2025-05-31", "2025-05-31"],
        ),
    )

    async with _client(fake, tmp_path) as client:
        manifest = await build_manifest(COMPANY, client, include_exhibits=False)

    assert len(manifest) == 1
    assert manifest[0].form == "10-K/A"
    assert manifest[0].is_amendment is True
    assert manifest[0].base_form == "10-K"


async def test_the_later_of_two_amendments_wins() -> None:
    refs = _refs(
        [
            ("0000320187-25-000010", "10-K", "2025-07-24", "2025-05-31"),
            ("0000320187-25-000020", "10-K/A", "2025-08-30", "2025-05-31"),
            ("0000320187-25-000030", "10-K/A", "2025-11-15", "2025-05-31"),
        ]
    )

    kept = apply_amendment_precedence(refs)

    assert len(kept) == 1
    assert kept[0].accession_no == "0000320187-25-000030"


async def test_an_original_never_beats_an_amendment_by_being_later() -> None:
    """Filing order does not override amendment status for the same period."""
    refs = _refs(
        [
            ("0000320187-25-000020", "10-K/A", "2025-08-30", "2025-05-31"),
            ("0000320187-25-000030", "10-K", "2025-09-30", "2025-05-31"),
        ]
    )

    kept = apply_amendment_precedence(refs)

    assert len(kept) == 1
    assert kept[0].form == "10-K/A"


async def test_different_periods_are_not_collapsed() -> None:
    refs = _refs(
        [
            ("0000320187-25-000010", "10-K", "2025-07-24", "2025-05-31"),
            ("0000320187-24-000010", "10-K", "2024-07-25", "2024-05-31"),
        ]
    )

    assert len(apply_amendment_precedence(refs)) == 2


async def test_current_reports_on_different_dates_are_not_collapsed() -> None:
    """8-Ks carry no period, so the filing date must keep them distinct."""
    refs = _refs(
        [
            ("0000320187-25-000010", "8-K", "2025-06-26", ""),
            ("0000320187-25-000011", "8-K", "2025-03-20", ""),
        ]
    )

    assert len(apply_amendment_precedence(refs)) == 2


async def test_superseded_filings_are_reportable(tmp_path: Path) -> None:
    fake = FakeEdgar()
    fake.json_route(
        f"CIK{CIK}.json",
        _submissions(
            accessions=["0000320187-25-000010", "0000320187-25-000020"],
            forms=["10-K", "10-K/A"],
            filed=["2025-07-24", "2025-09-30"],
            reported=["2025-05-31", "2025-05-31"],
        ),
    )

    async with _client(fake, tmp_path) as client:
        discarded = await superseded_filings(COMPANY, client)

    assert [ref.form for ref in discarded] == ["10-K"]


# --- Shards -----------------------------------------------------------------


async def test_older_filings_are_read_from_shards(tmp_path: Path) -> None:
    fake = FakeEdgar()
    fake.json_route(
        f"CIK{CIK}.json",
        _submissions(
            accessions=["0000320187-25-000011"],
            forms=["10-Q"],
            filed=["2025-04-03"],
            files=[{"name": f"CIK{CIK}-submissions-001.json"}],
        ),
    )
    fake.json_route(
        f"CIK{CIK}-submissions-001.json",
        {
            "accessionNumber": ["0000320187-15-000010"],
            "form": ["10-K"],
            "filingDate": ["2015-07-21"],
            "reportDate": ["2015-05-31"],
            "primaryDocument": ["nke10k.htm"],
            "items": [""],
        },
    )

    async with _client(fake, tmp_path) as client:
        manifest = await build_manifest(COMPANY, client, include_exhibits=False)

    assert {ref.base_form for ref in manifest} == {"10-Q", "10-K"}


async def test_an_unreadable_shard_does_not_lose_the_manifest(
    tmp_path: Path,
) -> None:
    fake = FakeEdgar()
    fake.json_route(
        f"CIK{CIK}.json",
        _submissions(
            accessions=["0000320187-25-000011"],
            forms=["10-Q"],
            filed=["2025-04-03"],
            files=[{"name": f"CIK{CIK}-submissions-999.json"}],
        ),
    )
    # The shard route is absent, so it answers 404.

    async with _client(fake, tmp_path) as client:
        manifest = await build_manifest(COMPANY, client, include_exhibits=False)

    assert [ref.base_form for ref in manifest] == ["10-Q"]


# --- Exhibits ---------------------------------------------------------------


def test_parse_exhibits_reads_the_type_column() -> None:
    exhibits = parse_exhibits(INDEX_HTML)

    assert [exhibit.exhibit_type for exhibit in exhibits] == ["EX-99.1", "EX-99.2"]
    assert exhibits[0].description == "Press release dated June 26, 2025"
    assert str(exhibits[0].url).endswith("/ex991.htm")
    assert str(exhibits[0].url).startswith("https://www.sec.gov/Archives/")


def test_parse_exhibits_strips_the_inline_xbrl_viewer_prefix() -> None:
    page = """
    <table><tr>
      <td>2</td><td>Press release</td>
      <td><a href="/ix?doc=/Archives/edgar/data/1/2/ex991.htm">ex991.htm</a></td>
      <td>EX-99.1</td>
    </tr></table>
    """

    exhibits = parse_exhibits(page)

    assert (
        str(exhibits[0].url)
        == "https://www.sec.gov/Archives/edgar/data/1/2/ex991.htm"
    )


def test_parse_exhibits_ignores_pages_with_none() -> None:
    assert parse_exhibits("<html><body><p>nothing here</p></body></html>") == ()


async def test_exhibits_are_attached_to_current_reports(tmp_path: Path) -> None:
    fake = FakeEdgar()
    fake.json_route(
        f"CIK{CIK}.json",
        _submissions(
            accessions=["0000320187-25-000012", "0000320187-25-000010"],
            forms=["8-K", "10-K"],
            filed=["2025-06-26", "2025-07-24"],
            reported=["2025-06-26", "2025-05-31"],
            items=["2.02,9.01", ""],
        ),
    )
    fake.text_route("000032018725000012", INDEX_HTML)

    async with _client(fake, tmp_path) as client:
        manifest = await build_manifest(COMPANY, client, include_exhibits=True)

    current = next(ref for ref in manifest if ref.base_form == "8-K")
    annual = next(ref for ref in manifest if ref.base_form == "10-K")

    assert [exhibit.exhibit_type for exhibit in current.exhibits] == [
        "EX-99.1",
        "EX-99.2",
    ]
    assert current.items == ("2.02", "9.01")
    # Periodic reports are not expanded; only current reports carry exhibits.
    assert annual.exhibits == ()


async def test_an_unreadable_index_does_not_lose_the_filing(
    tmp_path: Path,
) -> None:
    fake = FakeEdgar()
    fake.json_route(
        f"CIK{CIK}.json",
        _submissions(
            accessions=["0000320187-25-000012"],
            forms=["8-K"],
            filed=["2025-06-26"],
        ),
    )
    # No index route: the request 404s.

    async with _client(fake, tmp_path) as client:
        manifest = await build_manifest(COMPANY, client, include_exhibits=True)

    assert len(manifest) == 1
    assert manifest[0].exhibits == ()


# --- Handover to the extraction modules -------------------------------------


def test_as_filings_preserves_identity_and_addressing() -> None:
    refs = _refs(
        [
            ("0000320187-25-000020", "10-K/A", "2025-09-30", "2025-05-31"),
            ("0000320187-25-000012", "8-K", "2025-06-26", ""),
        ]
    )

    filings = as_filings(refs)

    assert [filing.accession_no for filing in filings] == [
        "0000320187-25-000020",
        "0000320187-25-000012",
    ]
    # The amendment suffix survives: m03 dedupes on form and needs to see it.
    assert filings[0].form == "10-K/A"
    assert filings[0].period_of_report == dt.date(2025, 5, 31)
    assert filings[1].period_of_report is None
    assert str(filings[0].primary_doc_url).endswith("/doc.htm")


# --- Helpers ----------------------------------------------------------------


def _refs(rows: list[tuple[str, str, str, str]]) -> list[Any]:
    """Builds FilingRefs directly, for precedence tests that need no network."""
    from app.modules.m02_discovery import _rows_from_block

    return _rows_from_block(
        CIK,
        {
            "accessionNumber": [row[0] for row in rows],
            "form": [row[1] for row in rows],
            "filingDate": [row[2] for row in rows],
            "reportDate": [row[3] for row in rows],
            "primaryDocument": ["doc.htm"] * len(rows),
            "items": [""] * len(rows),
        },
    )
