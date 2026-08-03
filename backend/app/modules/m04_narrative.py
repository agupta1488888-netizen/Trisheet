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
    AMENDMENT_FORM_SUFFIX,
    ISOLATED_HEADING_SLACK_CHARS,
    MAX_FILING_TEXT_BYTES,
    MAX_RISK_ITEMS,
    NARRATIVE_MAX_SECTION_CHARS,
    NARRATIVE_MIN_SECTION_CHARS,
    NARRATIVE_SPECS,
    NARRATIVE_SUMMARY_CHARS,
    NARRATIVE_TRUNCATION_NOTE,
    RISK_HEADING_MARKERS,
    RISK_HEADING_MAX_CHARS,
    RISK_HEADING_MIN_CHARS,
    NarrativeSpec,
)
from app.models import (
    Company,
    ExtractionMethod,
    Fact,
    FilerType,
    Filing,
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
    company: Company, manifest: list[Filing]
) -> list[Fact]:
    """Extracts the annual report's narrative items as Tier 1 textual facts.

    Args:
        company: The filer. Its type decides which form is the annual report
            and therefore which item numbering applies — a 20-F is not
            searched for an Item 1A that it does not have.
        manifest: Filings from m02.

    Returns:
        One fact per item located. Empty when there is no annual report in the
        manifest, when it could not be fetched, or when none of its items could
        be located — each of which is logged and none of which raises.
    """
    annual = _latest_annual(manifest, company.filer_type)
    if annual is None:
        logger.info(
            "No annual report in the manifest; narrative skipped",
            extra={"cik": company.cik, "manifest_size": len(manifest)},
        )
        return []

    text = await _filing_text(annual)
    if not text:
        return []

    base_form = annual.form.split(AMENDMENT_FORM_SUFFIX, 1)[0]
    specs = [spec for spec in NARRATIVE_SPECS if base_form in spec.forms]

    facts: list[Fact] = []
    for spec in specs:
        body = _locate(text, spec)
        if body is None:
            logger.info(
                "Narrative item not found in the filing",
                extra={
                    "cik": company.cik,
                    "metric": spec.metric,
                    "form": annual.form,
                    "accession_no": annual.accession_no,
                },
            )
            continue

        fact = _build_fact(spec, body, annual)
        if fact is not None:
            facts.append(fact)

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
    spec: NarrativeSpec, body: str, filing: Filing
) -> Fact | None:
    """One textual fact for a located item, or None if it cannot be sound.

    The whole item is the display value: m10 is shown the filer's words and
    restates them, and m11 checks any figure in that prose against the fact
    store like any other. A fact that will not validate is dropped rather than
    patched — an unsourced quotation is worse than a missing section.
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
            source_url=str(filing.primary_doc_url),
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
    manifest: Sequence[Filing], filer_type: FilerType
) -> Filing | None:
    """The most recently filed annual report for this filer type."""
    forms = _annual_forms_for(filer_type)
    candidates = [
        filing
        for filing in manifest
        if filing.form.split(AMENDMENT_FORM_SUFFIX, 1)[0] in forms
    ]
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


async def _filing_text(filing: Filing) -> str:
    """The readable text of the annual report, or an empty string.

    A document that cannot be fetched or parsed costs the narrative sections.
    It never costs the report, which is why nothing here raises.
    """
    url = str(filing.primary_doc_url)
    try:
        body = await edgar.get_client().get_bytes(url)
    except EdgarError as cause:
        logger.warning(
            "Could not read the annual report for narrative extraction",
            extra={
                "accession_no": filing.accession_no,
                "url": url,
                "error": str(cause),
            },
        )
        return ""

    if len(body) > MAX_FILING_TEXT_BYTES:
        logger.warning(
            "Annual report is larger than the parse ceiling; narrative skipped",
            extra={"accession_no": filing.accession_no, "bytes": len(body)},
        )
        return ""

    return document.html_to_text(body.decode("utf-8", errors="replace"))
