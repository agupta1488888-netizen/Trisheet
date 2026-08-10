"""Tests for the live filing feed.

Covers the two firehose formats, the enrichment that turns a bare filing into a
quoted one, and one whole cycle of the poller against stubbed EDGAR and store.

The claim these tests defend is narrower than "the feed works": it is that
nothing in the feed is invented. A headline is EDGAR's own item label or the
form's own name, a quoted sentence is the filer's, and a filing whose items say
nothing reportable is dropped rather than described.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.feed import enrich as enrichment
from app.feed import poller, sources, store, universe
from app.models import FeedItem
from app.services import edgar
from tests.conftest import StubEdgarClient

APPLE_CIK = "0000320193"
APPLE_ACCESSION = "0000320193-26-000010"
UNTRACKED_CIK = "0000999999"


# --- Fixtures shaped like EDGAR's own responses -------------------------------


def atom_entry(
    *,
    title: str = "8-K - Apple Inc. (0000320193) (Filer)",
    accession: str = APPLE_ACCESSION,
    updated: str = "2026-08-07T16:31:05-04:00",
    summary: str | None = (
        "&lt;br&gt;Item 2.02: Results of Operations and Financial Condition"
        "&lt;br&gt;Item 9.01: Financial Statements and Exhibits"
    ),
) -> str:
    """One entry of EDGAR's current-events feed."""
    summary_element = "" if summary is None else f"<summary>{summary}</summary>"
    return (
        "<entry>"
        f"<title>{title}</title>"
        f"<id>urn:tag:sec.gov,2008:accession-number={accession}</id>"
        f"<updated>{updated}</updated>"
        f"{summary_element}"
        "</entry>"
    )


def atom_feed(*entries: str) -> str:
    return (
        '<?xml version="1.0" encoding="ISO-8859-1"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        + "".join(entries)
        + "</feed>"
    )


# Narrower columns than EDGAR publishes, which is itself part of the test: the
# parser reads these rows by shape rather than by fixed offsets, precisely
# because the real widths have changed over the years.
DAILY_INDEX = """Description:  Daily Index of EDGAR Dissemination Feed
Last Data Received:  August 07, 2026
Comments:  webmaster@sec.gov
Anonymous FTP:  ftp://ftp.sec.gov/edgar/

Form Type  Company Name  CIK  Date Filed  File Name
--------------------------------------------------
8-K  APPLE INC  320193  20260807  edgar/data/320193/0000320193-26-000010.txt
10-Q  MICROSOFT CORP  789019  20260807  edgar/data/789019/0000789019-26-000044.txt
DEF 14A  APPLE INC  320193  20260807  edgar/data/320193/0000320193-26-000011.txt
8-K  SOME PRIVATE FILER  999999  20260807  edgar/data/999999/0000999999-26-000001.txt
"""


INDEX_PAGE = """
<html><body><table>
  <tr><td>1</td><td>8-K</td><td>a8k.htm</td><td>10001</td></tr>
  <tr>
    <td>2</td><td>EX-99.1</td>
    <td>
      <a href="/Archives/edgar/data/320193/000032019326000010/ex991.htm">ex991</a>
    </td>
    <td>20002</td>
  </tr>
</table>
<p>Item 2.02: Results of Operations and Financial Condition</p>
<p>Item 9.01: Financial Statements and Exhibits</p>
</body></html>
"""

EXHIBIT_URL = (
    "https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/ex991.htm"
)

PRESS_RELEASE = """
<html><body>
<p>Apple today announced financial results for its fiscal 2026 third quarter.</p>
<p>The Company posted quarterly revenue of $94.0 billion, up 10 percent year
over year.</p>
<p>Net income was $23.4 billion and diluted earnings per share were $1.57.</p>
<p>For the fourth quarter, the Company expects revenue guidance in the range of
$98 billion to $102 billion.</p>
</body></html>
"""


TRACKED = universe.Universe(
    entries={
        APPLE_CIK: universe.UniverseEntry(
            cik=APPLE_CIK, ticker="AAPL", name="Apple Inc."
        ),
        "0000789019": universe.UniverseEntry(
            cik="0000789019", ticker="MSFT", name="Microsoft Corporation"
        ),
    }
)


def make_feed_item(**overrides: object) -> FeedItem:
    fields: dict[str, object] = {
        "accession_no": APPLE_ACCESSION,
        "cik": APPLE_CIK,
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "form": "8-K",
        "filed_at": dt.datetime(2026, 8, 7, 20, 31, tzinfo=dt.UTC),
        "headline": "Current report",
        "source_url": (
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019326000010/0000320193-26-000010-index.htm"
        ),
    }
    fields.update(overrides)
    return FeedItem(**fields)


# --- URLs ---------------------------------------------------------------------


def test_daily_index_url_derives_the_quarter_from_the_date() -> None:
    assert "QTR3" in sources.daily_index_url(dt.date(2026, 8, 7))
    assert "QTR1" in sources.daily_index_url(dt.date(2026, 3, 31))
    assert "QTR4" in sources.daily_index_url(dt.date(2026, 10, 1))
    assert "form.20260807.idx" in sources.daily_index_url(dt.date(2026, 8, 7))


def test_current_events_url_names_the_form() -> None:
    url = sources.current_events_url("8-K")
    assert "type=8-K" in url
    assert "output=atom" in url


# --- The Atom fast lane -------------------------------------------------------


def test_current_events_entry_carries_items_and_a_real_timestamp() -> None:
    (filing,) = sources.parse_current_events(atom_feed(atom_entry()))

    assert filing.accession_no == APPLE_ACCESSION
    assert filing.cik == APPLE_CIK
    assert filing.company_name == "Apple Inc."
    assert filing.form == "8-K"
    # 9.01 is an exhibit index, not an event, so m09 drops it.
    assert filing.items == ("2.02",)
    assert filing.filed_at == dt.datetime(
        2026, 8, 7, 16, 31, 5, tzinfo=dt.timezone(-dt.timedelta(hours=4))
    )


def test_an_entry_without_a_summary_leaves_items_unknown() -> None:
    """`None` means nobody looked; `()` would mean EDGAR said there were none."""
    (filing,) = sources.parse_current_events(
        atom_feed(
            atom_entry(
                title="10-Q - Apple Inc. (0000320193) (Filer)", summary=None
            )
        )
    )
    assert filing.items is None


def test_an_entry_whose_items_are_all_noise_reports_no_items() -> None:
    (filing,) = sources.parse_current_events(
        atom_feed(
            atom_entry(
                summary="&lt;br&gt;Item 9.01: Financial Statements and Exhibits"
            )
        )
    )
    assert filing.items == ()


@pytest.mark.parametrize(
    "entry",
    [
        atom_entry(title="a title EDGAR would never write"),
        atom_entry(accession="not-an-accession-number").replace(
            "accession-number=not-an-accession-number", "nothing-parseable"
        ),
        atom_entry(updated="the seventh of August"),
        # A timestamp without an offset cannot be placed on a clock.
        atom_entry(updated="2026-08-07T16:31:05"),
    ],
)
def test_a_malformed_entry_is_skipped_and_the_rest_survive(entry: str) -> None:
    filings = sources.parse_current_events(atom_feed(entry, atom_entry()))
    assert len(filings) == 1
    assert filings[0].accession_no == APPLE_ACCESSION


def test_an_unparseable_document_yields_nothing_rather_than_raising() -> None:
    assert sources.parse_current_events("<not xml at all") == ()


def test_amendments_are_recognised_by_their_suffix() -> None:
    (filing,) = sources.parse_current_events(
        atom_feed(atom_entry(title="8-K/A - Apple Inc. (0000320193) (Filer)"))
    )
    assert filing.is_amendment
    assert filing.base_form == "8-K"


# --- The daily index safety net -----------------------------------------------


def test_daily_index_keeps_tracked_forms_and_drops_the_rest() -> None:
    filings = sources.parse_daily_index(DAILY_INDEX)

    # DEF 14A is not a feed form. The untracked filer survives parsing — the
    # universe filter is the poller's job, not this function's.
    assert [filing.form for filing in filings] == ["8-K", "10-Q", "8-K"]
    assert "DEF 14A" not in {filing.form for filing in filings}


def test_daily_index_reads_the_accession_out_of_the_file_path() -> None:
    filings = sources.parse_daily_index(DAILY_INDEX)
    assert filings[0].accession_no == APPLE_ACCESSION
    assert filings[0].cik == APPLE_CIK


def test_a_reconciled_filing_is_dated_at_the_open_of_its_day() -> None:
    """Midnight Eastern: the earliest it could have been filed, so it sorts
    below the precisely-timestamped filings from the same day."""
    filing = sources.parse_daily_index(DAILY_INDEX)[0]
    assert filing.items is None
    assert filing.filed_at.date() == dt.date(2026, 8, 7)
    assert (filing.filed_at.hour, filing.filed_at.minute) == (0, 0)


def test_daily_index_preamble_is_not_read_as_filings() -> None:
    preamble_only = "\n".join(DAILY_INDEX.splitlines()[:6])
    assert sources.parse_daily_index(preamble_only) == ()


def test_both_lanes_address_the_same_page_for_the_same_filing() -> None:
    """The whole reason re-seeing a filing is an idempotent upsert."""
    fast = sources.parse_current_events(atom_feed(atom_entry()))[0]
    slow = sources.parse_daily_index(DAILY_INDEX)[0]
    assert fast.source_url == slow.source_url
    assert fast.accession_no == slow.accession_no


# --- Headlines ----------------------------------------------------------------


def test_a_headline_with_no_items_is_the_form_s_own_name() -> None:
    assert sources.headline_for("10-Q", None) == "Quarterly report"
    assert sources.headline_for("10-Q", ()) == "Quarterly report"


def test_a_single_item_headline_is_edgar_s_own_label() -> None:
    assert (
        sources.headline_for("8-K", ("2.02",))
        == "Results of operations and financial condition"
    )


def test_several_items_are_joined_with_the_first_leading() -> None:
    headline = sources.headline_for("8-K", ("2.02", "5.02"))
    assert headline.startswith("Results of operations and financial condition;")
    assert "change of directors or principal officers" in headline


def test_an_amendment_says_so_in_its_headline() -> None:
    """A restatement is not the same event as the original."""
    assert sources.headline_for("8-K/A", ("2.02",)).endswith(" (amended)")
    assert sources.headline_for("10-Q/A", None) == "Quarterly report (amended)"


def test_an_unknown_form_falls_back_to_naming_itself() -> None:
    assert sources.headline_for("S-1", None) == "S-1 filed"


# --- Conversion ---------------------------------------------------------------


def test_the_tracked_name_beats_the_name_edgar_shouted() -> None:
    parsed = sources.parse_daily_index(DAILY_INDEX)[0]
    assert parsed.company_name == "APPLE INC"

    item = sources.to_feed_item(parsed, ticker="AAPL", company_name="Apple Inc.")
    assert item.company_name == "Apple Inc."
    assert item.ticker == "AAPL"


def test_item_labels_are_derived_rather_than_carried() -> None:
    parsed = sources.parse_current_events(atom_feed(atom_entry()))[0]
    item = sources.to_feed_item(parsed, ticker="AAPL")
    assert item.items == ("2.02",)
    assert item.item_labels == ("Results of operations and financial condition",)
    assert item.headline == "Results of operations and financial condition"


# --- The tracked universe -----------------------------------------------------


def test_the_universe_matches_a_cik_however_it_is_written() -> None:
    assert TRACKED.contains("320193")
    assert TRACKED.contains(APPLE_CIK)
    assert not TRACKED.contains(UNTRACKED_CIK)


def test_an_untracked_cik_has_no_entry() -> None:
    assert TRACKED.entry(UNTRACKED_CIK) is None
    entry = TRACKED.entry("320193")
    assert entry is not None
    assert entry.ticker == "AAPL"


def test_the_checked_in_seed_loads_and_is_not_empty() -> None:
    """A packaging check: a missing seed would look like a merely quiet feed."""
    universe.reset()
    try:
        seeded = universe._read_seed()  # noqa: SLF001 — the packaging is the test
    finally:
        universe.reset()

    assert len(seeded) > 100
    assert all(len(cik) == 10 and cik.isdigit() for cik in seeded)


# --- Enrichment ---------------------------------------------------------------


def test_parse_items_reads_the_index_page_and_drops_the_noise() -> None:
    assert enrichment.parse_items(INDEX_PAGE) == ("2.02",)


async def test_enrichment_quotes_the_release_and_separates_guidance(
    stub_edgar: StubEdgarClient,
) -> None:
    item = make_feed_item()
    stub_edgar.register(str(item.source_url), INDEX_PAGE)
    stub_edgar.register(EXHIBIT_URL, PRESS_RELEASE)

    result = await enrichment.enrich(stub_edgar, item)  # type: ignore[arg-type]

    assert result.items == ("2.02",)
    assert result.headline == "Results of operations and financial condition"
    assert result.exhibit_url == EXHIBIT_URL
    assert result.discard is False

    # Every quoted sentence is the filer's own, present verbatim in the release.
    quoted = result.result_sentences + result.guidance_sentences
    assert quoted
    for sentence in quoted:
        assert sentence.strip('"').strip() in " ".join(PRESS_RELEASE.split())

    # A forecast mentions revenue but must never be filed as a reported figure.
    assert any("guidance" in s.lower() for s in result.guidance_sentences)
    assert not any("guidance" in s.lower() for s in result.result_sentences)


async def test_an_unreadable_index_costs_the_quotations_and_nothing_else(
    stub_edgar: StubEdgarClient,
) -> None:
    """The filing keeps its headline and still leaves the queue."""
    item = make_feed_item(headline="Current report")

    result = await enrichment.enrich(stub_edgar, item)  # type: ignore[arg-type]

    assert result.headline == "Current report"
    assert result.result_sentences == ()
    assert result.discard is False
    assert result.enriched_at is not None


async def test_a_current_report_with_nothing_reportable_is_discarded(
    stub_edgar: StubEdgarClient,
) -> None:
    item = make_feed_item()
    stub_edgar.register(
        str(item.source_url),
        "<html><body><p>Item 9.01 Financial Statements and Exhibits</p></body></html>",
    )

    result = await enrichment.enrich(stub_edgar, item)  # type: ignore[arg-type]
    assert result.discard is True


async def test_a_quarterly_report_with_no_items_is_kept(
    stub_edgar: StubEdgarClient,
) -> None:
    """A 10-Q never had items to list; that is not grounds for dropping it."""
    item = make_feed_item(form="10-Q", headline="Quarterly report")
    stub_edgar.register(str(item.source_url), "<html><body></body></html>")

    result = await enrichment.enrich(stub_edgar, item)  # type: ignore[arg-type]
    assert result.discard is False
    assert result.headline == "Quarterly report"


async def test_a_filing_without_a_results_item_never_costs_a_second_request(
    stub_edgar: StubEdgarClient,
) -> None:
    item = make_feed_item(items=("7.01",), headline="Regulation FD disclosure")
    stub_edgar.register(str(item.source_url), INDEX_PAGE)

    result = await enrichment.enrich(stub_edgar, item)  # type: ignore[arg-type]

    assert stub_edgar.requested == [str(item.source_url)]
    assert result.exhibit_url is None
    assert result.result_sentences == ()


async def test_an_exhibit_that_cannot_be_read_still_records_where_it_was(
    stub_edgar: StubEdgarClient,
) -> None:
    item = make_feed_item()
    stub_edgar.register(str(item.source_url), INDEX_PAGE)
    # The exhibit URL is not registered, so the stub 404s it.

    result = await enrichment.enrich(stub_edgar, item)  # type: ignore[arg-type]

    assert result.exhibit_url == EXHIBIT_URL
    assert result.result_sentences == ()
    assert result.discard is False


# --- The publishing window ----------------------------------------------------


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (dt.datetime(2026, 8, 7, 10, 0), True),  # Friday, mid-morning
        (dt.datetime(2026, 8, 7, 5, 59), False),  # Friday, before EDGAR opens
        (dt.datetime(2026, 8, 7, 22, 0), False),  # Friday, after it closes
        (dt.datetime(2026, 8, 8, 10, 0), False),  # Saturday
        (dt.datetime(2026, 8, 9, 10, 0), False),  # Sunday
        (dt.datetime(2026, 8, 10, 10, 0), True),  # Monday
    ],
)
def test_the_poller_idles_when_edgar_publishes_nothing(
    moment: dt.datetime, expected: bool
) -> None:
    assert poller.is_publishing(moment) is expected


# --- One whole cycle ----------------------------------------------------------


class RecordingStore:
    """A stand-in for `feed.store`, holding what a cycle wrote."""

    def __init__(self, pending: list[FeedItem] | None = None) -> None:
        self.inserted: list[FeedItem] = []
        self.pending = pending or []
        self.enriched: list[str] = []
        self.discarded: list[str] = []
        self.pruned_before: dt.datetime | None = None

    def insert_new(self, items: list[FeedItem]) -> int:
        self.inserted.extend(items)
        return len(items)

    def unenriched(self, limit: int) -> list[FeedItem]:
        return self.pending[:limit]

    def record_enrichment(
        self, accession_no: str, enrichment_result: object
    ) -> None:
        self.enriched.append(accession_no)

    def discard(self, accession_no: str) -> None:
        self.discarded.append(accession_no)

    def prune(self, before: dt.datetime) -> int:
        self.pruned_before = before
        return 0


@pytest.fixture
def recording_store(monkeypatch: pytest.MonkeyPatch) -> RecordingStore:
    recorder = RecordingStore()
    for name in (
        "insert_new",
        "unenriched",
        "record_enrichment",
        "discard",
        "prune",
    ):
        monkeypatch.setattr(store, name, getattr(recorder, name))
    monkeypatch.setattr(universe, "load", lambda **_: TRACKED)
    return recorder


async def test_a_cycle_stores_only_filings_by_tracked_companies(
    stub_edgar: StubEdgarClient, recording_store: RecordingStore
) -> None:
    untracked = atom_entry(
        title="8-K - Some Private Filer LLC (0000999999) (Filer)",
        accession="0000999999-26-000001",
    )
    for form in ("8-K", "10-Q", "10-K", "6-K"):
        body = atom_feed(atom_entry(), untracked) if form == "8-K" else atom_feed()
        stub_edgar.register(sources.current_events_url(form), body)

    report = await poller.run_once(reconcile=False)

    assert report.ok, report.errors
    assert [item.accession_no for item in recording_store.inserted] == [
        APPLE_ACCESSION
    ]
    # The universe supplies the name, so the feed says "Apple Inc.".
    assert recording_store.inserted[0].company_name == "Apple Inc."


async def test_a_cycle_survives_a_form_whose_feed_is_down(
    stub_edgar: StubEdgarClient, recording_store: RecordingStore
) -> None:
    """Three forms read is still a cycle that reached EDGAR."""
    stub_edgar.register(sources.current_events_url("8-K"), atom_feed(atom_entry()))
    for form in ("10-Q", "10-K"):
        stub_edgar.register(sources.current_events_url(form), atom_feed())
    # 6-K is left unregistered, so the stub raises for it.

    report = await poller.run_once(reconcile=False)

    assert not report.ok
    assert any("6-K" in failure for failure in report.errors)
    assert report.stored == 1
    assert poller.last_checked_at() is not None


async def test_a_cycle_never_stores_a_filing_edgar_said_had_no_items(
    stub_edgar: StubEdgarClient, recording_store: RecordingStore
) -> None:
    """Never stored, so it never has to be discarded later."""
    noise = atom_entry(
        summary="&lt;br&gt;Item 9.01: Financial Statements and Exhibits"
    )
    stub_edgar.register(sources.current_events_url("8-K"), atom_feed(noise))
    for form in ("10-Q", "10-K", "6-K"):
        stub_edgar.register(sources.current_events_url(form), atom_feed())

    report = await poller.run_once(reconcile=False)

    assert recording_store.inserted == []
    assert report.stored == 0


async def test_a_cycle_enriches_a_bounded_number_and_discards_the_noise(
    stub_edgar: StubEdgarClient,
    recording_store: RecordingStore,
) -> None:
    quotable = make_feed_item()
    noise = make_feed_item(
        accession_no="0000320193-26-000012",
        source_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019326000012/0000320193-26-000012-index.htm"
        ),
    )
    recording_store.pending = [quotable, noise]

    for form in ("8-K", "10-Q", "10-K", "6-K"):
        stub_edgar.register(sources.current_events_url(form), atom_feed())
    stub_edgar.register(str(quotable.source_url), INDEX_PAGE)
    stub_edgar.register(EXHIBIT_URL, PRESS_RELEASE)
    stub_edgar.register(
        str(noise.source_url),
        "<html><body><p>Item 9.01 Financial Statements and Exhibits</p></body></html>",
    )

    report = await poller.run_once(reconcile=False)

    assert recording_store.enriched == [quotable.accession_no]
    assert recording_store.discarded == [noise.accession_no]
    assert (report.enriched, report.discarded) == (1, 1)


async def test_reconciliation_sweeps_the_day_and_prunes_the_window(
    stub_edgar: StubEdgarClient, recording_store: RecordingStore
) -> None:
    for form in ("8-K", "10-Q", "10-K", "6-K"):
        stub_edgar.register(sources.current_events_url(form), atom_feed())
    today = poller._now_eastern().date()  # noqa: SLF001 — the URL is date-derived
    stub_edgar.register(sources.daily_index_url(today), DAILY_INDEX)

    report = await poller.run_once(reconcile=True)

    # Two of the index's four rows are by tracked companies; DEF 14A and the
    # untracked filer are both dropped.
    assert report.reconciled == 2
    assert {item.ticker for item in recording_store.inserted} == {"AAPL", "MSFT"}
    assert recording_store.pruned_before is not None
    assert recording_store.pruned_before < dt.datetime.now(dt.UTC)


async def test_a_missing_daily_index_is_not_an_error(
    stub_edgar: StubEdgarClient, recording_store: RecordingStore
) -> None:
    """Normal before the day's first filing, and every weekend."""
    for form in ("8-K", "10-Q", "10-K", "6-K"):
        stub_edgar.register(sources.current_events_url(form), atom_feed())
    # The daily index is left unregistered, so the stub 404s it.

    report = await poller.run_once(reconcile=True)

    assert report.ok, report.errors
    assert report.reconciled == 0


async def test_a_daily_index_sec_refuses_is_absence_not_failure(
    stub_edgar: StubEdgarClient,
    recording_store: RecordingStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A regression test with a live-verified premise.

    SEC answers a daily index whose file has not been built with 403 and never
    with 404 — checked against sec.gov for a Sunday, for the current morning
    and for a date a year out, all three of which refuse. Catching only
    `EdgarNotFoundError` therefore marked every weekend and every pre-index
    morning as a failed cycle, which is exactly the false alarm the poller is
    supposed to be immune to.
    """
    for form in ("8-K", "10-Q", "10-K", "6-K"):
        stub_edgar.register(sources.current_events_url(form), atom_feed())

    index_url = sources.daily_index_url(poller._now_eastern().date())  # noqa: SLF001
    unrefused = stub_edgar.get_bytes

    async def refuse(url: str, ttl: float | None = None) -> bytes:
        if url == index_url:
            raise edgar.EdgarForbiddenError(f"EDGAR refused the request for {url}")
        return await unrefused(url, ttl)

    monkeypatch.setattr(stub_edgar, "get_bytes", refuse)

    report = await poller.run_once(reconcile=True)

    assert report.ok, report.errors
    assert report.reconciled == 0
    # The prune still ran: a refused index must not cost the retention sweep.
    assert recording_store.pruned_before is not None


async def test_a_daily_index_that_fails_some_other_way_is_still_an_error(
    stub_edgar: StubEdgarClient,
    recording_store: RecordingStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only absence is forgiven. A real outage must still be visible."""
    for form in ("8-K", "10-Q", "10-K", "6-K"):
        stub_edgar.register(sources.current_events_url(form), atom_feed())

    index_url = sources.daily_index_url(poller._now_eastern().date())  # noqa: SLF001
    reachable = stub_edgar.get_bytes

    async def fail(url: str, ttl: float | None = None) -> bytes:
        if url == index_url:
            raise edgar.EdgarUnavailableError("EDGAR did not answer")
        return await reachable(url, ttl)

    monkeypatch.setattr(stub_edgar, "get_bytes", fail)

    report = await poller.run_once(reconcile=True)

    assert not report.ok
    assert any("daily index" in failure for failure in report.errors)
