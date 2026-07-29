"""Pydantic models for every boundary in the system.

These mirror `frontend/lib/types.ts`. When one changes, change both.

The central model is `Fact`. Its provenance fields are required with no
defaults: a fact that cannot name its source cannot be constructed, which is
why "discarded at write time" is a property of the type rather than a check
someone has to remember to run.
"""

from __future__ import annotations

import datetime as dt
from enum import IntEnum, StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


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

    model_config = ConfigDict(frozen=True, extra="forbid")

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

    # --- Provenance. All required. -----------------------------------------
    tier: SourceTier
    source_type: SourceType
    source_url: HttpUrl
    accession_no: str
    filed_date: dt.date

    # --- Extraction trail --------------------------------------------------
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


class Company(BaseModel):
    model_config = ConfigDict(frozen=True)

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

    model_config = ConfigDict(frozen=True)

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

    model_config = ConfigDict(frozen=True)

    outcome: ResolutionOutcome
    query: str
    company: Company | None = None
    candidates: tuple[Candidate, ...] = ()


class ExhibitRef(BaseModel):
    """An exhibit attached to a filing, e.g. the EX-99.1 earnings release."""

    model_config = ConfigDict(frozen=True)

    exhibit_type: str
    description: str | None = None
    url: HttpUrl


class FilingRef(BaseModel):
    """A filing in the manifest, with absolute URLs and its accession number."""

    model_config = ConfigDict(frozen=True)

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
    model_config = ConfigDict(frozen=True)

    accession_no: str
    cik: str
    form: str
    filed_date: dt.date
    period_of_report: dt.date | None = None
    primary_doc_url: HttpUrl


class Report(BaseModel):
    model_config = ConfigDict(frozen=True)

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


class ApiError(BaseModel):
    """Typed failure. The API never returns a bare string error."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str = Field(
        description="What happened and what to do, in the interface's voice."
    )
    detail: str | None = None


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    environment: str
    #: EDGAR is the only hard dependency. False means reports cannot be built.
    edgar_configured: bool
