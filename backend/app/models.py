"""Pydantic models for every boundary in the system.

These mirror `frontend/lib/types.ts`. When one changes, change both.

The central model is `Fact`. Its provenance fields are required with no
defaults: a fact that cannot name its source cannot be constructed, which is
why "discarded at write time" is a property of the type rather than a check
someone has to remember to run.

Wire casing
    Everything the API returns serialises in camel case, because that is what
    `frontend/lib/types.ts` declares. Field names stay snake case in Python and
    `populate_by_name` keeps them accepted on the way in, so a row read out of
    Postgres — which is snake case — validates against the same model that
    serves the browser. There is one model per concept, not two.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from enum import IntEnum, StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator
from pydantic.alias_generators import to_camel

from app.config import (
    CHAT_MESSAGE_MAX_CHARS,
    CUSTOM_PERIODS_MAX,
    CUSTOM_PERIODS_MIN,
    FACT_ID_LENGTH,
    FACT_ID_PREFIX,
    NOT_DISCLOSED_TEXT,
)

#: Applied to every model the API serialises. `serialize_by_alias` means a
#: caller cannot forget `by_alias=True` and quietly emit snake case that the
#: browser's types do not describe.
WIRE_CONFIG = ConfigDict(
    frozen=True,
    alias_generator=to_camel,
    populate_by_name=True,
    serialize_by_alias=True,
)


class WireModel(BaseModel):
    """Base for models that cross the HTTP boundary."""

    model_config = WIRE_CONFIG


class SourceTier(IntEnum):
    """Provenance tier. Enforced in code, never by prompting."""

    #: 10-K, 10-Q, 8-K, DEF 14A, 20-F, 40-F, 6-K and their exhibits.
    FILING = 1
    #: Company website, investor presentations, press releases.
    COMPANY = 2
    #: Market data providers. Price, market cap and multiples only.
    MARKET = 3
    #: News and general web.
    NEWS = 4


class SourceType(StrEnum):
    SEC_FILING = "sec_filing"
    SEC_XBRL = "sec_xbrl"
    COMPANY_SITE = "company_site"
    INVESTOR_PRESENTATION = "investor_presentation"
    PRESS_RELEASE = "press_release"
    MARKET_DATA = "market_data"
    NEWS = "news"


class FilerType(StrEnum):
    """Decided by which annual form a filer actually files, never by guesswork.

    DOMESTIC files 10-K. FOREIGN (a foreign private issuer) files 20-F.
    CANADIAN files 40-F under the Multijurisdictional Disclosure System, which
    permits Canadian-form disclosure and therefore needs its own handling.
    """

    DOMESTIC = "domestic"
    FOREIGN = "foreign"
    CANADIAN = "canadian"


class Taxonomy(StrEnum):
    US_GAAP = "us-gaap"
    IFRS_FULL = "ifrs-full"
    DEI = "dei"


class ExtractionMethod(StrEnum):
    """How a fact came to exist. Recorded so any figure can be re-derived.

    This is part of provenance, not metadata: a reader who disagrees with a
    figure needs to know whether it was read off a filing, parsed out of prose
    or computed, before they know which part of the pipeline to distrust.
    """

    #: Consolidated figure from the XBRL company facts API.
    XBRL_COMPANY_FACTS = "xbrl_company_facts"
    #: Dimensional figure parsed from a filing's XBRL instance document.
    XBRL_DIMENSIONAL = "xbrl_dimensional"
    #: Read out of filing narrative text by m04.
    NARRATIVE = "narrative"
    #: Computed in m07 from other facts. Always carries a formula.
    CALCULATED = "calculated"
    #: Tier 3 market data from m05.
    MARKET_DATA = "market_data"
    #: The metric was searched for and genuinely not reported.
    NOT_DISCLOSED = "not_disclosed"


class ReportStatus(StrEnum):
    QUEUED = "queued"
    RESOLVING = "resolving"
    EXTRACTING = "extracting"
    ANALYSING = "analysing"
    WRITING = "writing"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    FAILED = "failed"


class Fact(BaseModel):
    """A single sourced figure or statement.

    Every provenance field below is required. There is deliberately no default
    for any of them — a fact missing provenance raises at construction rather
    than rendering as a blank.
    """

    model_config = ConfigDict(**WIRE_CONFIG, extra="forbid")

    metric: str = Field(description="Dotted path, e.g. 'income.revenue'.")
    label: str

    value: float | None = Field(
        default=None,
        description="Numeric value, or None when genuinely not disclosed.",
    )
    display_value: str = Field(
        description="Rendered text. 'Not disclosed' when value is None."
    )
    unit: str | None = None

    period_start: dt.date | None = None
    period_end: dt.date
    fiscal_year: int | None = None

    # --- Segmentation ------------------------------------------------------
    # Populated only for dimensional facts. A fact with no segment is the
    # consolidated figure, which is what the sum of its segments must match.
    segment_axis: str | None = Field(
        default=None,
        description=(
            "XBRL axis the breakdown is along, e.g. 'srt:ProductOrServiceAxis'."
        ),
    )
    segment_member: str | None = Field(
        default=None,
        description="XBRL member on that axis, e.g. 'aapl:IPhoneMember'.",
    )
    segment_label: str | None = Field(
        default=None,
        description="Human-readable member name, e.g. 'iPhone'.",
    )

    # --- Provenance. All required. -----------------------------------------
    tier: SourceTier
    source_type: SourceType
    source_url: HttpUrl
    accession_no: str
    filed_date: dt.date

    # --- Extraction trail --------------------------------------------------
    extraction_method: ExtractionMethod = Field(
        description="How the figure was obtained. Required — never inferred."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Confidence that this value is what the filer reported for this "
            "metric and period. 1.0 means the metric's primary tag resolved in "
            "the filer's primary taxonomy; fallbacks reduce it. A "
            "NOT_DISCLOSED marker carries 0.0 — there is no value to be "
            "confident about."
        ),
    )
    resolved_tag: str | None = Field(
        default=None,
        description="The XBRL tag that actually resolved, when applicable.",
    )
    taxonomy: Taxonomy | None = None

    is_calculated: bool = Field(
        default=False,
        description="True when produced by m07_analysis rather than extracted.",
    )
    formula: str | None = Field(
        default=None,
        description="Required when is_calculated; rendered beside the figure.",
    )

    @property
    def fact_id(self) -> str:
        """Stable citation id, derived from what identifies this fact.

        Every sentence m10 writes names the fact ids it rests on, and m11
        checks those names against the fact store. Deriving the id from the
        fact's identity — rather than assigning one at insert time — means the
        writer can cite before anything is persisted, and the checker can
        verify without trusting a mapping someone maintained by hand.

        The identity is the unique constraint on `facts` minus `report_id`:
        the same metric, period and segment is the same fact.
        """
        identity = "|".join(
            (
                self.metric,
                self.period_end.isoformat(),
                self.period_start.isoformat() if self.period_start else "",
                self.segment_axis or "",
                self.segment_member or "",
            )
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"{FACT_ID_PREFIX}{digest[:FACT_ID_LENGTH]}"

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        """Enforces the rules that make a fact renderable, at construction.

        These mirror the check constraints on the `facts` table. Failing here
        rather than at insert time means an unrenderable fact cannot travel
        through the pipeline pretending to be sound.
        """
        if not self.display_value.strip():
            message = (
                "display_value is blank. A missing figure reads "
                f"{NOT_DISCLOSED_TEXT!r}, never an empty string."
            )
            raise ValueError(message)

        if not self.accession_no.strip():
            message = "accession_no is blank; the fact cannot name its source."
            raise ValueError(message)

        if self.is_calculated and not (self.formula or "").strip():
            message = (
                "A calculated fact must carry the formula that produced it. "
                "Derived figures are rendered with their working or not at all."
            )
            raise ValueError(message)

        if self.extraction_method is ExtractionMethod.NOT_DISCLOSED:
            if self.value is not None:
                message = (
                    "A NOT_DISCLOSED fact cannot carry a value. Never estimate."
                )
                raise ValueError(message)
            if self.display_value != NOT_DISCLOSED_TEXT:
                message = (
                    f"A NOT_DISCLOSED fact must read {NOT_DISCLOSED_TEXT!r}, "
                    f"not {self.display_value!r}."
                )
                raise ValueError(message)

        if (self.segment_axis is None) != (self.segment_member is None):
            message = (
                "segment_axis and segment_member are set together or not at "
                "all; a member without its axis is unattributable."
            )
            raise ValueError(message)

        return self


class Company(BaseModel):
    model_config = WIRE_CONFIG

    cik: str = Field(description="Zero-padded to 10 digits.")
    ticker: str
    name: str
    filer_type: FilerType
    sic_code: str | None = None
    sector: str | None = Field(
        default=None, description="SIC description, as EDGAR reports it."
    )
    fiscal_year_end: str | None = Field(
        default=None, description="MMDD, as EDGAR reports it in submissions."
    )
    reporting_currency: str | None = Field(
        default=None,
        description="ISO 4217 code the filer reports in. None when undetermined.",
    )


class Candidate(BaseModel):
    """One possible match for an ambiguous query. Never silently chosen."""

    model_config = WIRE_CONFIG

    cik: str
    ticker: str
    name: str


class ResolutionOutcome(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class Resolution(BaseModel):
    """Result of resolving user input to a filer.

    When the input matches more than one entity the outcome is AMBIGUOUS and
    `candidates` is populated. The system does not pick one.
    """

    model_config = WIRE_CONFIG

    outcome: ResolutionOutcome
    query: str
    company: Company | None = None
    candidates: tuple[Candidate, ...] = ()


class ExhibitRef(BaseModel):
    """An exhibit attached to a filing, e.g. the EX-99.1 earnings release."""

    model_config = WIRE_CONFIG

    exhibit_type: str
    description: str | None = None
    url: HttpUrl


class FilingRef(BaseModel):
    """A filing in the manifest, with absolute URLs and its accession number."""

    model_config = WIRE_CONFIG

    accession_no: str
    cik: str
    #: As filed, including the amendment suffix: "10-K/A".
    form: str
    #: The form with any amendment suffix removed: "10-K".
    base_form: str
    is_amendment: bool
    filed_date: dt.date
    period_of_report: dt.date | None = None
    primary_doc_url: HttpUrl
    filing_index_url: HttpUrl
    #: 8-K item numbers, as EDGAR reports them.
    items: tuple[str, ...] = ()
    exhibits: tuple[ExhibitRef, ...] = ()


class Filing(BaseModel):
    model_config = WIRE_CONFIG

    accession_no: str
    cik: str
    form: str
    filed_date: dt.date
    period_of_report: dt.date | None = None
    primary_doc_url: HttpUrl


class Report(BaseModel):
    model_config = WIRE_CONFIG

    id: str
    ticker: str
    cik: str | None = None
    status: ReportStatus
    error_message: str | None = Field(
        default=None,
        description="Set only when status is FAILED. Written for a reader.",
    )
    created_at: dt.datetime
    completed_at: dt.datetime | None = None


# --- Peers (m08) ------------------------------------------------------------


class PeerSelectionMethod(StrEnum):
    """How a peer came to be on the list, in descending authority.

    This is disclosed beside every peer. A comparison against a group the
    company itself named is a different claim from a comparison against
    whatever else shares its SIC code, and the reader is entitled to know
    which one they are reading.
    """

    #: Named in the compensation committee's benchmarking group in a DEF 14A.
    COMPENSATION_PEER_GROUP = "compensation_peer_group"
    #: Named in the competition discussion of the annual report.
    COMPETITION_PARAGRAPH = "competition_paragraph"
    #: Shares the filer's SIC code, per EDGAR's own classification.
    SIC_MATCH = "sic_match"
    #: Proposed by the model when no filed source named anyone. Last resort.
    LLM_SUGGESTION = "llm_suggestion"


class Peer(BaseModel):
    """One comparable company, with how it was chosen and what it may distort.

    A peer whose ticker did not resolve to a live CIK never becomes one of
    these — resolution happens before construction, so a `Peer` that exists is
    a filer that exists.
    """

    model_config = WIRE_CONFIG

    cik: str = Field(description="Zero-padded to 10 digits.")
    ticker: str
    name: str

    selection_method: PeerSelectionMethod
    #: Where the selection came from: the filing that named it, or the EDGAR
    #: query that returned it. Empty only for a model suggestion, which names
    #: the model instead.
    selection_source_url: HttpUrl | None = None
    #: Accession of the filing that named this peer, when one did.
    selection_accession_no: str | None = None
    selection_filed_date: dt.date | None = None
    #: One line, in the interface's voice, stating how this peer was chosen.
    selection_note: str

    sic_code: str | None = None
    filer_type: FilerType | None = None
    fiscal_year_end: str | None = Field(
        default=None, description="MMDD, as EDGAR reports it."
    )
    #: True when this peer closes its books at a materially different time
    #: from the subject, which makes a like-for-like comparison unsound.
    fiscal_year_mismatch: bool = False
    #: Stated whenever `fiscal_year_mismatch`. Rendered beside the peer.
    fiscal_year_note: str | None = None

    @model_validator(mode="after")
    def _mismatch_is_disclosed(self) -> Self:
        """A mismatch that is not stated is a mismatch that is hidden."""
        if self.fiscal_year_mismatch and not (self.fiscal_year_note or "").strip():
            message = (
                f"{self.ticker}: a fiscal year mismatch must carry the note that "
                "discloses it."
            )
            raise ValueError(message)
        return self


class PeerSet(BaseModel):
    """The selected peers, and an account of how the ladder ran."""

    model_config = WIRE_CONFIG

    subject_cik: str
    peers: tuple[Peer, ...] = ()
    #: Rungs that contributed at least one peer, in the order they were tried.
    methods_used: tuple[PeerSelectionMethod, ...] = ()
    #: Rungs that were tried and produced nothing, with the reason. Shown so
    #: that "no compensation peer group" reads as a finding, not an omission.
    notes: tuple[str, ...] = ()

    @property
    def has_fiscal_year_mismatch(self) -> bool:
        return any(peer.fiscal_year_mismatch for peer in self.peers)


# --- Recent developments (m09) ----------------------------------------------


class DevelopmentEvent(BaseModel):
    """One current report in the timeline, sourced to the filing it came from.

    Mirrors `DevelopmentEvent` in frontend/lib/types.ts.
    """

    model_config = WIRE_CONFIG

    id: str
    date: dt.date
    form: str
    accession_no: str
    #: EDGAR item numbers, as filed, filtered to the ones a profile reports.
    items: tuple[str, ...]
    #: What those item numbers mean, in EDGAR's own words.
    item_labels: tuple[str, ...]
    headline: str
    source_url: HttpUrl
    #: Sentences quoted from the EX-99.1 earnings release, when there is one.
    result_sentences: tuple[str, ...] = ()
    #: Forward-looking sentences, labelled as guidance and never as results.
    guidance_sentences: tuple[str, ...] = ()
    #: Ids of the facts this event produced, for the provenance rail.
    fact_ids: tuple[str, ...] = ()


class DevelopmentTimeline(BaseModel):
    """The developments section: typed events plus the facts behind them."""

    model_config = WIRE_CONFIG

    events: tuple[DevelopmentEvent, ...] = ()
    facts: tuple[Fact, ...] = ()


# --- Generated prose (m10) --------------------------------------------------


class GeneratedSentence(BaseModel):
    """One sentence, with the facts it rests on.

    `fact_ids` is what makes m11's verification possible: a sentence that makes
    a numeric claim must name where the number came from.
    """

    model_config = WIRE_CONFIG

    text: str
    fact_ids: tuple[str, ...] = ()


class GeneratedSection(BaseModel):
    """Prose for one report section, or the reason there is none."""

    model_config = WIRE_CONFIG

    section_id: str
    title: str
    sentences: tuple[GeneratedSentence, ...] = ()
    #: Set when the section could not be written. Never a blank section.
    unavailable_reason: str | None = None

    @property
    def text(self) -> str:
        """The section as continuous prose."""
        return " ".join(sentence.text for sentence in self.sentences)


class GeneratedReport(BaseModel):
    """Everything m10 produced, in report order."""

    model_config = WIRE_CONFIG

    sections: tuple[GeneratedSection, ...] = ()

    def section(self, section_id: str) -> GeneratedSection | None:
        return next(
            (s for s in self.sections if s.section_id == section_id), None
        )


# --- Verification (m11) -----------------------------------------------------


class Severity(StrEnum):
    """Whether a finding stops the report or is merely disclosed on it."""

    #: The report does not render. Provenance is not negotiable.
    BLOCKING = "blocking"
    #: Rendered with the finding shown beside the figure it concerns.
    ADVISORY = "advisory"


class Violation(BaseModel):
    """One thing that is wrong, phrased for a reader rather than a log."""

    model_config = WIRE_CONFIG

    check: str
    severity: Severity
    #: The report section, when the finding belongs to one.
    section: str | None = None
    metric: str | None = None
    #: What happened and what it means. In the interface's voice.
    message: str
    #: The figure or fact the finding concerns, when there is one.
    detail: str | None = None


class CheckResult(BaseModel):
    """The outcome of one check, whether or not it found anything."""

    model_config = WIRE_CONFIG

    check: str
    #: One line naming what was checked and against what tolerance.
    description: str
    passed: bool
    #: Number of things examined. Zero means the check had nothing to run on,
    #: which is reported as such rather than as a pass.
    examined: int
    violations: tuple[Violation, ...] = ()

    @property
    def applicable(self) -> bool:
        """False when there was nothing to check. Not the same as passing."""
        return self.examined > 0


class ComplianceReport(BaseModel):
    """The verification result, consumed by the UI's compliance strip.

    Mirrors `ComplianceSummary` in frontend/lib/types.ts. Every count here is
    computed at verification time; the browser renders these numbers and never
    derives one.
    """

    model_config = WIRE_CONFIG

    #: The gate. A report is assembled only when this is True.
    passed: bool
    verified_at: dt.datetime

    fact_count: int
    #: Facts per tier, keyed by tier number.
    tier_counts: dict[int, int] = Field(default_factory=dict)

    #: Figures found in the generated prose.
    figure_count: int
    #: Figures that resolved to a stored fact and carry a citation.
    cited_figure_count: int
    coverage_ratio: float = Field(ge=0.0, le=1.0)
    #: Preformatted, e.g. "100%". The browser never recomputes it.
    coverage_display: str

    checks: tuple[CheckResult, ...] = ()
    violations: tuple[Violation, ...] = ()

    @property
    def blocking_violations(self) -> tuple[Violation, ...]:
        return tuple(
            v for v in self.violations if v.severity is Severity.BLOCKING
        )


class ApiError(BaseModel):
    """Typed failure. The API never returns a bare string error."""

    model_config = WIRE_CONFIG

    code: str
    message: str = Field(
        description="What happened and what to do, in the interface's voice."
    )
    detail: str | None = None


class HealthResponse(BaseModel):
    model_config = WIRE_CONFIG

    status: str
    environment: str
    #: EDGAR is the only hard dependency. False means reports cannot be built.
    edgar_configured: bool


# --- Requests ---------------------------------------------------------------


class AnalysisDepth(StrEnum):
    """How far the pipeline goes.

    Depth changes how many periods are extracted and which optional modules
    run. It never changes a provenance rule — a brief report is a smaller
    report, never a less sourced one.
    """

    BRIEF = "brief"
    STANDARD = "standard"
    FULL = "full"
    CUSTOM = "custom"


class ResolveRequest(WireModel):
    query: str = Field(min_length=1, description="Ticker or company name.")


class CreateReportRequest(WireModel):
    """Queues a report. `cik` is what the run uses; `ticker` is for display."""

    cik: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    depth: AnalysisDepth = AnalysisDepth.STANDARD
    #: Annual periods to extract, required exactly when depth is CUSTOM. The
    #: brief/standard/full depths carry their own fixed budget in
    #: DEPTH_PROFILES; this field only ever overrides that for CUSTOM.
    periods: int | None = Field(
        default=None, ge=CUSTOM_PERIODS_MIN, le=CUSTOM_PERIODS_MAX
    )

    @model_validator(mode="after")
    def _check_periods_matches_depth(self) -> Self:
        if self.depth == AnalysisDepth.CUSTOM and self.periods is None:
            message = "periods is required when depth is custom."
            raise ValueError(message)
        if self.depth != AnalysisDepth.CUSTOM and self.periods is not None:
            message = "periods may only be set when depth is custom."
            raise ValueError(message)
        return self


class TickerSuggestion(WireModel):
    """A ticker-index entry offered as an autocomplete suggestion."""

    cik: str
    ticker: str
    name: str
    #: Populated only when it is already known; the index alone does not say.
    filer_type: FilerType | None = None


class SuggestionsResponse(WireModel):
    suggestions: tuple[TickerSuggestion, ...] = ()


# --- Progress feed ----------------------------------------------------------


class StepState(StrEnum):
    """Where one pipeline step has got to.

    SKIPPED is not FAILED: an optional source that had nothing to give is a
    finding about the filer, and the interface says so in those words.
    """

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class ProgressStep(WireModel):
    """One line in the progress feed, as the browser receives it.

    Emitted twice per step — once on entry, once on settling — and merged by
    module in the browser, which is what makes the feed a feed rather than a
    growing list of duplicates.
    """

    #: Module scope, e.g. "m03". Rendered in the data face.
    module: str
    #: What the step is doing, sentence case: "Extracting XBRL figures".
    label: str
    state: StepState
    #: The real count the step produced — 142 facts, 31 filings. Never a
    #: percentage and never a guess, which is why it is None until it settles.
    count: int | None = None
    count_label: str | None = None
    at: dt.datetime
    #: Set on SKIPPED and FAILED. States what happened, for a reader.
    detail: str | None = None
    #: Wall time the step took, once it has settled. The monitoring figure.
    duration_ms: int | None = None


# --- The assembled document -------------------------------------------------
# What the browser renders and what the PDF prints. Assembled once by m12 and
# then rendered three ways, so a figure cannot appear in the PDF having failed
# verification on the screen.


class DocumentFact(Fact):
    """A fact as the document carries it: the fact, plus its identity.

    `id` is `Fact.fact_id` — content-addressed, so a table row that cites an id
    and the rail card that resolves it cannot drift apart.
    """

    id: str
    report_id: str

    @classmethod
    def of(cls, fact: Fact, report_id: str) -> DocumentFact:
        return cls(
            id=fact.fact_id,
            report_id=report_id,
            **fact.model_dump(by_alias=False),
        )


class ProseBlock(WireModel):
    """One paragraph of generated prose and the facts it rests on."""

    id: str
    text: str
    fact_ids: tuple[str, ...] = ()
    #: Set only when `text` is the filer's own words quoted verbatim rather
    #: than generated prose, so a reader can tell which filing item it came
    #: from (e.g. "From Item 1. Business").
    label: str | None = None


class FigureEmphasis(StrEnum):
    TOTAL = "total"
    DERIVED = "derived"


class FigureRow(WireModel):
    """A labelled row in a figure table, bound to the facts behind it.

    `fact_ids` is positional against `FigureTable.periods`, and None marks a
    period the filer did not report. A blank cell is never a zero.
    """

    label: str
    fact_ids: tuple[str | None, ...]
    emphasis: FigureEmphasis | None = None


class FigureTable(WireModel):
    id: str
    caption: str
    #: Column headers in the data face: "FY2023", "FY2024", "FY2025".
    periods: tuple[str, ...]
    rows: tuple[FigureRow, ...] = ()
    #: e.g. "USD millions, except per-share amounts".
    unit_note: str


class RiskItem(WireModel):
    """One risk as the filer discloses it. Never ranked, never editorialised."""

    id: str
    heading: str
    summary: str
    fact_ids: tuple[str, ...] = ()


class SectionId(StrEnum):
    """The seven sections, in brief order."""

    SNAPSHOT = "snapshot"
    BUSINESS = "business"
    FINANCIALS = "financials"
    ANALYSIS = "analysis"
    PEERS = "peers"
    DEVELOPMENTS = "developments"
    RISKS = "risks"


class DocumentSection(WireModel):
    """One rendered section. Never blank: it has content or it says why not."""

    id: SectionId
    title: str
    unavailable_reason: str | None = None
    prose: tuple[ProseBlock, ...] = ()
    tables: tuple[FigureTable, ...] = ()
    events: tuple[DevelopmentEvent, ...] = ()
    risks: tuple[RiskItem, ...] = ()


# --- Chart series -----------------------------------------------------------
# Values arrive in their display scale, already computed. The browser plots and
# formats them; it never divides, sums or derives a figure.


class SeriesMeta(WireModel):
    #: Axis label, e.g. "USD millions".
    unit_label: str
    #: Facts backing the series, so a chart cites like any other figure.
    fact_ids: tuple[str, ...] = ()


class RevenueMarginPoint(WireModel):
    period: str
    revenue: float | None = None
    gross_margin_pct: float | None = None
    operating_margin_pct: float | None = None


class SegmentMixPoint(WireModel):
    period: str
    #: Segment name to value. A missing key is a segment not disclosed that
    #: period, which is not the same as a zero.
    values: dict[str, float] = Field(default_factory=dict)


class CashFlowPoint(WireModel):
    period: str
    operating_cash_flow: float | None = None
    #: A positive outflow. The chart plots it below the axis.
    capex: float | None = None
    free_cash_flow: float | None = None


class PeerValuationPoint(WireModel):
    ticker: str
    name: str
    #: True for the subject. Highlighted, never re-ordered.
    is_subject: bool = False
    ev_to_ebitda: float | None = None
    price_to_earnings: float | None = None


class RevenueMarginSeries(WireModel):
    meta: SeriesMeta
    points: tuple[RevenueMarginPoint, ...] = ()


class SegmentMixSeries(WireModel):
    meta: SeriesMeta
    #: Draw order, bottom to top. Fixed here so colours are stable.
    segments: tuple[str, ...] = ()
    points: tuple[SegmentMixPoint, ...] = ()


class CashFlowSeries(WireModel):
    meta: SeriesMeta
    points: tuple[CashFlowPoint, ...] = ()


class PeerValuationSeries(WireModel):
    meta: SeriesMeta
    points: tuple[PeerValuationPoint, ...] = ()


class ReportCharts(WireModel):
    """Every series is nullable. A chart with no data is absent, never empty."""

    revenue_margin: RevenueMarginSeries | None = None
    segment_mix: SegmentMixSeries | None = None
    cash_flow: CashFlowSeries | None = None
    #: Tier 3. Absent whenever market data is unavailable.
    peer_valuation: PeerValuationSeries | None = None


class ArtifactKind(StrEnum):
    PDF = "pdf"
    XLSX = "xlsx"


class ArtifactRef(WireModel):
    """A rendered output, wherever it ended up.

    `url` is None when the artifact was built but could not be published —
    Storage is a sink, not a dependency, so the report is still complete.
    """

    kind: ArtifactKind
    #: Bytes of the rendered file. Reported so a reader can see it is real.
    size_bytes: int
    content_type: str
    url: HttpUrl | None = None
    storage_path: str | None = None
    created_at: dt.datetime
    #: Set when the artifact could not be built at all, in the same voice the
    #: interface uses everywhere else.
    unavailable_reason: str | None = None


class ReportDocument(WireModel):
    """Everything the report view renders. Read-only to the browser."""

    report: Report
    company: Company
    depth: AnalysisDepth
    #: Every cited fact, in first-appearance order — which is the order the
    #: provenance rail assigns its markers in.
    facts: tuple[DocumentFact, ...] = ()
    #: The manifest, so the rail can name the form behind an accession.
    filings: tuple[FilingRef, ...] = ()
    sections: tuple[DocumentSection, ...] = ()
    charts: ReportCharts = Field(default_factory=ReportCharts)
    compliance: ComplianceReport
    artifacts: tuple[ArtifactRef, ...] = ()


# --- Monitoring -------------------------------------------------------------


class StepTiming(WireModel):
    """How long one step took, aggregated across runs."""

    module: str
    label: str
    runs: int
    p50_ms: int
    p95_ms: int
    failures: int


class ReportMetrics(WireModel):
    """The operational picture, computed in SQL and rendered as given.

    Every figure here is counted, never sampled or estimated. A window with no
    runs in it reports zero runs rather than a rate of zero — those are
    different statements and the interface makes both.
    """

    window_hours: int
    reports_total: int
    reports_complete: int
    reports_failed: int
    #: Completed over total, 0–1. None when the window holds no settled run.
    success_rate: float | None = None
    success_rate_display: str
    #: End-to-end wall time of a completed run.
    p50_latency_ms: int | None = None
    p95_latency_ms: int | None = None
    #: Mean of each completed report's citation coverage, 0–1.
    citation_coverage: float | None = None
    citation_coverage_display: str
    #: Model spend attributable to one report, in US dollars.
    cost_per_report_usd: float | None = None
    cost_per_report_display: str
    tokens_per_report: int | None = None
    steps: tuple[StepTiming, ...] = ()
    generated_at: dt.datetime


# --- Chat assistant ----------------------------------------------------------
# A question about a completed report, answered from facts already stored for
# it (tier 1) or derivable from the same reported facts under a wider metric
# selection (tier 2), or from a DCF scenario built over those same facts. A
# company website and the open web are separate, deliberately deferred phases.
# See app/modules/chat_agent.


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ChatClaim(WireModel):
    """One segment of an assistant turn, individually sourced.

    Exactly one of three shapes, never ambiguously:

    - Certified: names a real source and a tier — tier 1 (`fact_id`, a fact
      already stored for this report, including a peer's own selection
      note), tier 2 (`source_url`, a company page fetched fresh for this
      answer, when the report's own data didn't have it and the user
      supplied a URL), tier 3 (`fact_id`, a stored market-data figure — the
      usual case is a peer valuation multiple such as EV/EBITDA, which
      already carries tier 3 lineage honestly because it uses market cap),
      or tier 4 (`source_url`, a general web search, the last resort tried
      only once tiers 1-3 have come up empty — the frontend must render
      this least like a filing).
    - Assumption: a modelling choice (a DCF discount rate, a growth rate, or
      a result computed from one) that names no filing and cannot be
      certified — `assumption_note` says what it is and why it isn't a fact.
    - Not found: the question could not be answered from this report's filed
      data. Carries no value, no tier, no citation, no assumption.

    A claim that is none of these, or more than one, is not constructable —
    the same discipline `Fact` applies to itself.
    """

    text: str
    #: 1 (filing) or 2 (company source). None unless certified.
    tier: SourceTier | None = None
    #: Set when citing a fact already stored for this report (tier 1, or a
    #: tier-2 metric recomputed from the same reported facts).
    fact_id: str | None = None
    #: Set when citing a page fetched fresh for this answer (a tier-2 company
    #: URL the user supplied). `accession_no`/`filed_date` stay None here —
    #: a fetched page has neither.
    source_url: HttpUrl | None = None
    source_type: SourceType | None = None
    accession_no: str | None = None
    filed_date: dt.date | None = None
    #: This segment states that the question could not be answered from this
    #: report's filed data. Carries no value, no tier, no citation.
    not_found: bool = False
    #: This segment is a modelling choice or a figure computed from one — a
    #: DCF discount rate, a growth assumption, or the resulting estimate.
    #: Never paired with a tier or fact id: an assumption names no filing.
    is_assumption: bool = False
    #: Required whenever `is_assumption`. States what the assumption is and
    #: that it is not a sourced figure, so it cannot be read as a fact by
    #: omission.
    assumption_note: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if not self.text.strip():
            message = "A chat claim's text is blank."
            raise ValueError(message)

        if self.not_found:
            if (
                self.tier is not None
                or self.fact_id is not None
                or self.is_assumption
            ):
                message = (
                    "A not-found claim cannot also carry a tier, a fact id, "
                    "or be marked as an assumption."
                )
                raise ValueError(message)
            return self

        if self.is_assumption:
            if self.tier is not None or self.fact_id is not None:
                message = (
                    "An assumption claim cannot also carry a tier or fact "
                    "id — it names no filing."
                )
                raise ValueError(message)
            if not (self.assumption_note or "").strip():
                message = (
                    "An assumption claim must carry the note stating what "
                    "the assumption is and that it is not a sourced figure."
                )
                raise ValueError(message)
            return self

        if self.tier is None or (self.fact_id is None and self.source_url is None):
            message = (
                "A claim that is not not_found and not an assumption must "
                "carry a tier, and either a fact id (a fact already stored "
                "for this report) or a source url (a page fetched fresh for "
                "this answer) naming where it came from."
            )
            raise ValueError(message)

        if int(self.tier) not in (
            int(SourceTier.FILING),
            int(SourceTier.COMPANY),
            int(SourceTier.MARKET),
            int(SourceTier.NEWS),
        ):
            message = (
                f"Chat claims may cite tier 1 (filing), tier 2 (company "
                f"source), tier 3 (market data — e.g. a peer valuation "
                f"multiple) or tier 4 (general web, last resort) facts; got "
                f"tier {int(self.tier)}."
            )
            raise ValueError(message)

        return self


class ChatTurn(WireModel):
    """One turn in a report's chat history, user or assistant."""

    id: str
    role: ChatRole
    claims: tuple[ChatClaim, ...] = ()
    #: Plain-text rendering of the turn, for simple display and for the user's
    #: own turns, which carry no claims.
    content: str
    #: True when every claim in this turn is a not-found claim.
    not_found: bool = False
    created_at: dt.datetime
    #: Questions left in the current rate-limit window, after this turn. None
    #: when no database is configured — the same case the limit itself
    #: cannot be enforced for.
    turns_remaining: int | None = None


class ChatRequest(WireModel):
    """One question about a completed report.

    `pasted_url` is the reader's own company-URL fallback: read only when the
    tier-1/tier-2 cascade over the report's own facts comes up empty, never
    consulted when it doesn't.
    """

    message: str = Field(min_length=1, max_length=CHAT_MESSAGE_MAX_CHARS)
    pasted_url: HttpUrl | None = None


class ChatSuggestions(WireModel):
    """Example questions this report's own stored data can actually answer.

    Deterministic, not model-generated — see `chat_agent.suggest_questions`.
    """

    suggestions: tuple[str, ...] = ()
