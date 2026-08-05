"""m08 — peer selection.

Responsibility
    Choose comparable companies by a ladder of decreasing authority, and say
    which rung answered for each one.

The ladder
    1. The compensation peer group in the latest DEF 14A. A compensation
       committee that benchmarks pay against a named group has stated, on the
       record, who it believes its peers are. Nothing this system could infer
       beats that.
    2. The competition discussion in the latest annual report. The filer names
       who it competes with, in its own words.
    3. SIC match. EDGAR's own classification of the filer's industry, queried
       for other filers carrying the same code.
    4. The model, as a last resort, asked for tickers and nothing else.

    The ladder stops as soon as `PEER_TARGET_COUNT` peers have been accepted,
    so a filer that names its own peer group never reaches a model.

Two rules hold at every rung
    Every candidate must resolve to a live CIK before inclusion. A name
    scraped out of a proxy is a string until the SEC ticker index says it is a
    filer; one that does not resolve is dropped and logged. The model's
    suggestions go through exactly the same gate, which is what stops a
    plausible-sounding hallucination from reaching the page.

    Every peer records how it was selected. `Peer.selection_method` and
    `Peer.selection_note` travel with the peer into the report, because a
    comparison against a self-declared peer group is a different claim from a
    comparison against whatever else shares a SIC code.

Fiscal year mismatches
    Two companies that close their books six months apart are not comparable
    like for like. Every peer's fiscal year end is read from EDGAR and compared
    with the subject's; a material difference sets `fiscal_year_mismatch` and
    is disclosed in the peer's note, never silently absorbed.

Nothing is hardcoded to a company
    There are no company-specific peer lists anywhere. Every rung derives its
    candidates from the filer's own disclosure or its own SIC code.

Degradation
    A rung that fails costs its candidates, not the ladder. Nothing in the
    public interface raises.

Public interface
    select_peers(company, manifest, client) -> PeerSet
    peer_facts(peer_set) -> list[Fact]
    build_peer_comparison(company, peer_set, subject_facts, client) -> PeerComparison
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from lxml import etree
from pydantic import ValidationError

from app.config import (
    AMENDMENT_FORM_SUFFIX,
    ANALYSIS_ZERO_TOLERANCE,
    ANNUAL_FORM_TO_FILER_TYPE,
    ATOM_NAMESPACE,
    COMPETITION_MARKERS,
    COMPETITOR_MAX_COUNT,
    COMPETITOR_NAME_MAX_CHARS,
    COMPETITOR_NAME_MIN_CHARS,
    CURRENCY_DECIMAL_PLACES,
    DAYS_IN_YEAR,
    DISPLAY_SCALE_DIVISOR,
    FISCAL_YEAR_END_TOLERANCE_DAYS,
    MAX_FILING_TEXT_BYTES,
    PEER_COMPARISON_MAX_COUNT,
    PEER_CONTEXT_WINDOW_CHARS,
    PEER_GROUP_MARKERS,
    PEER_LLM_MAX_SUGGESTIONS,
    PEER_MAX_COUNT,
    PEER_MAX_NAMES_PER_DOCUMENT,
    PEER_NAME_MAX_WORDS,
    PEER_NAME_MIN_CHARS,
    PEER_NAME_STOPWORDS,
    PEER_TARGET_COUNT,
    PERCENT_DECIMAL_PLACES,
    PERCENT_DISPLAY_TEMPLATE,
    PROXY_FORM,
    RATIO_DECIMAL_PLACES,
    SIC_BROWSE_PAGE_SIZE,
    SIC_BROWSE_TTL_SECONDS,
    TIMES_DISPLAY_TEMPLATE,
    UNIT_PERCENT,
    UNIT_TIMES,
)
from app.models import (
    Company,
    ExtractionMethod,
    Fact,
    FilerType,
    FilingRef,
    Peer,
    PeerSelectionMethod,
    PeerSet,
    SourceNote,
    SourceTier,
    SourceType,
)
from app.modules import m02_discovery, m03_financials, m05_market
from app.modules.m01_resolver import (
    IndexEntry,
    load_company,
    load_index,
    normalise_name,
    normalise_name_exact,
)
from app.modules.m07_analysis import PERCENT_SCALE, compute_derived_metrics
from app.services import document, llm
from app.services.edgar import (
    EdgarClient,
    EdgarError,
    company_list_by_sic_url,
    filing_index_url,
    pad_cik,
    submissions_url,
)

logger = logging.getLogger(__name__)

#: A token that could be part of a company name. Names carry ampersands,
#: full stops and hyphens, so those travel with the token.
_NAME_TOKEN = re.compile(r"[A-Za-z0-9&.'\-]+")

#: Lowercase words that appear inside a company name without breaking it.
_NAME_CONNECTORS = frozenset({"and", "of", "de", "van", "der", "for", "the"})

#: Metric carrying one peer. The ticker is part of the metric because a report
#: holds several peers at once, and a fact's identity is its metric and period
#: — two peers sharing a metric would be one fact overwriting another.
PEER_METRIC_TEMPLATE = "peer.company.{ticker}"

#: A competitor is keyed by its name, not a ticker: most of the ones a filer
#: names have no ticker, which is the whole reason they are kept separately.
COMPETITOR_METRIC_TEMPLATE = "competitor.named.{slug}"

#: What each rung is called when the reason is written for a reader.
_METHOD_PHRASES: dict[PeerSelectionMethod, str] = {
    PeerSelectionMethod.COMPENSATION_PEER_GROUP: (
        "named in the compensation peer group of the {form} filed {date}"
    ),
    PeerSelectionMethod.COMPETITION_PARAGRAPH: (
        "named in the competition discussion of the {form} filed {date}"
    ),
    PeerSelectionMethod.SIC_MATCH: (
        "shares SIC code {sic}, per EDGAR's classification"
    ),
    PeerSelectionMethod.LLM_SUGGESTION: (
        "suggested as a comparable and confirmed to be a live SEC filer"
    ),
}


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One resolved candidate: a live filer, and where the name came from."""

    entry: IndexEntry
    method: PeerSelectionMethod
    source_url: str | None = None
    accession_no: str | None = None
    filed_date: dt.date | None = None
    source_form: str | None = None


@dataclass(frozen=True, slots=True)
class _Profile:
    """What EDGAR's submissions document says about a candidate filer.

    Fetched once per accepted peer. Confirms the CIK is live and supplies the
    fiscal year end the mismatch check needs.
    """

    cik: str
    name: str
    sic_code: str | None
    fiscal_year_end: str | None
    filer_type: FilerType | None
    latest_accession_no: str | None
    latest_filed_date: dt.date | None


# --- Name index -------------------------------------------------------------


class _NameIndex:
    """The SEC ticker index, addressable by normalised name and by CIK.

    This is the gate every candidate passes through. A name that is not in
    here is not a live filer with a ticker, and does not become a peer.
    """

    def __init__(self, entries: Sequence[IndexEntry]) -> None:
        self._by_name: dict[str, IndexEntry] = {}
        self._by_exact_name: dict[str, IndexEntry] = {}
        self._ambiguous: set[str] = set()
        self._by_cik: dict[str, IndexEntry] = {}
        self._by_ticker: dict[str, IndexEntry] = {}

        for entry in entries:
            exact = normalise_name_exact(entry.name)
            if exact:
                self._by_exact_name.setdefault(exact, entry)

            normalised = normalise_name(entry.name)
            if normalised:
                held = self._by_name.get(normalised)
                if held is None:
                    self._by_name[normalised] = entry
                elif held.cik != entry.cik:
                    # Two different filers reduce to the same stripped name —
                    # "Target Corporation" and "Target Group Inc." both become
                    # "target". Whichever the index happened to list first
                    # would otherwise shadow the other silently, which is how
                    # an OTC shell came to stand in for a retailer.
                    self._ambiguous.add(normalised)

            # A filer with several share classes appears more than once; the
            # shortest ticker is the one a reader recognises.
            by_cik = self._by_cik.get(entry.cik)
            if by_cik is None or len(entry.ticker) < len(by_cik.ticker):
                self._by_cik[entry.cik] = entry
            self._by_ticker.setdefault(entry.ticker.upper(), entry)

    def by_name(self, name: str) -> IndexEntry | None:
        """The filer of that name, or None if there is not exactly one.

        The length and stopword guards are applied to the stripped form first,
        so a filer whose name reduces to an ordinary English word is refused
        however it was written. Only then is the full name tried, ahead of the
        stripped one, so a prose mention carrying the suffix resolves to the
        filer that actually bears it. A stripped name shared by more than one
        filer resolves to nothing at all: picking one would be a guess, and a
        guess here puts a different company on the page under the right name.
        """
        normalised = normalise_name(name)
        if len(normalised) < PEER_NAME_MIN_CHARS:
            return None
        if normalised in PEER_NAME_STOPWORDS:
            return None

        exact = self._by_exact_name.get(normalise_name_exact(name))
        if exact is not None:
            return exact

        if normalised in self._ambiguous:
            logger.info(
                "Peer name is ambiguous and was not resolved",
                # Not "name": logging reserves that attribute on a record.
                extra={"peer_name": name, "normalised": normalised},
            )
            return None
        return self._by_name.get(normalised)

    def by_cik(self, cik: str) -> IndexEntry | None:
        try:
            return self._by_cik.get(pad_cik(cik))
        except ValueError:
            return None

    def by_ticker(self, ticker: str) -> IndexEntry | None:
        return self._by_ticker.get(ticker.strip().upper())


def find_company_names(text: str, index: _NameIndex) -> list[IndexEntry]:
    """Every live filer named in a passage of filing prose, in order.

    Company names are found by matching runs of capitalised words against the
    ticker index, longest run first — so "First Solar" resolves to First Solar
    rather than to a filer called "First". A run that matches nothing is not a
    company name and is discarded; there is no fuzzy fallback, because a
    near-match on a company name is a different company.

    Public for testing: this is the step that decides who appears on the page,
    and it is worth being able to exercise without a network.
    """
    found: list[IndexEntry] = []
    seen: set[str] = set()

    for run in _capitalised_runs(text):
        position = 0
        while position < len(run):
            matched = False
            longest = min(PEER_NAME_MAX_WORDS, len(run) - position)
            for length in range(longest, 0, -1):
                phrase = " ".join(run[position : position + length])
                entry = index.by_name(phrase)
                if entry is None:
                    continue
                if entry.cik not in seen:
                    seen.add(entry.cik)
                    found.append(entry)
                position += length
                matched = True
                break
            if not matched:
                position += 1

        if len(found) >= PEER_MAX_NAMES_PER_DOCUMENT:
            break

    return found


def _capitalised_runs(text: str) -> list[list[str]]:
    """Runs of consecutive capitalised tokens, which is what names look like.

    Connectors ("and", "of") are kept inside a run so that "Bath and Body
    Works" survives, but a run may not start or end on one.
    """
    runs: list[list[str]] = []
    current: list[str] = []

    for token in _NAME_TOKEN.findall(text):
        is_name_word = token[:1].isupper() and any(c.isalpha() for c in token)
        is_connector = token.lower() in _NAME_CONNECTORS

        if is_name_word or (is_connector and current):
            current.append(token)
            continue

        if current:
            runs.append(_trim_connectors(current))
            current = []

    if current:
        runs.append(_trim_connectors(current))

    return [run for run in runs if run]


def _trim_connectors(run: list[str]) -> list[str]:
    """Drops leading and trailing connectors, which are never a name's edge."""
    start, end = 0, len(run)
    while start < end and run[start].lower() in _NAME_CONNECTORS:
        start += 1
    while end > start and run[end - 1].lower() in _NAME_CONNECTORS:
        end -= 1
    return run[start:end]


# --- Filing text ------------------------------------------------------------


def _latest_of_form(
    manifest: Sequence[FilingRef], forms: frozenset[str]
) -> FilingRef | None:
    """The most recently filed of a set of forms, amendments included."""
    candidates = [
        filing
        for filing in manifest
        if filing.form.split(AMENDMENT_FORM_SUFFIX, 1)[0] in forms
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda f: (f.filed_date, f.accession_no))


def _annual_forms_for(filer_type: FilerType) -> frozenset[str]:
    """Annual forms this filer type might have filed.

    Generous on purpose: a company that migrated between regimes still has its
    older annual reports in the manifest, and either is a valid anchor.
    """
    if filer_type is FilerType.DOMESTIC:
        return frozenset({"10-K"})
    if filer_type is FilerType.CANADIAN:
        return frozenset({"40-F", "20-F"})
    return frozenset({"20-F", "40-F"})


async def _filing_text(client: EdgarClient, filing: FilingRef) -> str:
    """The readable text of a filing's primary document, or an empty string.

    A proxy or annual report that cannot be fetched or parsed costs the rung
    that wanted it. It never costs the report.
    """
    url = str(filing.primary_doc_url)
    try:
        body = await client.get_bytes(url)
    except EdgarError:
        logger.warning(
            "Could not read filing for peer selection",
            extra={"accession_no": filing.accession_no, "url": url},
        )
        return ""

    if len(body) > MAX_FILING_TEXT_BYTES:
        logger.warning(
            "Filing is larger than the parse ceiling; peer rung skipped",
            extra={"accession_no": filing.accession_no, "bytes": len(body)},
        )
        return ""

    return document.html_to_text(body.decode("utf-8", errors="replace"))


async def _candidates_from_filing(
    client: EdgarClient,
    filing: FilingRef,
    markers: tuple[str, ...],
    method: PeerSelectionMethod,
    index: _NameIndex,
    subject_cik: str,
) -> list[_Candidate]:
    """Company names in the marked passages of one filing.

    Only the windows around a marker are scanned. A company mentioned in a
    ninety-page proxy is not thereby a peer; a company named beside the words
    "compensation peer group" is.
    """
    text = await _filing_text(client, filing)
    if not text:
        return []

    windows = document.windows_around(
        text, markers, PEER_CONTEXT_WINDOW_CHARS
    )
    if not windows:
        logger.info(
            "Filing does not discuss this topic; rung produced nothing",
            extra={"accession_no": filing.accession_no, "method": str(method)},
        )
        return []

    candidates: list[_Candidate] = []
    seen: set[str] = set()

    for window in windows:
        for entry in find_company_names(window, index):
            if entry.cik == subject_cik or entry.cik in seen:
                continue
            seen.add(entry.cik)
            candidates.append(
                _Candidate(
                    entry=entry,
                    method=method,
                    source_url=str(filing.primary_doc_url),
                    accession_no=filing.accession_no,
                    filed_date=filing.filed_date,
                    source_form=filing.form,
                )
            )

    logger.info(
        "Peer candidates read from filing",
        extra={
            "accession_no": filing.accession_no,
            "method": str(method),
            "windows": len(windows),
            "candidates": len(candidates),
        },
    )
    return candidates


# --- Competitors named in the filing ----------------------------------------


@dataclass(frozen=True, slots=True)
class NamedCompetitors:
    """Competitors the filer named, and the filing it named them in.

    Separate from `Peer` on purpose. A `Peer` is a live SEC filer whose own
    statements the comparison table reads, so it must carry a CIK. A competitor
    is whoever the filer said it competes with — adidas and Puma for Nike — and
    most of the interesting ones never file with the SEC at all. Requiring a
    CIK here would discard exactly the names the reader asked for.

    What this carries instead is the filing's provenance, which is complete:
    the passage came from a 10-K, so the accession number and filing date are
    real. These are Tier 1 statements, and they contain no figures.
    """

    names: tuple[str, ...] = ()
    source_url: str | None = None
    accession_no: str | None = None
    filed_date: dt.date | None = None
    source_form: str | None = None


_COMPETITOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "names": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Company names copied verbatim from the passage. No figures, "
                "no descriptions."
            ),
        }
    },
    "required": ["names"],
    "additionalProperties": False,
}

_COMPETITOR_SYSTEM_PROMPT = (
    "You extract company names from a passage of a company's own annual "
    "report. You copy names that appear in the passage; you never add a "
    "company from your own knowledge, however obvious it seems, and you never "
    "return a figure, a description or a judgement. Every name you return is "
    "checked against the passage before it is used, and one that does not "
    "appear there is discarded — so adding a name gains the answer nothing. "
    "Return the names exactly as the passage spells them, including lower "
    "case brand styling. Returning fewer is correct; returning an empty list "
    "is correct when the passage names no competitor."
)


def _competitor_slug(name: str) -> str:
    """A stable metric suffix for a competitor name."""
    normalised = normalise_name_exact(name)
    return "_".join(part for part in normalised.split(" ") if part)


async def find_named_competitors(
    client: EdgarClient, filing: FilingRef, subject: Company
) -> NamedCompetitors:
    """The competitors a filer names in its own annual report.

    Reads the same competition passage the peer ladder's second rung scans, and
    keeps the names that rung has to throw away — the ones that do not resolve
    to a US ticker, which is most of the ones a reader would recognise.

    The model is used to read the passage, not to recall anything. Every name
    it returns must appear in the passage it was shown, checked here in code:
    a name the filing does not contain is dropped whatever produced it. That
    check is what makes this extraction rather than the model's own knowledge,
    and it is the same reasoning CLAUDE.md gives for tier enforcement — a rule
    the code applies, not one the prompt requests.

    Never raises. A filing that cannot be read, a passage that names nobody,
    and an unconfigured model all produce no competitors and no report failure.
    """
    if not llm.is_configured():
        logger.info(
            "No model configured; no competitors are read from the filing",
            extra={"cik": subject.cik},
        )
        return NamedCompetitors()

    text = await _filing_text(client, filing)
    if not text:
        return NamedCompetitors()

    windows = document.windows_around(
        text, COMPETITION_MARKERS, PEER_CONTEXT_WINDOW_CHARS
    )
    if not windows:
        logger.info(
            "Annual report does not discuss competition; no competitors read",
            extra={"accession_no": filing.accession_no},
        )
        return NamedCompetitors()

    passage = "\n\n".join(windows)
    user_prompt = (
        f"The company is {subject.name}.\n\n"
        "Below is a passage from its annual report discussing competition. "
        "Return the names of the competitors the passage names. Do not "
        "include the company itself. Copy each name exactly as written.\n\n"
        f"Passage:\n{passage}"
    )

    try:
        answer = await llm.complete_json(
            _COMPETITOR_SYSTEM_PROMPT,
            user_prompt,
            _COMPETITOR_SCHEMA,
            purpose="peers:competitors",
        )
    except llm.LlmError as cause:
        logger.warning(
            "The model did not answer when reading competitors",
            extra={"cik": subject.cik, "error": str(cause)},
        )
        return NamedCompetitors()

    raw = answer.get("names")
    if not isinstance(raw, list):
        return NamedCompetitors()

    names = _kept_competitor_names(raw, passage, subject)
    if not names:
        return NamedCompetitors()

    logger.info(
        "Competitors read from the annual report",
        extra={
            "cik": subject.cik,
            "accession_no": filing.accession_no,
            "competitors": len(names),
        },
    )
    return NamedCompetitors(
        names=names,
        source_url=str(filing.primary_doc_url),
        accession_no=filing.accession_no,
        filed_date=filing.filed_date,
        source_form=filing.form,
    )


def _kept_competitor_names(
    raw: Sequence[object], passage: str, subject: Company
) -> tuple[str, ...]:
    """The returned names that the passage actually contains.

    Public behaviour worth stating: a name absent from the passage is dropped
    and counted, never trimmed to fit or matched approximately. A near match on
    a company name is a different company.
    """
    haystack = passage.casefold()
    subject_key = normalise_name(subject.name)

    kept: list[str] = []
    invented: list[str] = []
    seen: set[str] = set()

    for item in raw:
        if not isinstance(item, str):
            continue
        name = " ".join(item.split())
        if not (
            COMPETITOR_NAME_MIN_CHARS <= len(name) <= COMPETITOR_NAME_MAX_CHARS
        ):
            continue
        if name.casefold() not in haystack:
            invented.append(name)
            continue

        key = normalise_name(name)
        if not key or key == subject_key or key in seen:
            continue
        seen.add(key)
        kept.append(name)
        if len(kept) >= COMPETITOR_MAX_COUNT:
            break

    if invented:
        logger.warning(
            "Names not present in the passage were dropped",
            extra={"cik": subject.cik, "dropped": invented},
        )

    return tuple(kept)


_WEB_COMPETITOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "competitors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "source_url": {
                        "type": "string",
                        "description": "The page this was read from.",
                    },
                },
                "required": ["name", "source_url"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["competitors"],
    "additionalProperties": False,
}

_WEB_COMPETITOR_SYSTEM_PROMPT = (
    "You identify a company's competitors using web search. You return "
    "company names and the page each was read from, and nothing else: no "
    "figures, no market shares, no rankings, no reasoning. Every answer you "
    "give is shown to the reader labelled as unverified and not taken from a "
    "filing, so accuracy matters more than completeness. Return only "
    "competitors you found stated on a page you searched. Returning fewer is "
    "correct; returning an empty list is correct when the search does not "
    "support an answer."
)


async def find_web_competitors(company: Company) -> tuple[SourceNote, ...]:
    """Competitors read off the open web, for a filer that names none.

    The fallback, and only the fallback. A company that names its competitors
    in its own annual report has said something on the record, and nothing
    found by search improves on that. This runs when the filing named nobody —
    Apple's competition section, for instance, describes competition for two
    pages without naming a single company.

    What comes back is a `SourceNote`, never a `Fact`. A web page has no
    accession number and no filing date, and inventing either so the field
    could be populated would be fabricating provenance. `is_user_supplied` is
    False, which is what lets the interface say where these came from rather
    than implying the reader supplied them.

    Never raises. No model, no search result and no answer all produce nothing.
    """
    if not llm.is_configured():
        return ()

    user_prompt = (
        f"Company: {company.name} (ticker {company.ticker}).\n"
        f"Industry, as classified by the SEC: "
        f"{company.sector or 'not disclosed'}.\n"
        f"Return at most {COMPETITOR_MAX_COUNT} companies that compete with "
        "it in its product markets. Include companies listed outside the "
        "United States and companies that are privately held."
    )

    try:
        answer = await llm.complete_json_with_web_search(
            _WEB_COMPETITOR_SYSTEM_PROMPT,
            user_prompt,
            _WEB_COMPETITOR_SCHEMA,
            purpose="peers:competitors_web",
        )
    except llm.LlmError as cause:
        logger.warning(
            "The model did not answer when searching for competitors",
            extra={"cik": company.cik, "error": str(cause)},
        )
        return ()

    raw = answer.get("competitors")
    if not isinstance(raw, list):
        return ()

    fetched = dt.datetime.now(dt.UTC)
    subject_key = normalise_name(company.name)
    # "Apple Inc." already ends the sentence. Appending a full stop to a name
    # that carries one reads as a typo on the page.
    subject = company.name.rstrip(".")
    notes: list[SourceNote] = []
    seen: set[str] = set()

    for item in raw[:COMPETITOR_MAX_COUNT]:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("name", "")).split())
        url = str(item.get("source_url", "")).strip()
        if not (
            COMPETITOR_NAME_MIN_CHARS <= len(name) <= COMPETITOR_NAME_MAX_CHARS
        ):
            continue

        key = normalise_name(name)
        if not key or key == subject_key or key in seen:
            continue

        try:
            notes.append(
                SourceNote(
                    text=f"{name} competes with {subject}.",
                    source_url=url,
                    source_type=SourceType.NEWS,
                    tier=SourceTier.NEWS,
                    fetched_at=fetched,
                    is_user_supplied=False,
                )
            )
        except ValidationError as cause:
            # Usually an unusable URL. The claim is dropped rather than kept
            # without the page it came from.
            logger.warning(
                "Web competitor could not be expressed as a sourced note",
                extra={"competitor_name": name, "error": str(cause)},
            )
            continue
        seen.add(key)

    logger.info(
        "Competitors read from the web because the filing named none",
        extra={"cik": company.cik, "competitors": len(notes)},
    )
    return tuple(notes)


async def read_named_competitors(
    company: Company, manifest: Sequence[FilingRef], client: EdgarClient
) -> NamedCompetitors:
    """The competitors named in this filer's latest annual report.

    The public entry point. Picks the annual form this filer actually files —
    a 10-K, or a 20-F/40-F for a foreign private issuer — so the passage read
    is the right one without the caller knowing which form that is.
    """
    annual = _latest_of_form(manifest, _annual_forms_for(company.filer_type))
    if annual is None:
        return NamedCompetitors()
    return await find_named_competitors(client, annual, company)


def competitor_facts(found: NamedCompetitors) -> list[Fact]:
    """One fact per named competitor, sourced to the filing that named it.

    The fact carries a name and no figure. `value` is None and there is no
    unit, because a competitor is a statement the filer made, not a quantity —
    nothing here can reach an arithmetic check or a financial table.
    """
    if not found.names or found.accession_no is None:
        return []

    filed = found.filed_date or dt.date.today()
    facts: list[Fact] = []

    for name in found.names:
        slug = _competitor_slug(name)
        if not slug:
            continue
        try:
            facts.append(
                Fact(
                    metric=COMPETITOR_METRIC_TEMPLATE.format(slug=slug),
                    label="Competitor",
                    value=None,
                    display_value=name,
                    unit=None,
                    period_start=None,
                    period_end=filed,
                    fiscal_year=None,
                    tier=SourceTier.FILING,
                    source_type=SourceType.SEC_FILING,
                    source_url=found.source_url,
                    accession_no=found.accession_no,
                    filed_date=filed,
                    extraction_method=ExtractionMethod.NARRATIVE,
                    confidence=1.0,
                )
            )
        except ValueError as cause:
            logger.warning(
                "Competitor could not be expressed as a sourced fact",
                extra={"competitor_name": name, "error": str(cause)},
            )

    return facts


# --- Rung 3: SIC match ------------------------------------------------------


async def _candidates_from_sic(
    client: EdgarClient,
    company: Company,
    index: _NameIndex,
) -> list[_Candidate]:
    """Filers sharing the subject's SIC code, per EDGAR's own classification.

    Every CIK EDGAR returns is looked up in the ticker index: a filer with no
    live ticker is a registrant, not a comparable, and is dropped.
    """
    if not company.sic_code:
        logger.info(
            "Filer has no SIC code; the industry rung cannot run",
            extra={"cik": company.cik},
        )
        return []

    annual_form = next(
        (
            form
            for form, filer_type in ANNUAL_FORM_TO_FILER_TYPE.items()
            if filer_type == str(company.filer_type)
        ),
        "10-K",
    )

    try:
        url = company_list_by_sic_url(
            company.sic_code, annual_form, SIC_BROWSE_PAGE_SIZE
        )
    except ValueError:
        logger.warning(
            "SIC code is not numeric; the industry rung cannot run",
            extra={"cik": company.cik, "sic_code": company.sic_code},
        )
        return []

    try:
        body = await client.get_bytes(url, ttl=SIC_BROWSE_TTL_SECONDS)
    except EdgarError:
        logger.warning(
            "EDGAR's company browser did not answer",
            extra={"cik": company.cik, "sic_code": company.sic_code},
        )
        return []

    candidates: list[_Candidate] = []
    for cik in _ciks_from_atom(body):
        if cik == company.cik:
            continue
        entry = index.by_cik(cik)
        if entry is None:
            # A filer with no live ticker is not a comparable.
            continue
        candidates.append(
            _Candidate(
                entry=entry,
                method=PeerSelectionMethod.SIC_MATCH,
                source_url=url,
            )
        )

    logger.info(
        "Peer candidates read from SIC classification",
        extra={
            "cik": company.cik,
            "sic_code": company.sic_code,
            "candidates": len(candidates),
        },
    )
    return candidates


def _ciks_from_atom(body: bytes) -> list[str]:
    """CIKs in an EDGAR company-browser Atom response, in the order given."""
    try:
        parser = etree.XMLParser(
            resolve_entities=False, no_network=True, recover=True
        )
        root = etree.fromstring(body, parser=parser)
    except etree.XMLSyntaxError:
        logger.warning("EDGAR's company browser returned unparseable XML")
        return []
    if root is None:
        return []

    ciks: list[str] = []
    for element in root.iter(f"{{{ATOM_NAMESPACE}}}CIK"):
        text = (element.text or "").strip()
        if not text:
            continue
        try:
            ciks.append(pad_cik(text))
        except ValueError:
            continue

    if ciks:
        return ciks

    # Older responses carry the CIK only in the entry link's query string.
    for element in root.iter(f"{{{ATOM_NAMESPACE}}}link"):
        href = element.get("href") or ""
        match = re.search(r"CIK=(\d{1,10})", href)
        if match:
            try:
                ciks.append(pad_cik(match.group(1)))
            except ValueError:
                continue
    return ciks


# --- Rung 4: the model ------------------------------------------------------

_PEER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tickers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "US-listed ticker symbols only.",
        }
    },
    "required": ["tickers"],
    "additionalProperties": False,
}

_PEER_SYSTEM_PROMPT = (
    "You name comparable public companies. You return ticker symbols and "
    "nothing else: no figures, no descriptions, no reasoning. Every ticker "
    "you return is independently verified against the SEC's register before "
    "it is used, and an unverifiable ticker is discarded — so a guess costs "
    "the answer nothing and gains it nothing. Return only companies you are "
    "confident are listed on a US exchange. Returning fewer is correct; "
    "returning an empty list is correct when you are not confident."
)


async def _candidates_from_model(
    company: Company, index: _NameIndex
) -> list[_Candidate]:
    """Tickers proposed by the model, kept only if they resolve to a filer.

    The model is asked for ticker symbols and nothing else — it is never asked
    for a figure, a description or a judgement, and nothing it returns is used
    without the SEC's register agreeing that the company exists.
    """
    if not llm.is_configured():
        logger.info(
            "No model configured; the last rung of the peer ladder is skipped",
            extra={"cik": company.cik},
        )
        return []

    sector = company.sector or "an undisclosed industry"
    user_prompt = (
        f"Company: {company.name} (ticker {company.ticker}).\n"
        f"Industry, as classified by the SEC: {sector}.\n"
        f"Return at most {PEER_LLM_MAX_SUGGESTIONS} ticker symbols of "
        "US-listed companies that a research analyst would compare it against. "
        "Do not include the company itself."
    )

    try:
        answer = await llm.complete_json(
            _PEER_SYSTEM_PROMPT,
            user_prompt,
            _PEER_SCHEMA,
            purpose="peers:suggest",
        )
    except llm.LlmError as cause:
        logger.warning(
            "The model did not answer for peer selection",
            extra={"cik": company.cik, "error": str(cause)},
        )
        return []

    raw = answer.get("tickers")
    if not isinstance(raw, list):
        return []

    candidates: list[_Candidate] = []
    dropped: list[str] = []
    seen: set[str] = set()

    for item in raw[:PEER_LLM_MAX_SUGGESTIONS]:
        if not isinstance(item, str):
            continue
        entry = index.by_ticker(item)
        if entry is None:
            # The register does not know this ticker, so neither does a report.
            dropped.append(item.strip().upper())
            continue
        if entry.cik == company.cik or entry.cik in seen:
            continue
        seen.add(entry.cik)
        candidates.append(
            _Candidate(entry=entry, method=PeerSelectionMethod.LLM_SUGGESTION)
        )

    if dropped:
        logger.warning(
            "Suggested tickers did not resolve to a live filer and were dropped",
            extra={"cik": company.cik, "dropped": dropped},
        )

    return candidates


# --- Profiles and fiscal year ----------------------------------------------


async def _load_profile(client: EdgarClient, cik: str) -> _Profile | None:
    """Reads a candidate's submissions document.

    This is the confirmation that a candidate is a live CIK: a filer EDGAR has
    no submissions document for does not become a peer, whichever rung named
    it. It also supplies the fiscal year end the mismatch check needs.
    """
    try:
        submissions = await client.get_json(submissions_url(cik))
    except EdgarError:
        logger.warning("Could not confirm peer against EDGAR", extra={"cik": cik})
        return None

    filings = submissions.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    recent_map = recent if isinstance(recent, dict) else {}

    accession, filed, filer_type = _latest_filing_of(recent_map)
    sic = submissions.get("sic")
    fiscal_year_end = submissions.get("fiscalYearEnd")

    return _Profile(
        cik=pad_cik(cik),
        name=str(submissions.get("name", "")),
        sic_code=str(sic) if sic else None,
        fiscal_year_end=str(fiscal_year_end) if fiscal_year_end else None,
        filer_type=filer_type,
        latest_accession_no=accession,
        latest_filed_date=filed,
    )


def _latest_filing_of(
    recent: dict[str, Any],
) -> tuple[str | None, dt.date | None, FilerType | None]:
    """The most recent filing in a submissions block, and the filer type.

    The filer type comes from the most recent annual form present, which is
    the same rule m01 applies — a company is what it files now.
    """
    forms = _column(recent, "form")
    accessions = _column(recent, "accessionNumber")
    dates = _column(recent, "filingDate")

    latest_accession: str | None = None
    latest_date: dt.date | None = None
    filer_type: FilerType | None = None
    annual_date: str = ""

    for index, form in enumerate(forms):
        accession = accessions[index] if index < len(accessions) else ""
        filed_raw = dates[index] if index < len(dates) else ""
        filed = _parse_date(filed_raw)

        if accession and filed is not None and (
            latest_date is None or filed > latest_date
        ):
            latest_accession, latest_date = accession, filed

        stem = form.split(AMENDMENT_FORM_SUFFIX, 1)[0].strip()
        if stem in ANNUAL_FORM_TO_FILER_TYPE and filed_raw > annual_date:
            annual_date = filed_raw
            filer_type = FilerType(ANNUAL_FORM_TO_FILER_TYPE[stem])

    return latest_accession, latest_date, filer_type


def _column(block: dict[str, Any], key: str) -> list[str]:
    column = block.get(key)
    if not isinstance(column, list):
        return []
    return [str(item) if item is not None else "" for item in column]


def _parse_date(value: str) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def fiscal_year_gap_days(subject: str | None, peer: str | None) -> int | None:
    """Days between two MMDD fiscal year ends, the short way round the year.

    Returns None when either end is unknown — an unknown year end is not a
    mismatch, and it is not a match either. Both are stated as such.

    Public because it is the whole of the mismatch rule, and a rule that
    decides what a report discloses is worth testing directly.
    """
    first, second = _day_of_year(subject), _day_of_year(peer)
    if first is None or second is None:
        return None
    gap = abs(first - second)
    return min(gap, DAYS_IN_YEAR - gap)


def _day_of_year(mmdd: str | None) -> int | None:
    """Turns EDGAR's MMDD fiscal year end into a day number, or None."""
    if not mmdd or len(mmdd.strip()) != 4 or not mmdd.strip().isdigit():
        return None
    text = mmdd.strip()
    month, day = int(text[:2]), int(text[2:])
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    try:
        # A non-leap year, so 29 February resolves to the 28th rather than
        # failing. The gap this feeds is measured in days, not in dates.
        safe_day = min(day, 28) if month == 2 else day
        return dt.date(2001, month, safe_day).timetuple().tm_yday
    except ValueError:
        return None


def _fiscal_year_note(
    subject: Company, profile: _Profile, gap: int | None
) -> tuple[bool, str | None]:
    """Whether the year ends differ materially, and how to say so."""
    if gap is None:
        if profile.fiscal_year_end is None:
            return False, "Fiscal year end not disclosed by EDGAR for this filer."
        return False, None

    if gap <= FISCAL_YEAR_END_TOLERANCE_DAYS:
        return False, None

    return True, (
        f"Fiscal year ends {_readable_mmdd(profile.fiscal_year_end)}, "
        f"{gap} days from {subject.name}'s "
        f"{_readable_mmdd(subject.fiscal_year_end)}. Figures cover different "
        "periods and are not directly comparable."
    )


def _readable_mmdd(mmdd: str | None) -> str:
    """Renders EDGAR's MMDD as MM-DD, or says it is not disclosed."""
    if not mmdd or len(mmdd) != 4:
        return "an undisclosed date"
    return f"{mmdd[:2]}-{mmdd[2:]}"


def _selection_note(
    candidate: _Candidate, company: Company, fiscal_note: str | None
) -> str:
    """One line stating how this peer was chosen, and what to watch for."""
    phrase = _METHOD_PHRASES[candidate.method].format(
        form=candidate.source_form or "filing",
        date=(
            candidate.filed_date.isoformat()
            if candidate.filed_date is not None
            else "an undated filing"
        ),
        sic=company.sic_code or "the filer's",
    )
    note = f"Selected because it is {phrase}."
    return f"{note} {fiscal_note}" if fiscal_note else note


# --- Public interface -------------------------------------------------------


async def select_peers(
    company: Company,
    manifest: Sequence[FilingRef],
    client: EdgarClient,
    *,
    allow_model: bool = True,
) -> PeerSet:
    """Selects comparable companies, recording how each one was chosen.

    Args:
        company: The subject. Its SIC code and fiscal year end drive rungs 3
            and the mismatch disclosure respectively.
        manifest: Filings from m02. Rungs 1 and 2 read the latest proxy and
            the latest annual report out of it.
        client: The shared EDGAR client.
        allow_model: When False the last rung is skipped entirely. Set this
            where a filed source is required.

    Returns:
        A `PeerSet`. Empty when no rung produced a peer that resolved — which
        is reported as a finding, not as a failure.
    """
    subject_cik = pad_cik(company.cik)
    index = _NameIndex(await load_index(client))

    accepted: dict[str, _Candidate] = {}
    methods_used: list[PeerSelectionMethod] = []
    notes: list[str] = []

    for method, produce in _ladder(company, manifest, client, index, allow_model):
        if len(accepted) >= PEER_TARGET_COUNT:
            break

        try:
            candidates = await produce()
        except Exception as cause:  # noqa: BLE001 — a rung never fails the ladder
            logger.warning(
                "Peer selection rung failed",
                extra={
                    "cik": company.cik,
                    "method": str(method),
                    "error": str(cause),
                },
            )
            notes.append(f"{_rung_name(method)} could not be read.")
            continue

        added = 0
        for candidate in candidates:
            if len(accepted) >= PEER_TARGET_COUNT:
                break
            if candidate.entry.cik in accepted or candidate.entry.cik == subject_cik:
                continue
            accepted[candidate.entry.cik] = candidate
            added += 1

        if added:
            methods_used.append(method)
        else:
            notes.append(f"{_rung_name(method)} named no listed comparable.")

    peers = await _build_peers(company, list(accepted.values()), client)

    logger.info(
        "Peer selection complete",
        extra={
            "cik": company.cik,
            "peers": len(peers),
            "methods": [str(method) for method in methods_used],
            "fiscal_year_mismatches": sum(
                1 for peer in peers if peer.fiscal_year_mismatch
            ),
        },
    )
    return PeerSet(
        subject_cik=subject_cik,
        peers=tuple(peers[:PEER_MAX_COUNT]),
        methods_used=tuple(methods_used),
        notes=tuple(notes),
    )


def _ladder(
    company: Company,
    manifest: Sequence[FilingRef],
    client: EdgarClient,
    index: _NameIndex,
    allow_model: bool,
) -> list[tuple[PeerSelectionMethod, Any]]:
    """The rungs, in order, each as a callable that produces candidates."""
    subject_cik = pad_cik(company.cik)
    proxy = _latest_of_form(manifest, frozenset({PROXY_FORM}))
    annual = _latest_of_form(manifest, _annual_forms_for(company.filer_type))

    rungs: list[tuple[PeerSelectionMethod, Any]] = []

    async def _from_proxy() -> list[_Candidate]:
        if proxy is None:
            return []
        return await _candidates_from_filing(
            client,
            proxy,
            PEER_GROUP_MARKERS,
            PeerSelectionMethod.COMPENSATION_PEER_GROUP,
            index,
            subject_cik,
        )

    async def _from_annual() -> list[_Candidate]:
        if annual is None:
            return []
        return await _candidates_from_filing(
            client,
            annual,
            COMPETITION_MARKERS,
            PeerSelectionMethod.COMPETITION_PARAGRAPH,
            index,
            subject_cik,
        )

    async def _from_sic() -> list[_Candidate]:
        return await _candidates_from_sic(client, company, index)

    async def _from_model() -> list[_Candidate]:
        return await _candidates_from_model(company, index)

    rungs.append((PeerSelectionMethod.COMPENSATION_PEER_GROUP, _from_proxy))
    rungs.append((PeerSelectionMethod.COMPETITION_PARAGRAPH, _from_annual))
    rungs.append((PeerSelectionMethod.SIC_MATCH, _from_sic))
    if allow_model:
        rungs.append((PeerSelectionMethod.LLM_SUGGESTION, _from_model))
    return rungs


def _rung_name(method: PeerSelectionMethod) -> str:
    """How a rung is named when the interface reports on it."""
    return {
        PeerSelectionMethod.COMPENSATION_PEER_GROUP: (
            "The compensation peer group"
        ),
        PeerSelectionMethod.COMPETITION_PARAGRAPH: (
            "The competition discussion"
        ),
        PeerSelectionMethod.SIC_MATCH: "EDGAR's industry classification",
        PeerSelectionMethod.LLM_SUGGESTION: "The suggested comparables",
    }[method]


async def _build_peers(
    company: Company,
    candidates: Sequence[_Candidate],
    client: EdgarClient,
) -> list[Peer]:
    """Confirms each candidate against EDGAR and builds the peer.

    A candidate whose CIK has no submissions document is dropped here — the
    ticker index said it was live, and EDGAR's own record is the confirmation.
    """
    peers: list[Peer] = []

    for candidate in candidates:
        profile = await _load_profile(client, candidate.entry.cik)
        if profile is None:
            logger.warning(
                "Dropping peer that could not be confirmed against EDGAR",
                extra={
                    "cik": candidate.entry.cik,
                    "ticker": candidate.entry.ticker,
                },
            )
            continue

        gap = fiscal_year_gap_days(
            company.fiscal_year_end, profile.fiscal_year_end
        )
        mismatch, fiscal_note = _fiscal_year_note(company, profile, gap)

        peers.append(
            Peer(
                cik=profile.cik,
                ticker=candidate.entry.ticker,
                name=profile.name or candidate.entry.name,
                selection_method=candidate.method,
                selection_source_url=candidate.source_url,
                selection_accession_no=candidate.accession_no,
                selection_filed_date=candidate.filed_date,
                selection_note=_selection_note(candidate, company, fiscal_note),
                sic_code=profile.sic_code,
                filer_type=profile.filer_type,
                fiscal_year_end=profile.fiscal_year_end,
                fiscal_year_mismatch=mismatch,
                fiscal_year_note=fiscal_note if mismatch else None,
            )
        )

    return peers


def peer_facts(peer_set: PeerSet, subject: Company) -> list[Fact]:
    """One fact per peer, carrying the selection method and any mismatch.

    Provenance points at the filing that named the peer when one did, and at
    the peer's own EDGAR record otherwise. Either way the fact traces to an SEC
    source: the selection method is disclosed in the fact's own text, so a
    reader is never shown a peer without being told how it got there.

    A peer that cannot produce a sound fact is skipped rather than patched.
    """
    facts: list[Fact] = []
    today = dt.date.today()

    for peer in peer_set.peers:
        accession = peer.selection_accession_no
        source_url = (
            str(peer.selection_source_url) if peer.selection_source_url else None
        )
        filed = peer.selection_filed_date

        if accession is None:
            # No filing named this peer, so the peer's own EDGAR record is the
            # source: it is what confirms the company exists and files.
            accession = f"CIK{peer.cik}"
            source_url = submissions_url(peer.cik)
            filed = filed or today
        elif source_url is None:
            source_url = filing_index_url(peer.cik, accession)

        try:
            facts.append(
                Fact(
                    metric=PEER_METRIC_TEMPLATE.format(ticker=peer.ticker),
                    label=f"Peer — {peer.ticker}",
                    value=None,
                    display_value=f"{peer.name} ({peer.ticker}). {peer.selection_note}",
                    unit=None,
                    period_start=None,
                    period_end=filed or today,
                    fiscal_year=None,
                    tier=SourceTier.FILING,
                    source_type=SourceType.SEC_FILING,
                    source_url=source_url,
                    accession_no=accession,
                    filed_date=filed or today,
                    extraction_method=ExtractionMethod.NARRATIVE,
                    confidence=1.0,
                )
            )
        except ValueError as cause:
            logger.warning(
                "Peer could not be expressed as a sourced fact",
                extra={
                    "cik": subject.cik,
                    "peer_ticker": peer.ticker,
                    "error": str(cause),
                },
            )

    return facts


# --- Peer comparison: financials and valuation, side by side ----------------


@dataclass(frozen=True, slots=True)
class PeerComparisonRow:
    """One company's line in the comparison table: the subject or a peer.

    Revenue, margin and growth are read from that company's own SEC filings —
    the subject's from facts already extracted upstream, a peer's from a fresh
    read of its own filings, exactly as m03 and m07 build them for the
    subject. Valuation multiples additionally need a live quote, which is why
    they carry tier 3 while the financials beside them stay tier 1: the row is
    only as trustworthy as its shakiest cell, and a reader should be able to
    tell which cells those are.
    """

    ticker: str
    name: str
    is_subject: bool
    revenue: Fact | None = None
    net_margin: Fact | None = None
    operating_margin: Fact | None = None
    revenue_growth: Fact | None = None
    enterprise_value: Fact | None = None
    price_to_earnings: Fact | None = None
    ev_to_ebitda: Fact | None = None
    ev_to_sales: Fact | None = None
    dividend_yield: Fact | None = None

    @property
    def facts(self) -> tuple[Fact, ...]:
        return tuple(
            fact
            for fact in (
                self.revenue,
                self.net_margin,
                self.operating_margin,
                self.revenue_growth,
                self.enterprise_value,
                self.price_to_earnings,
                self.ev_to_ebitda,
                self.ev_to_sales,
                self.dividend_yield,
            )
            if fact is not None
        )


@dataclass(frozen=True, slots=True)
class PeerComparison:
    """The comparison table: the subject's row, then each peer that answered.

    A peer that could not be compared is not silently missing a row — its
    ticker and the reason are in `notes`, the same way a ladder rung that
    named nothing is reported in `PeerSet.notes` rather than left unexplained.
    """

    rows: tuple[PeerComparisonRow, ...]
    notes: tuple[str, ...] = ()

    @property
    def facts(self) -> list[Fact]:
        """Every fact behind the table, for the provenance gate and the rail."""
        return [fact for row in self.rows for fact in row.facts]


#: (source metric, row attribute) for the figures copied onto each row.
_COMPARISON_METRICS: tuple[tuple[str, str], ...] = (
    ("income.revenue", "revenue"),
    ("margin.net", "net_margin"),
    ("margin.operating", "operating_margin"),
    ("growth.income.revenue.yoy", "revenue_growth"),
)

#: How each valuation figure reads when relabelled onto a row.
_VALUATION_LABELS: dict[str, str] = {
    "enterprise_value": "Enterprise value",
    "price_to_earnings": "Price to earnings",
    "ev_to_ebitda": "EV to EBITDA",
    "ev_to_sales": "EV to sales",
    "dividend_yield": "Dividend yield",
}

#: Valuation figures that are a count of currency rather than a multiple, and
#: so are not rendered with the "×" suffix a multiple carries.
_VALUATION_CURRENCY_METRICS = frozenset({"enterprise_value"})

#: Valuation figures expressed as a percentage.
_VALUATION_PERCENT_METRICS = frozenset({"dividend_yield"})


async def build_peer_comparison(
    company: Company,
    peer_set: PeerSet,
    subject_facts: Sequence[Fact],
    client: EdgarClient,
    *,
    max_peers: int = PEER_COMPARISON_MAX_COUNT,
) -> PeerComparison:
    """Revenue, margin, growth and valuation for the subject and its peers.

    Every company's financials come from its own SEC filings, read the same
    way the subject's are: m03 extracts, m07 derives the ratios. Nothing here
    invents a number a filer did not report, and a peer whose filings cannot
    be read is dropped with a stated reason rather than shown with a gap
    quietly filled in.

    Valuation multiples need one more thing a filing cannot give: a live
    price. That figure comes from m05, the one module permitted to hold one,
    which is the only reason a fact in this table is ever tier 3.

    Args:
        company: The subject. Its own comparison row is built from facts
            already extracted upstream, so the subject costs no extra fetch.
        peer_set: Peers from `select_peers`, in the order they were accepted.
        subject_facts: The subject's own facts, reported and derived, from
            earlier in the same run.
        client: The shared EDGAR client.
        max_peers: How many peers to fetch a full comparison for. A full
            filing read per comparable is not free, and an analyst's comp
            table is a handful of names, not the whole ladder.

    Returns:
        A row for the subject and for every peer that could be read. Never
        raises: a company that cannot be compared costs its own row, not the
        table.
    """
    rows: list[PeerComparisonRow] = [
        _row_from_facts(
            ticker=company.ticker,
            name=company.name,
            is_subject=True,
            facts=subject_facts,
        )
    ]
    notes: list[str] = []

    for peer in peer_set.peers[:max_peers]:
        row = await _peer_comparison_row(client, peer)
        if row is None:
            notes.append(
                f"{peer.ticker}: could not read comparable figures from its "
                "own filings."
            )
            continue
        rows.append(row)

    logger.info(
        "Peer comparison built",
        extra={
            "cik": company.cik,
            "rows": len(rows),
            "peers_attempted": min(len(peer_set.peers), max_peers),
        },
    )
    return PeerComparison(rows=tuple(rows), notes=tuple(notes))


async def _peer_comparison_row(
    client: EdgarClient, peer: Peer
) -> PeerComparisonRow | None:
    """One peer's comparison row, built the same way the subject's is.

    Returns None on any failure to read the peer's own filings or to extract
    a usable figure from them — a filer with nothing to show is not shown
    with blanks standing in for it.
    """
    try:
        peer_company = await load_company(client, peer.cik, ticker=peer.ticker)
        manifest = await m02_discovery.build_manifest(
            peer_company, client, include_exhibits=False
        )
    except (EdgarError, m02_discovery.DiscoveryError) as cause:
        logger.warning(
            "Could not build a filing manifest for a peer comparison",
            extra={"cik": peer.cik, "ticker": peer.ticker, "error": str(cause)},
        )
        return None

    reported = await m03_financials.extract_financials(
        peer_company, m02_discovery.as_filings(manifest)
    )
    if not any(fact.value is not None for fact in reported):
        logger.info(
            "No reported figures for peer comparison",
            extra={"cik": peer.cik, "ticker": peer.ticker},
        )
        return None

    facts: list[Fact] = [
        *reported,
        *compute_derived_metrics(reported, sic_code=peer_company.sic_code),
    ]
    # Market data is optional everywhere in this system; a peer with no quote
    # still gets its financial cells, just no valuation multiple.
    facts.extend(await m05_market.fetch_market_data(peer_company))

    return _row_from_facts(
        ticker=peer.ticker, name=peer.name, is_subject=False, facts=facts
    )


def _row_from_facts(
    *, ticker: str, name: str, is_subject: bool, facts: Sequence[Fact]
) -> PeerComparisonRow:
    """Builds one row from a company's own facts, relabelled for this table.

    Relabelling gives each figure a metric name unique to this company within
    the report — the same reason `peer_facts` embeds a ticker in its own
    metric template — so a peer's revenue and the subject's revenue are two
    facts, not one overwriting the other in the fact store.
    """
    latest = _latest_by_metric(facts)

    values = {
        attribute: _relabel(latest.get(metric), ticker, metric)
        for metric, attribute in _COMPARISON_METRICS
    }

    return PeerComparisonRow(
        ticker=ticker,
        name=name,
        is_subject=is_subject,
        enterprise_value=_enterprise_value(latest, ticker),
        price_to_earnings=_price_to_earnings(latest, ticker),
        ev_to_ebitda=_ev_to_ebitda(latest, ticker),
        ev_to_sales=_ev_to_sales(latest, ticker),
        dividend_yield=_dividend_yield(latest, ticker),
        **values,
    )


def _latest_by_metric(facts: Sequence[Fact]) -> dict[str, Fact]:
    """The most recent, non-segment fact for each metric a company reported."""
    latest: dict[str, Fact] = {}
    for fact in facts:
        if fact.value is None or fact.segment_member is not None:
            continue
        incumbent = latest.get(fact.metric)
        if incumbent is None or fact.period_end > incumbent.period_end:
            latest[fact.metric] = fact
    return latest


def _relabel(fact: Fact | None, ticker: str, metric: str) -> Fact | None:
    """Renames a fact into this table's namespace, without changing its value.

    Provenance is untouched — same source, same tier, same accession. Only the
    identity changes, which is what lets the same metric from two different
    companies sit in one shared report without one overwriting the other.
    """
    if fact is None:
        return None
    return fact.model_copy(
        update={
            "metric": f"peer.comparison.{ticker}.{metric}",
            "label": f"{ticker} — {fact.label}",
        }
    )


def _ratio(numerator: float, denominator: float) -> float | None:
    """A quotient, or None when the denominator is too near zero to mean much.

    Uses the same tolerance m07 divides by rather than a second one invented
    here, so "too close to zero to divide by" means the same thing everywhere
    arithmetic happens in this system.
    """
    if abs(denominator) < ANALYSIS_ZERO_TOLERANCE:
        return None
    result = numerator / denominator
    return result if math.isfinite(result) else None


def _render_valuation(
    metric: str, value: float, sources: Sequence[Fact]
) -> tuple[float, str, str | None]:
    """Rounds and renders a valuation figure by what kind of figure it is.

    Three kinds live in this table and they cannot share a format. A multiple
    is "18.40x"; enterprise value is a count of currency and is scaled the way
    every other currency figure in the report is scaled, so it lines up in the
    same column; a yield is a percentage. Rendering them all as multiples,
    which is what this did when multiples were the only thing here, would have
    printed a market capitalisation as "409000000000.00x".

    Rounding happens here, at the boundary, for the same reason m07 rounds at
    its own: two runs over the same filings must produce identical facts.
    """
    if metric in _VALUATION_PERCENT_METRICS:
        rounded = round(value, PERCENT_DECIMAL_PLACES)
        return (
            rounded,
            PERCENT_DISPLAY_TEMPLATE.format(value=rounded),
            UNIT_PERCENT,
        )

    if metric in _VALUATION_CURRENCY_METRICS:
        rounded = round(value, CURRENCY_DECIMAL_PLACES)
        scaled = rounded / DISPLAY_SCALE_DIVISOR
        display = f"{scaled:,.0f}" if abs(scaled) >= 1 else f"{scaled:,.2f}"
        currency = next(
            (source.unit for source in sources if source.unit), None
        )
        return rounded, display, currency

    rounded = round(value, RATIO_DECIMAL_PLACES)
    return rounded, TIMES_DISPLAY_TEMPLATE.format(value=rounded), UNIT_TIMES


def _valuation_fact(
    *,
    ticker: str,
    metric: str,
    value: float | None,
    formula: str,
    sources: Sequence[Fact],
) -> Fact | None:
    """One valuation multiple, tiered and anchored the way m07 derives a fact.

    Tier is the highest — least trusted — tier among the inputs: a multiple
    built from a filing and a live quote is only as sound as the quote. The
    anchor (source, accession, filed date) is whichever input is freshest,
    which is always the quote here — a multiple is "as of" the price, not the
    filing beneath it.
    """
    if value is None or not math.isfinite(value):
        return None

    anchor = max(sources, key=lambda f: (f.filed_date, f.accession_no))
    rounded, display, unit = _render_valuation(metric, value, sources)

    return Fact(
        metric=f"peer.comparison.{ticker}.{metric}",
        label=f"{ticker} — {_VALUATION_LABELS[metric]}",
        value=rounded,
        display_value=display,
        unit=unit,
        period_start=None,
        period_end=anchor.period_end,
        fiscal_year=None,
        tier=SourceTier(max(int(source.tier) for source in sources)),
        source_type=anchor.source_type,
        source_url=str(anchor.source_url),
        accession_no=anchor.accession_no,
        filed_date=anchor.filed_date,
        extraction_method=ExtractionMethod.CALCULATED,
        confidence=min(source.confidence for source in sources),
        is_calculated=True,
        formula=formula,
    )


def _price_to_earnings(latest: dict[str, Fact], ticker: str) -> Fact | None:
    """Market capitalisation over net income. None unless both are known."""
    market_cap = latest.get("market.market_cap")
    net_income = latest.get("income.net_income")
    if market_cap is None or net_income is None:
        return None
    if market_cap.value is None or net_income.value is None:
        return None

    value = _ratio(market_cap.value, net_income.value)
    return _valuation_fact(
        ticker=ticker,
        metric="price_to_earnings",
        value=value,
        formula=(
            f"market capitalisation as of {market_cap.filed_date.isoformat()} "
            "÷ net income FY"
            f"{net_income.fiscal_year or net_income.period_end.year}"
        ),
        sources=(market_cap, net_income),
    )


def _enterprise_value_of(
    latest: dict[str, Fact],
) -> tuple[float, tuple[Fact, ...]] | None:
    """Enterprise value and the facts behind it, or None if any is missing.

    Market capitalisation plus total debt less cash. Where debt or cash is not
    disclosed the figure is not computed at all — treating an undisclosed debt
    balance as zero would understate leverage while looking like a complete
    answer, which is the same reasoning m07 applies to net debt itself.

    Shared by every figure below it that needs enterprise value, so the four
    inputs are located once and the definition cannot drift between them.
    """
    market_cap = latest.get("market.market_cap")
    debt = latest.get("derived.total_debt")
    cash = latest.get("balance.cash_and_equivalents")
    if market_cap is None or debt is None or cash is None:
        return None
    if market_cap.value is None or debt.value is None or cash.value is None:
        return None

    value = market_cap.value + debt.value - cash.value
    return value, (market_cap, debt, cash)


def _enterprise_value(latest: dict[str, Fact], ticker: str) -> Fact | None:
    """Enterprise value as a figure in its own right, not only as a divisor."""
    computed = _enterprise_value_of(latest)
    if computed is None:
        return None
    value, sources = computed
    market_cap = sources[0]
    return _valuation_fact(
        ticker=ticker,
        metric="enterprise_value",
        value=value,
        formula=(
            f"market capitalisation as of {market_cap.filed_date.isoformat()} "
            "+ total debt − cash and equivalents"
        ),
        sources=sources,
    )


def _ev_to_ebitda(latest: dict[str, Fact], ticker: str) -> Fact | None:
    """Enterprise value over EBITDA. None unless every input is disclosed."""
    computed = _enterprise_value_of(latest)
    ebitda = latest.get("derived.ebitda")
    if computed is None or ebitda is None or ebitda.value is None:
        return None

    enterprise_value, sources = computed
    market_cap = sources[0]
    return _valuation_fact(
        ticker=ticker,
        metric="ev_to_ebitda",
        value=_ratio(enterprise_value, ebitda.value),
        formula=(
            f"(market capitalisation as of {market_cap.filed_date.isoformat()} "
            "+ total debt − cash and equivalents) ÷ EBITDA FY"
            f"{ebitda.fiscal_year or ebitda.period_end.year}"
        ),
        sources=(*sources, ebitda),
    )


def _ev_to_sales(latest: dict[str, Fact], ticker: str) -> Fact | None:
    """Enterprise value over revenue.

    Worth having beside EV/EBITDA because it survives a loss-making or
    heavily-adjusted year, where an earnings-based multiple stops meaning
    much. Revenue is the one line almost every filer reports on the same
    basis.
    """
    computed = _enterprise_value_of(latest)
    revenue = latest.get("income.revenue")
    if computed is None or revenue is None or revenue.value is None:
        return None

    enterprise_value, sources = computed
    market_cap = sources[0]
    return _valuation_fact(
        ticker=ticker,
        metric="ev_to_sales",
        value=_ratio(enterprise_value, revenue.value),
        formula=(
            f"(market capitalisation as of {market_cap.filed_date.isoformat()} "
            "+ total debt − cash and equivalents) ÷ revenue FY"
            f"{revenue.fiscal_year or revenue.period_end.year}"
        ),
        sources=(*sources, revenue),
    )


def _dividend_yield(latest: dict[str, Fact], ticker: str) -> Fact | None:
    """Dividends paid over market capitalisation, as a percentage.

    Built from the cash actually paid out in the filer's own cash flow
    statement rather than from a declared rate, because the cash flow figure
    is a reported fact and a forward rate is a projection. It is therefore a
    trailing yield, and the formula that travels with it says so.

    A filer that pays no dividend reports zero here, which is a disclosure,
    not a gap — so zero is rendered rather than dropped.
    """
    market_cap = latest.get("market.market_cap")
    dividends = latest.get("cashflow.dividends_paid")
    if market_cap is None or dividends is None:
        return None
    if market_cap.value is None or dividends.value is None:
        return None

    # Cash outflows are reported as positive magnitudes by m03's sign
    # convention, but a filer that tags the raw negative would otherwise
    # produce a negative yield. The magnitude is what a yield means.
    ratio = _ratio(abs(dividends.value), market_cap.value)
    return _valuation_fact(
        ticker=ticker,
        metric="dividend_yield",
        value=None if ratio is None else ratio * PERCENT_SCALE,
        formula=(
            "dividends paid FY"
            f"{dividends.fiscal_year or dividends.period_end.year} ÷ market "
            f"capitalisation as of {market_cap.filed_date.isoformat()}, "
            "trailing"
        ),
        sources=(market_cap, dividends),
    )
