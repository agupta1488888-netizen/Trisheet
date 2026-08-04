"""Tests for m13_sources — a reader's own link, read without letting it
become a filing.

The load-bearing test in this file is the last one. Everything else confirms
the module degrades quietly; that one confirms the separation the module
exists for, by proving the fact store refuses what this module produces even
when someone tries to push it through.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from pydantic import ValidationError

from app.models import SourceNote, SourceTier, SourceType
from app.modules import m06_factstore, m13_sources
from app.services import llm, webfetch

TICKER = "AAPL"
PAGE_URL = "https://example.com/investors"


def _stub_fetch(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    async def _fetch(url: str) -> webfetch.FetchedPage:
        return webfetch.FetchedPage(url=PAGE_URL, text=text)

    monkeypatch.setattr(m13_sources.webfetch, "fetch_page", _fetch)


def _stub_llm(monkeypatch: pytest.MonkeyPatch, answer: dict[str, Any]) -> None:
    async def _answer(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return answer

    monkeypatch.setattr(llm, "complete_json", _answer)


def _refuse_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail(*args: Any, **kwargs: Any) -> dict[str, Any]:
        message = "The model should not have been called."
        raise AssertionError(message)

    monkeypatch.setattr(llm, "complete_json", _fail)


async def test_reads_a_page_into_cited_notes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_fetch(monkeypatch, "We opened a plant in Ohio.")
    _stub_llm(
        monkeypatch,
        {
            "notes": [{"text": "The company opened a plant in Ohio."}],
            "not_found": False,
        },
    )

    result = await m13_sources.read_sources([PAGE_URL], TICKER)

    assert result.unreachable_urls == []
    assert result.model_failed_urls == []
    assert len(result.notes) == 1
    note = result.notes[0]
    assert note.text == "The company opened a plant in Ohio."
    assert str(note.source_url) == PAGE_URL
    assert note.tier == SourceTier.COMPANY
    assert note.source_type == SourceType.COMPANY_SITE
    # The reader supplied this; nothing verified it is the company's own site.
    assert note.is_user_supplied is True


async def test_cites_the_url_actually_reached_not_the_one_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A redirect means the page read is not the page requested, and the
    citation has to point at what was actually read."""

    async def _fetch(url: str) -> webfetch.FetchedPage:
        return webfetch.FetchedPage(url="https://example.com/final", text="Text.")

    monkeypatch.setattr(m13_sources.webfetch, "fetch_page", _fetch)
    _stub_llm(monkeypatch, {"notes": [{"text": "A statement."}], "not_found": False})

    result = await m13_sources.read_sources(["https://example.com/start"], TICKER)

    assert str(result.notes[0].source_url) == "https://example.com/final"


async def test_an_unreachable_page_yields_no_notes_and_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule 6: only EDGAR is a hard dependency. A dead link must not take the
    report down with it."""

    async def _fail(url: str) -> webfetch.FetchedPage:
        raise webfetch.WebfetchError("The page did not respond in time.")

    monkeypatch.setattr(m13_sources.webfetch, "fetch_page", _fail)
    _refuse_llm(monkeypatch)

    result = await m13_sources.read_sources([PAGE_URL], TICKER)

    assert result.notes == []
    # The whole point of this change: a page that could not be fetched is
    # distinguishable from one that was read and had nothing to say.
    assert result.unreachable_urls == [PAGE_URL]
    assert result.model_failed_urls == []


async def test_a_blocked_url_yields_no_notes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _blocked(url: str) -> webfetch.FetchedPage:
        raise webfetch.WebfetchBlockedError("Refused: not a public address.")

    monkeypatch.setattr(m13_sources.webfetch, "fetch_page", _blocked)
    _refuse_llm(monkeypatch)

    result = await m13_sources.read_sources(["http://169.254.169.254/"], TICKER)

    assert result.notes == []
    assert result.unreachable_urls == ["http://169.254.169.254/"]
    assert result.model_failed_urls == []


async def test_a_model_failure_is_told_apart_from_an_unreachable_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The distinction this whole change exists for. A model failure and a
    dead link produce the same empty notes list but are different findings —
    the page was read fine here, so this must land in `model_failed_urls`,
    never `unreachable_urls`. Diagnosing a real production incident without
    this distinction meant no way to tell "the fetch failed" from "the model
    call failed" from the pipeline feed alone."""
    _stub_fetch(monkeypatch, "Some text.")

    async def _fail(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise llm.LlmError("The model is unavailable.")

    monkeypatch.setattr(llm, "complete_json", _fail)

    result = await m13_sources.read_sources([PAGE_URL], TICKER)

    assert result.notes == []
    assert result.unreachable_urls == []
    assert result.model_failed_urls == [PAGE_URL]


async def test_a_page_with_nothing_on_it_yields_no_notes_and_no_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty answer is the correct answer for a cookie banner. The module
    must not manufacture a note to avoid returning nothing, and this is a
    third, distinct finding from both an unreachable page and a model
    failure — the model was reached and it genuinely had nothing to say."""
    _stub_fetch(monkeypatch, "Accept cookies to continue.")
    _stub_llm(monkeypatch, {"notes": [], "not_found": True})

    result = await m13_sources.read_sources([PAGE_URL], TICKER)

    assert result.notes == []
    assert result.unreachable_urls == []
    assert result.model_failed_urls == []


async def test_no_links_never_calls_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """The common case — no link supplied — costs neither a fetch nor a call."""

    async def _fail_fetch(url: str) -> webfetch.FetchedPage:
        message = "Nothing should have been fetched."
        raise AssertionError(message)

    monkeypatch.setattr(m13_sources.webfetch, "fetch_page", _fail_fetch)
    _refuse_llm(monkeypatch)

    result = await m13_sources.read_sources([], TICKER)

    assert result.notes == []
    assert result.unreachable_urls == []
    assert result.model_failed_urls == []


async def test_notes_per_page_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch, "A long page.")
    _stub_llm(
        monkeypatch,
        {
            "notes": [{"text": f"Statement {index}."} for index in range(50)],
            "not_found": False,
        },
    )

    result = await m13_sources.read_sources([PAGE_URL], TICKER)

    assert len(result.notes) == m13_sources.SOURCE_NOTES_MAX_PER_URL


async def test_more_links_than_the_cap_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request model caps this too, but a cap enforced only at the HTTP
    boundary is one an internal caller can walk straight past."""
    fetched: list[str] = []

    async def _fetch(url: str) -> webfetch.FetchedPage:
        fetched.append(url)
        return webfetch.FetchedPage(url=url, text="Text.")

    monkeypatch.setattr(m13_sources.webfetch, "fetch_page", _fetch)
    _stub_llm(monkeypatch, {"notes": [], "not_found": True})

    urls = [f"https://example.com/{index}" for index in range(10)]
    await m13_sources.read_sources(urls, TICKER)

    assert len(fetched) == m13_sources.SOURCE_LINKS_MAX


async def test_one_bad_link_does_not_stop_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fetch(url: str) -> webfetch.FetchedPage:
        if url.endswith("bad"):
            raise webfetch.WebfetchError("Refused.")
        return webfetch.FetchedPage(url=url, text="Text.")

    monkeypatch.setattr(m13_sources.webfetch, "fetch_page", _fetch)
    _stub_llm(monkeypatch, {"notes": [{"text": "A statement."}], "not_found": False})

    result = await m13_sources.read_sources(
        ["https://example.com/bad", "https://example.com/good"], TICKER
    )

    assert len(result.notes) == 1
    assert str(result.notes[0].source_url) == "https://example.com/good"
    assert result.unreachable_urls == ["https://example.com/bad"]
    assert result.model_failed_urls == []


# --- The separation ------------------------------------------------------


def _note() -> SourceNote:
    return SourceNote(
        text="The company opened a plant in Ohio.",
        source_url=PAGE_URL,
        source_type=SourceType.COMPANY_SITE,
        tier=SourceTier.COMPANY,
        fetched_at=dt.datetime(2024, 11, 1, tzinfo=dt.UTC),
    )


@pytest.mark.parametrize("tier", [SourceTier.FILING, SourceTier.MARKET])
def test_a_note_cannot_claim_a_filing_or_market_tier(tier: SourceTier) -> None:
    """Tier 1 means an accession number backs it and tier 3 means a market
    provider does. A pasted page is neither, so the shape refuses both."""
    with pytest.raises(ValidationError):
        SourceNote(
            text="Anything.",
            source_url=PAGE_URL,
            source_type=SourceType.COMPANY_SITE,
            tier=tier,
            fetched_at=dt.datetime(2024, 11, 1, tzinfo=dt.UTC),
        )


def test_a_note_cannot_be_blank() -> None:
    with pytest.raises(ValidationError):
        SourceNote(
            text="   ",
            source_url=PAGE_URL,
            source_type=SourceType.COMPANY_SITE,
            tier=SourceTier.COMPANY,
            fetched_at=dt.datetime(2024, 11, 1, tzinfo=dt.UTC),
        )


def test_the_fact_store_refuses_a_source_note() -> None:
    """The point of the whole module, asserted directly.

    `SOURCE_TYPE_TIERS` maps company_site to tier 2 and
    `SECTION_3_ALLOWED_TIERS` admits tier 2, so a fact built from a pasted
    page would land in the financial highlights table beside figures traced
    to an accession number. A `SourceNote` carries no accession number and no
    filed date, so the gate cannot coerce one into a `Fact` — this asserts
    the gate actually rejects it rather than inventing the missing fields.
    """
    accepted, rejections = m06_factstore.admit_facts(
        "11111111-1111-1111-1111-111111111111", [_note().model_dump()]
    )

    assert accepted == []
    assert len(rejections) == 1


def test_m13_does_not_import_fact() -> None:
    """The separation is structural, not a rule to remember.

    If `Fact` is ever imported here, the module has gained the ability to put
    a pasted page's number into the financial highlights table, and this test
    is the thing that says so out loud.
    """
    assert not hasattr(m13_sources, "Fact")
