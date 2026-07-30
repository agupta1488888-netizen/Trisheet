"""m09 — recent developments.

Responsibility
    Build a dated timeline from current reports (8-K, or 6-K for foreign
    filers), each entry sourced to the filing it came from.

    Two things happen here.

    Filtering by item number. An 8-K's item numbers are the filer's own index
    of what the filing is about, and only some of them belong in a company
    profile. `DEVELOPMENT_ITEMS` names the ones kept — a material agreement, a
    completed acquisition, results of operations, a change of officers, a
    bankruptcy, a new financial obligation, a Regulation FD disclosure, other
    events — and everything else is dropped. The exhibit index (9.01) and
    housekeeping items are noise; a filing whose only items are noise never
    reaches the timeline.

    Reading the earnings release. Item 2.02 filings carry the numbers the press
    reports, and they are not in the primary document — they are in the EX-99.1
    press release. That exhibit is fetched and scanned for two things: sentences
    stating a headline result, and sentences stating guidance.

Guidance is not a result
    A guidance sentence is forward-looking. It is stored under its own metric
    and its display value is prefixed, so a projection can never be read as a
    reported figure by anything downstream — including the writer, which sees
    only the labelled text.

Degradation
    Only the manifest is required. An exhibit that cannot be fetched or parsed
    costs its quotations, not the event; an event that cannot be built costs
    itself, not the timeline. Nothing in the public interface raises.

Public interface
    build_timeline(company, manifest, client) -> DevelopmentTimeline
    keep_items(items) -> tuple[str, ...]
    parse_release(text) -> ReleaseExtract
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.config import (
    CURRENT_REPORT_FORMS,
    DEVELOPMENT_EVENT_METRIC,
    DEVELOPMENT_GUIDANCE_METRIC,
    DEVELOPMENT_ITEMS,
    DEVELOPMENT_RESULT_METRIC,
    DEVELOPMENT_SENTENCE_MAX_CHARS,
    DEVELOPMENT_SENTENCE_MIN_CHARS,
    EARNINGS_RELEASE_EXHIBIT,
    EARNINGS_RELEASE_ITEM,
    GUIDANCE_LABEL_PREFIX,
    GUIDANCE_MARKERS,
    MAX_DEVELOPMENTS,
    MAX_EARNINGS_RELEASES_PARSED,
    MAX_FILING_TEXT_BYTES,
    MAX_GUIDANCE_SENTENCES,
    MAX_RESULT_SENTENCES,
    RESULT_MARKERS,
)
from app.models import (
    Company,
    DevelopmentEvent,
    DevelopmentTimeline,
    ExtractionMethod,
    Fact,
    FilingRef,
    SourceTier,
    SourceType,
)
from app.services import document
from app.services.edgar import EdgarClient, EdgarError

logger = logging.getLogger(__name__)

#: Item numbers as EDGAR writes them: "2.02", sometimes inside a longer label
#: such as "Item 2.02 Results of Operations".
_ITEM_NUMBER = re.compile(r"\b(\d{1,2}\.\d{2})\b")


@dataclass(frozen=True, slots=True)
class ReleaseExtract:
    """What was read out of an earnings release exhibit."""

    results: tuple[str, ...] = ()
    guidance: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.results and not self.guidance


# --- Item filtering ---------------------------------------------------------


def keep_items(items: Sequence[str]) -> tuple[str, ...]:
    """The item numbers worth reporting, normalised and deduplicated.

    EDGAR writes items several ways across the years — bare numbers, numbers
    with a title, comma-joined runs — so the number is extracted rather than
    matched whole. Order follows `DEVELOPMENT_ITEMS`, which is the order a
    reader expects to see them in, not the order the filer listed them.
    """
    found: set[str] = set()
    for raw in items:
        for match in _ITEM_NUMBER.finditer(raw):
            number = match.group(1)
            if number in DEVELOPMENT_ITEMS:
                found.add(number)
    return tuple(number for number in DEVELOPMENT_ITEMS if number in found)


def _labels_for(items: Sequence[str]) -> tuple[str, ...]:
    """What each kept item number means, in EDGAR's own words."""
    return tuple(DEVELOPMENT_ITEMS[item] for item in items)


def _headline(items: Sequence[str], form: str) -> str:
    """One line describing the filing, built from what the filer itself said.

    Never invented: the text is the item labels EDGAR publishes. A filing whose
    items are all unknown does not reach here.
    """
    labels = _labels_for(items)
    if not labels:
        return f"{form} filed"
    if len(labels) == 1:
        return labels[0]
    return f"{labels[0]}; {', '.join(label.lower() for label in labels[1:])}"


# --- Earnings release parsing -----------------------------------------------


def _is_quotable(sentence: str) -> bool:
    """True when a sentence is the right shape to quote in a timeline."""
    length = len(sentence)
    return (
        DEVELOPMENT_SENTENCE_MIN_CHARS <= length <= DEVELOPMENT_SENTENCE_MAX_CHARS
    )


def _mentions(sentence: str, markers: Sequence[str]) -> bool:
    lowered = sentence.lower()
    return any(marker in lowered for marker in markers)


def _has_figure(sentence: str) -> bool:
    """True when a sentence carries a number.

    A headline result without a figure is a claim about a figure, which is
    exactly the kind of sentence this system does not quote as a result.
    """
    return any(character.isdigit() for character in sentence)


def parse_release(text: str) -> ReleaseExtract:
    """Reads headline results and guidance out of an earnings release.

    Guidance is tested for first: a sentence that says "we expect full year
    revenue of ..." mentions revenue and is therefore a result by keyword, but
    it is a projection. Classifying it as guidance is what keeps a forecast out
    of the reported figures.

    Pure. Takes text and returns what it found; fetching happens elsewhere.
    """
    sentences = document.split_sentences(text)

    results: list[str] = []
    guidance: list[str] = []

    for sentence in sentences:
        if not _is_quotable(sentence):
            continue

        if _mentions(sentence, GUIDANCE_MARKERS):
            if len(guidance) < MAX_GUIDANCE_SENTENCES:
                guidance.append(sentence)
            continue

        if _mentions(sentence, RESULT_MARKERS) and _has_figure(sentence):
            if len(results) < MAX_RESULT_SENTENCES:
                results.append(sentence)

        if (
            len(results) >= MAX_RESULT_SENTENCES
            and len(guidance) >= MAX_GUIDANCE_SENTENCES
        ):
            break

    return ReleaseExtract(results=tuple(results), guidance=tuple(guidance))


async def _read_release(
    client: EdgarClient, filing: FilingRef
) -> tuple[ReleaseExtract, str | None]:
    """Fetches and parses a filing's EX-99.1, if it has one.

    Returns the extract and the exhibit URL it came from. An exhibit that
    cannot be fetched or parsed yields an empty extract — the event still
    reaches the timeline, just without quotations.
    """
    exhibit = next(
        (
            candidate
            for candidate in filing.exhibits
            if candidate.exhibit_type == EARNINGS_RELEASE_EXHIBIT
        ),
        None,
    )
    if exhibit is None:
        return ReleaseExtract(), None

    url = str(exhibit.url)
    try:
        body = await client.get_bytes(url)
    except EdgarError:
        logger.warning(
            "Could not read the earnings release exhibit",
            extra={"accession_no": filing.accession_no, "url": url},
        )
        return ReleaseExtract(), None

    if len(body) > MAX_FILING_TEXT_BYTES:
        logger.warning(
            "Earnings release exhibit is larger than the parse ceiling",
            extra={"accession_no": filing.accession_no, "bytes": len(body)},
        )
        return ReleaseExtract(), url

    text = document.html_to_text(body.decode("utf-8", errors="replace"))
    if not text:
        return ReleaseExtract(), url

    return parse_release(text), url


# --- Fact construction ------------------------------------------------------


def _textual_fact(
    *,
    metric: str,
    label: str,
    display_value: str,
    filing: FilingRef,
    source_url: str,
) -> Fact:
    """Builds one textual fact, sourced to the filing it was quoted from.

    The value is None and the display value is the quotation: a development is
    a statement, not a figure. It still carries full provenance, which is what
    lets the writer cite it and the checker verify the citation.
    """
    return Fact(
        metric=metric,
        label=label,
        value=None,
        display_value=display_value,
        unit=None,
        period_start=None,
        period_end=filing.period_of_report or filing.filed_date,
        fiscal_year=None,
        tier=SourceTier.FILING,
        source_type=SourceType.SEC_FILING,
        source_url=source_url,
        accession_no=filing.accession_no,
        filed_date=filing.filed_date,
        extraction_method=ExtractionMethod.NARRATIVE,
        confidence=1.0,
    )


def _truncate(text: str) -> str:
    """Caps a quotation at the stated ceiling, marking where it was cut."""
    if len(text) <= DEVELOPMENT_SENTENCE_MAX_CHARS:
        return text
    return f"{text[: DEVELOPMENT_SENTENCE_MAX_CHARS - 1].rstrip()}…"


def _build_event(
    filing: FilingRef,
    items: tuple[str, ...],
    extract: ReleaseExtract,
    exhibit_url: str | None,
) -> tuple[DevelopmentEvent, list[Fact]]:
    """Assembles one timeline entry and the facts behind it."""
    primary_url = str(filing.primary_doc_url)
    quotation_url = exhibit_url or primary_url
    headline = _headline(items, filing.form)

    facts: list[Fact] = [
        _textual_fact(
            metric=DEVELOPMENT_EVENT_METRIC,
            label=f"{filing.form} filed {filing.filed_date.isoformat()}",
            display_value=headline,
            filing=filing,
            source_url=primary_url,
        )
    ]

    # Indexed rather than one shared metric: every sentence in the loop quotes
    # the same filing, so without a per-sentence suffix they would all carry
    # the same (metric, period_end) identity and collide on the fact store's
    # uniqueness constraint the instant a release states more than one.
    for index, sentence in enumerate(extract.results, start=1):
        facts.append(
            _textual_fact(
                metric=f"{DEVELOPMENT_RESULT_METRIC}.{index}",
                label="Reported in the earnings release",
                display_value=_truncate(sentence),
                filing=filing,
                source_url=quotation_url,
            )
        )

    for index, sentence in enumerate(extract.guidance, start=1):
        facts.append(
            _textual_fact(
                metric=f"{DEVELOPMENT_GUIDANCE_METRIC}.{index}",
                label="Guidance stated in the earnings release",
                # Prefixed so a forward-looking statement cannot be rendered,
                # quoted or cited as though it were a reported figure.
                display_value=f"{GUIDANCE_LABEL_PREFIX} {_truncate(sentence)}",
                filing=filing,
                source_url=quotation_url,
            )
        )

    event = DevelopmentEvent(
        id=filing.accession_no,
        date=filing.filed_date,
        form=filing.form,
        accession_no=filing.accession_no,
        items=items,
        item_labels=_labels_for(items),
        headline=headline,
        source_url=primary_url,
        result_sentences=extract.results,
        guidance_sentences=extract.guidance,
        fact_ids=tuple(fact.fact_id for fact in facts),
    )
    return event, facts


# --- Public interface -------------------------------------------------------


def _current_reports(manifest: Sequence[FilingRef]) -> list[FilingRef]:
    """Current reports from the manifest, newest first.

    A foreign private issuer files 6-K rather than 8-K, so both forms are
    accepted and the item filter decides what is reportable. A 6-K carries no
    item numbers, which is why it contributes to the timeline only when EDGAR
    happens to record them.
    """
    current = [
        filing
        for filing in manifest
        if filing.base_form in CURRENT_REPORT_FORMS
    ]
    current.sort(
        key=lambda filing: (filing.filed_date, filing.accession_no), reverse=True
    )
    return current


async def build_timeline(
    company: Company,
    manifest: Sequence[FilingRef],
    client: EdgarClient,
) -> DevelopmentTimeline:
    """Builds the developments timeline, most recent first.

    Args:
        company: The filer. Used for logging correlation only — what goes in
            the timeline is decided entirely by the filings.
        manifest: Filings from m02, with exhibits attached. A manifest built
            with `include_exhibits=False` still produces a timeline; it simply
            has no quotations in it.
        client: The shared EDGAR client, used to fetch earnings releases.

    Returns:
        A timeline of typed events and the facts behind them. Empty when the
        filer has filed no reportable current report — which is a finding, not
        a failure.
    """
    candidates = _current_reports(manifest)

    events: list[DevelopmentEvent] = []
    facts: list[Fact] = []
    releases_parsed = 0

    for filing in candidates:
        if len(events) >= MAX_DEVELOPMENTS:
            break

        items = keep_items(filing.items)
        if not items:
            continue

        extract = ReleaseExtract()
        exhibit_url: str | None = None

        if (
            EARNINGS_RELEASE_ITEM in items
            and releases_parsed < MAX_EARNINGS_RELEASES_PARSED
        ):
            releases_parsed += 1
            extract, exhibit_url = await _read_release(client, filing)

        try:
            event, event_facts = _build_event(
                filing, items, extract, exhibit_url
            )
        except ValueError as cause:
            # A filing that cannot produce a sound fact is dropped, not
            # patched. One unusable current report is not a failed report.
            logger.warning(
                "Development could not be built from this filing",
                extra={
                    "cik": company.cik,
                    "accession_no": filing.accession_no,
                    "error": str(cause),
                },
            )
            continue

        events.append(event)
        facts.extend(event_facts)

    logger.info(
        "Developments timeline built",
        extra={
            "cik": company.cik,
            "current_reports": len(candidates),
            "events": len(events),
            "releases_parsed": releases_parsed,
            "facts": len(facts),
        },
    )
    return DevelopmentTimeline(events=tuple(events), facts=tuple(facts))


def latest_earnings_date(
    timeline: DevelopmentTimeline,
) -> dt.date | None:
    """When the filer last reported results, or None if it has not.

    Public because the snapshot section states it and the assembler needs it
    without re-deriving which item number means "results".
    """
    for event in timeline.events:
        if EARNINGS_RELEASE_ITEM in event.items:
            return event.date
    return None
