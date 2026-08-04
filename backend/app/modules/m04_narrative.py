"""m04 — narrative sections.

Responsibility
    Extract the text sections of the annual report: the business description,
    the risk factors and the operating review. Emits facts whose value is
    textual rather than numeric — the filer's own words, carrying the accession
    number they came from.

What this does not do
    It does not summarise, rank or interpret. A risk is stated as the filer
    headed it; a business description is quoted, not characterised. The model
    that later writes prose is shown this text rather than asked to recall it,
    which is what keeps section 2 and section 8 sourced.

Degradation
    Narrative parsing is not a hard dependency. Every failure yields fewer
    sections, never an exception: if it fails entirely the report is still
    generated from XBRL alone, with those sections marked unavailable.

Public interface
    extract_narrative(company, manifest) -> list[Fact]
    risk_headings(facts) -> list[str]
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from pydantic import ValidationError

from app.config import (
    ISOLATED_HEADING_SLACK_CHARS,
    MAX_FILING_TEXT_BYTES,
    MAX_RISK_ITEMS,
    NARRATIVE_EXHIBIT_TYPES,
    NARRATIVE_MAX_EXHIBITS,
    NARRATIVE_MAX_SECTION_CHARS,
    NARRATIVE_MIN_SECTION_CHARS,
    NARRATIVE_OVERSIZE_WINDOW_BYTES,
    NARRATIVE_SPECS,
    NARRATIVE_SUMMARY_CHARS,
    NARRATIVE_TRUNCATION_NOTE,
    RISK_HEADING_MARKERS,
    RISK_HEADING_MAX_CHARS,
    RISK_HEADING_MIN_CHARS,
    NarrativeFallback,
    NarrativeSpec,
)
from app.models import (
    Company,
    ExtractionMethod,
    Fact,
    FilerType,
    FilingRef,
    SourceTier,
    SourceType,
)
from app.services import document, edgar
from app.services.edgar import EdgarError

logger = logging.getLogger(__name__)

#: The metric whose fact carries the risk factors item. Risk headings are read
#: back off it rather than recomputed, so the list and the quotation agree.
RISK_METRIC = "risk.factors"

#: Confidence on a narrative fact. It is not 1.0 because locating an item by
#: its heading is a heuristic over a document with no machine-readable
#: structure — unlike an XBRL tag, which either resolved or did not.
NARRATIVE_CONFIDENCE = 0.8

#: A paragraph break in extracted filing text.
_PARAGRAPH = re.compile(r"\n\s*\n")

#: Runs of whitespace inside a heading candidate.
_SPACES = re.compile(r"\s+")


async def extract_narrative(
    company: Company, manifest: Sequence[FilingRef]
) -> list[Fact]:
    """Extracts the annual report's narrative items as Tier 1 textual facts.

    Each item is searched for down a ladder rather than at one address. The
    item's own heading is tried first; then each fallback the spec declares,
    which are punctuation and wording variants of the same heading; and
    finally, for anything still missing, the filing's exhibits. A 40-F carries
    its annual information form as an exhibit and a few 10-K filers carry
    Item 1 the same way, so a primary document that does not contain the item
    is not evidence that the filing does not.

    The ladder exists because a single heading list turned ordinary filer
    variation into a missing section: the business description and the risk
    factors rendered as "could not be read" on filers whose XBRL extracted
    perfectly. A rung costs only itself. Exhausting every rung costs the
    section, and never the report.

    Args:
        company: The filer. Its type decides which form is the annual report
            and therefore which item numbering applies — a 20-F is not
            searched for an Item 1A that it does not have.
        manifest: Filings from m02, with exhibits attached where m02 was asked
            for them. A manifest built without exhibits still works; the last
            rung simply has nothing to open.

    Returns:
        One fact per item located. Empty when there is no annual report in the
        manifest, when it could not be fetched, or when no rung located any of
        its items — each of which is logged and none of which raises.
    """
    annual = _latest_annual(manifest, company.filer_type)
    if annual is None:
        logger.info(
            "No annual report in the manifest; narrative skipped",
            extra={"cik": company.cik, "manifest_size": len(manifest)},
        )
        return []

    specs = [spec for spec in NARRATIVE_SPECS if annual.base_form in spec.forms]

    text = await _filing_text(str(annual.primary_doc_url), annual.accession_no)
    facts: list[Fact] = []
    unresolved: list[NarrativeSpec] = []

    for spec in specs:
        located = _locate_down_ladder(text, spec) if text else None
        if located is None:
            unresolved.append(spec)
            continue

        body, rung = located
        fact = _build_fact(spec, body, annual, str(annual.primary_doc_url))
        if fact is not None:
            facts.append(fact)
            logger.info(
                "Narrative item located",
                extra={
                    "cik": company.cik,
                    "metric": spec.metric,
                    "rung": rung,
                    "accession_no": annual.accession_no,
                },
            )

    if unresolved:
        facts.extend(await _from_exhibits(company, annual, unresolved))

    logger.info(
        "Narrative extraction complete",
        extra={
            "cik": company.cik,
            "form": annual.form,
            "accession_no": annual.accession_no,
            "items_found": len(facts),
            "items_searched": len(specs),
        },
    )
    return facts


async def _from_exhibits(
    company: Company, annual: FilingRef, specs: Sequence[NarrativeSpec]
) -> list[Fact]:
    """The last rung: look for what the primary document did not contain.

    Bounded by `NARRATIVE_MAX_EXHIBITS` and filtered to the exhibit types that
    can plausibly carry narrative — an EX-21 subsidiary list never does, and
    fetching one is pure cost against the SEC's rate limit.

    Never raises. An exhibit that cannot be read costs that exhibit.
    """
    candidates = [
        exhibit
        for exhibit in annual.exhibits
        if any(
            exhibit.exhibit_type.upper().startswith(wanted)
            for wanted in NARRATIVE_EXHIBIT_TYPES
        )
    ][:NARRATIVE_MAX_EXHIBITS]

    if not candidates:
        for spec in specs:
            logger.info(
                "Narrative item not found, and the filing has no exhibit to search",
                extra={
                    "cik": company.cik,
                    "metric": spec.metric,
                    "form": annual.form,
                    "accession_no": annual.accession_no,
                },
            )
        return []

    outstanding = list(specs)
    facts: list[Fact] = []

    for exhibit in candidates:
        if not outstanding:
            break

        text = await _filing_text(str(exhibit.url), annual.accession_no)
        if not text:
            continue

        still_missing: list[NarrativeSpec] = []
        for spec in outstanding:
            located = _locate_down_ladder(text, spec)
            if located is None:
                still_missing.append(spec)
                continue

            body, rung = located
            fact = _build_fact(spec, body, annual, str(exhibit.url))
            if fact is None:
                still_missing.append(spec)
                continue

            facts.append(fact)
            logger.info(
                "Narrative item located in an exhibit",
                extra={
                    "cik": company.cik,
                    "metric": spec.metric,
                    "rung": rung,
                    "exhibit_type": exhibit.exhibit_type,
                    "accession_no": annual.accession_no,
                },
            )
        outstanding = still_missing

    for spec in outstanding:
        logger.info(
            "Narrative item not found on any rung",
            extra={
                "cik": company.cik,
                "metric": spec.metric,
                "form": annual.form,
                "accession_no": annual.accession_no,
                "exhibits_searched": len(candidates),
            },
        )
    return facts


def risk_headings(facts: Sequence[Fact]) -> list[str]:
    """The filer's own risk headings, read back off the risk factors fact.

    Public because m12 renders these as the report's risk list and must not
    invent its own. Returns an empty list when no risk factors item was
    extracted, which renders as an unavailable section rather than a blank one.
    """
    risk = next((fact for fact in facts if fact.metric == RISK_METRIC), None)
    if risk is None:
        return []
    return _headings_in(risk.display_value)


# --- Locating an item --------------------------------------------------------


def _locate_down_ladder(
    text: str, spec: NarrativeSpec
) -> tuple[str, str] | None:
    """The item's text and the name of the rung that found it, or None.

    Rungs are tried in the order the spec declares and stop at the first that
    produces a body. Ordering matters: the preferred heading is the most
    specific, and each fallback below it is a wording the filer might have
    used instead — never a looser match that would admit a cross-reference.

    Public behaviour worth stating: a rung is not consulted when an earlier
    one answered, so a filing headed conventionally costs exactly what it did
    before this ladder existed.
    """
    body = _locate(text, spec)
    if body is not None:
        return body, "the item's own heading"

    for fallback in spec.fallbacks:
        body = _locate(text, _as_spec(spec, fallback))
        if body is not None:
            return body, fallback.reason
    return None


def _as_spec(spec: NarrativeSpec, fallback: NarrativeFallback) -> NarrativeSpec:
    """A fallback expressed as a spec, so `_locate` needs no second code path.

    The metric, label and forms are the item's; only where to look changes.
    """
    return NarrativeSpec(
        metric=spec.metric,
        label=spec.label,
        headings=fallback.headings,
        terminators=fallback.terminators,
        forms=spec.forms,
    )


def _locate(text: str, spec: NarrativeSpec) -> str | None:
    """The text of one item, or None when it is not in this document.

    A 10-K names every item at least twice: once in its table of contents,
    once as the item heading itself, and often again wherever later prose
    cross-references it ("see Item 1. Business"). The contents entry comes
    first and is followed by a page number, so the *last* heading match that
    yields a long enough body used to be treated as the real one — but a
    cross-reference near the end of the document can satisfy that same test.
    What a cross-reference never does is set the heading in capitals the way
    the filer's own heading line does, so a capitalised match is preferred
    when one exists. Some filers never set a heading in capitals at all —
    several 20-F filers write "Risk Factors" in title case throughout —
    which leaves that signal empty. For those, a match that stands alone as
    its own paragraph is preferred instead, which rules out a cross-reference
    sitting inside a longer sentence and a differently-scoped heading that
    merely contains the phrase (a "Risk Factors and Risk Management" note
    inside the financial statements, say). Only if neither signal finds
    anything does document order become the tiebreaker, for filers that fit
    neither convention.
    """
    lowered = text.lower()

    for heading in spec.headings:
        occurrences = _occurrences(lowered, heading.lower())
        capitalised = [
            start
            for start in occurrences
            if text[start : start + len(heading)].isupper()
        ]
        isolated = [
            start
            for start in occurrences
            if _is_isolated_heading(text, start, len(heading))
        ]

        for start in reversed(capitalised or isolated or occurrences):
            body = _body_from(text, lowered, start, spec)
            if body is not None:
                return body
    return None


def _is_isolated_heading(text: str, start: int, heading_len: int) -> bool:
    """True when a match sits alone in its paragraph, the way a heading does.

    A cross-reference sits inside a longer sentence; an unrelated heading
    that merely contains the phrase runs longer than the phrase itself. Both
    are excluded by requiring the enclosing paragraph to be close in length
    to the heading match — a real heading line is essentially just that line.
    """
    para_start = text.rfind("\n\n", 0, start)
    para_start = 0 if para_start == -1 else para_start + 2
    para_end = text.find("\n\n", start + heading_len)
    para_end = len(text) if para_end == -1 else para_end
    paragraph = text[para_start:para_end].strip()
    return len(paragraph) <= heading_len + ISOLATED_HEADING_SLACK_CHARS


def _occurrences(haystack: str, needle: str) -> list[int]:
    """Every index at which `needle` appears, in document order."""
    found: list[int] = []
    at = haystack.find(needle)
    while at != -1:
        found.append(at)
        at = haystack.find(needle, at + len(needle))
    return found


def _body_from(
    text: str, lowered: str, start: int, spec: NarrativeSpec
) -> str | None:
    """The item running from `start` to whichever terminator comes first.

    Returns None when the result is too short to be the item — which is what a
    table-of-contents line produces, and is the signal to try an earlier match.
    """
    end = len(text)
    for terminator in spec.terminators:
        at = _find_boundary(text, lowered, terminator, start)
        if at is not None:
            end = min(end, at)

    body = text[start:end].strip()
    if len(body) < NARRATIVE_MIN_SECTION_CHARS:
        return None
    return body


def _find_boundary(
    text: str, lowered: str, terminator: str, after: int
) -> int | None:
    """The next real heading matching `terminator`, or None if it never appears.

    A section's own prose often cross-references the item that closes it —
    an MD&A overview typically says "see Item 8. Financial Statements" long
    before Item 8 actually begins, which would truncate the body at that
    mention rather than the section's real end. A genuine heading is
    capitalised the way the filer's own heading line is; a cross-reference
    is not, so the first capitalised occurrence wins. Only when no occurrence
    is capitalised — a filer that does not follow that convention — does the
    first occurrence at all stand in, preserving the previous behaviour.
    """
    needle = terminator.lower()
    first_at: int | None = None
    at = lowered.find(needle, after + 1)
    while at != -1:
        if first_at is None:
            first_at = at
        if text[at : at + len(terminator)].isupper():
            return at
        at = lowered.find(needle, at + len(needle))
    return first_at


def _truncate(body: str) -> str:
    """Cuts an over-long item at a sentence boundary and says that it was cut.

    Never a silent cut: the note travels in the text, and the fact's source URL
    points at the filing where the rest of it is.
    """
    if len(body) <= NARRATIVE_MAX_SECTION_CHARS:
        return body

    window = body[:NARRATIVE_MAX_SECTION_CHARS]
    boundary = max(window.rfind(". "), window.rfind(".\n"))
    if boundary > NARRATIVE_MIN_SECTION_CHARS:
        window = window[: boundary + 1]
    return window.rstrip() + NARRATIVE_TRUNCATION_NOTE


# --- Risk headings -----------------------------------------------------------


def _headings_in(risk_text: str) -> list[str]:
    """The individually headed risks inside a risk factors item.

    A 10-K heads each risk with a one-line claim — "Our business is subject to
    seasonal fluctuations" — set off as its own paragraph. Paragraphs that read
    like a heading are kept in the order the filer wrote them; nothing is
    reordered, ranked or rewritten.
    """
    headings: list[str] = []
    seen: set[str] = set()

    for block in _PARAGRAPH.split(risk_text):
        candidate = _SPACES.sub(" ", block).strip()
        if not _is_heading(candidate):
            continue

        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        headings.append(candidate)

        if len(headings) >= MAX_RISK_ITEMS:
            break

    return headings


def _is_heading(candidate: str) -> bool:
    """True when a paragraph reads like the filer's heading for one risk."""
    if not RISK_HEADING_MIN_CHARS <= len(candidate) <= RISK_HEADING_MAX_CHARS:
        return False
    # A heading is one claim. Two full stops mean it is already prose.
    if candidate.count(". ") > 1:
        return False
    lowered = candidate.lower()
    if lowered.startswith("item "):
        return False
    return any(lowered.startswith(marker) for marker in RISK_HEADING_MARKERS)


# --- Fact construction -------------------------------------------------------


def _build_fact(
    spec: NarrativeSpec, body: str, filing: FilingRef, source_url: str
) -> Fact | None:
    """One textual fact for a located item, or None if it cannot be sound.

    The whole item is the display value: m10 is shown the filer's words and
    restates them, and m11 checks any figure in that prose against the fact
    store like any other. A fact that will not validate is dropped rather than
    patched — an unsourced quotation is worse than a missing section.

    `source_url` is passed rather than read off the filing because an item
    found on the exhibit rung came from the exhibit, and a citation must point
    at the document the words are actually in. The accession number stays the
    filing's, which is what ties the exhibit back to what it was filed under.
    """
    try:
        return Fact(
            metric=spec.metric,
            label=spec.label,
            value=None,
            display_value=_truncate(body),
            unit=None,
            period_end=filing.period_of_report or filing.filed_date,
            fiscal_year=(filing.period_of_report or filing.filed_date).year,
            tier=SourceTier.FILING,
            source_type=SourceType.SEC_FILING,
            source_url=source_url,
            accession_no=filing.accession_no,
            filed_date=filing.filed_date,
            extraction_method=ExtractionMethod.NARRATIVE,
            confidence=NARRATIVE_CONFIDENCE,
        )
    except ValidationError as cause:
        logger.warning(
            "Narrative item could not be made into a fact",
            extra={
                "metric": spec.metric,
                "accession_no": filing.accession_no,
                "error": str(cause),
            },
        )
        return None


def summary_of(fact: Fact) -> str:
    """The opening of a narrative fact, for a table cell or a card.

    Public because m12 renders it and must not re-derive the cut differently
    from the fact it came from.
    """
    text = fact.display_value.strip()
    if len(text) <= NARRATIVE_SUMMARY_CHARS:
        return text
    window = text[:NARRATIVE_SUMMARY_CHARS]
    boundary = window.rfind(" ")
    if boundary > 0:
        window = window[:boundary]
    return window.rstrip(" ,;:") + "…"


# --- Filing selection --------------------------------------------------------


def _latest_annual(
    manifest: Sequence[FilingRef], filer_type: FilerType
) -> FilingRef | None:
    """The most recently filed annual report for this filer type."""
    forms = _annual_forms_for(filer_type)
    candidates = [filing for filing in manifest if filing.base_form in forms]
    if not candidates:
        return None
    return max(candidates, key=lambda f: (f.filed_date, f.accession_no))


def _annual_forms_for(filer_type: FilerType) -> frozenset[str]:
    """Every annual form this filer type might have filed.

    Deliberately generous, and for the same reason as m03's copy: a company
    that migrated from 20-F to 10-K still has its older annual reports in the
    manifest, and either is a valid anchor.
    """
    if filer_type is FilerType.DOMESTIC:
        return frozenset({"10-K"})
    if filer_type is FilerType.CANADIAN:
        return frozenset({"40-F", "20-F"})
    return frozenset({"20-F", "40-F"})


async def _filing_text(url: str, accession_no: str) -> str:
    """The readable text of one document, or an empty string.

    A document that cannot be fetched or parsed costs the narrative sections.
    It never costs the report, which is why nothing here raises.

    A document over the parse ceiling is windowed rather than abandoned. It
    used to return nothing at all, which cost every narrative item on filers
    whose annual report happened to be large — a strictly worse answer than
    reading the part that fits. The narrative items sit in a 10-K's front
    matter, ahead of the financial statements and exhibits that make these
    documents large, so the leading window is where they are. The cut is
    reported, never silent.
    """
    try:
        body = await edgar.get_client().get_bytes(url)
    except EdgarError as cause:
        logger.warning(
            "Could not read a document for narrative extraction",
            extra={
                "accession_no": accession_no,
                "url": url,
                "error": str(cause),
            },
        )
        return ""

    if len(body) > MAX_FILING_TEXT_BYTES:
        logger.warning(
            "Document is larger than the parse ceiling; reading the leading window",
            extra={
                "accession_no": accession_no,
                "url": url,
                "bytes": len(body),
                "window_bytes": NARRATIVE_OVERSIZE_WINDOW_BYTES,
            },
        )
        body = body[:NARRATIVE_OVERSIZE_WINDOW_BYTES]

    return document.html_to_text(body.decode("utf-8", errors="replace"))
