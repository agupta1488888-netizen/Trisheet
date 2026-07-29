/**
 * Shared type contracts between the Next.js frontend and the FastAPI backend.
 *
 * These mirror `backend/app/models.py`. When one changes, change both.
 * No logic lives here — types only.
 */

/** Source tier. Enforced in backend code, never by prompting. */
export const SOURCE_TIER = {
  /** SEC filings: 10-K, 10-Q, 8-K, DEF 14A, 20-F, 40-F, 6-K and exhibits. */
  FILING: 1,
  /** Company website, investor presentations, press releases. */
  COMPANY: 2,
  /** Market data providers. Price, market cap and multiples only. */
  MARKET: 3,
  /** News and general web. */
  NEWS: 4,
} as const;

export type SourceTier = (typeof SOURCE_TIER)[keyof typeof SOURCE_TIER];

export type SourceType =
  | "sec_filing"
  | "sec_xbrl"
  | "company_site"
  | "investor_presentation"
  | "press_release"
  | "market_data"
  | "news";

/**
 * Filer type, decided by which annual form a company actually files.
 * domestic = 10-K, foreign = 20-F, canadian = 40-F (Multijurisdictional
 * Disclosure System).
 */
export type FilerType = "domestic" | "foreign" | "canadian";

/** Accounting taxonomy a figure was extracted from. */
export type Taxonomy = "us-gaap" | "ifrs-full" | "dei";

/**
 * A single sourced figure or statement.
 *
 * Every provenance field is required. A fact missing any of them is discarded
 * at write time — it is never rendered with a blank.
 */
export interface Fact {
  id: string;
  reportId: string;
  /** Dotted path identifying what this fact is, e.g. "income.revenue". */
  metric: string;
  label: string;
  /** Numeric value, or null when the figure is genuinely not disclosed. */
  value: number | null;
  /** Rendered text for non-numeric facts, or the formatted numeric value. */
  displayValue: string;
  unit: string | null;
  /** Period covered. Instant facts carry only periodEnd. */
  periodStart: string | null;
  periodEnd: string;
  fiscalYear: number | null;

  /* Provenance — all required. */
  tier: SourceTier;
  sourceType: SourceType;
  sourceUrl: string;
  accessionNo: string;
  filedDate: string;

  /** Which XBRL tag actually resolved, when the fact came from XBRL. */
  resolvedTag: string | null;
  taxonomy: Taxonomy | null;
  /** True when computed in m07_analysis. Carries the formula that produced it. */
  isCalculated: boolean;
  formula: string | null;
}

export interface Company {
  cik: string;
  ticker: string;
  name: string;
  filerType: FilerType;
  sicCode: string | null;
  sector: string | null;
  fiscalYearEnd: string | null;
  /** ISO 4217 code the filer reports in. Null when undetermined. */
  reportingCurrency: string | null;
}

/** One possible match for an ambiguous query. Never silently chosen. */
export interface Candidate {
  cik: string;
  ticker: string;
  name: string;
}

export type ResolutionOutcome = "resolved" | "ambiguous" | "not_found";

/**
 * Result of resolving user input to a filer. When the input matches more than
 * one entity the outcome is "ambiguous" and the interface must ask.
 */
export interface Resolution {
  outcome: ResolutionOutcome;
  query: string;
  company: Company | null;
  candidates: Candidate[];
}

export interface Filing {
  accessionNo: string;
  cik: string;
  form: string;
  filedDate: string;
  periodOfReport: string | null;
  primaryDocUrl: string;
}

/** An exhibit attached to a filing, e.g. the EX-99.1 earnings release. */
export interface ExhibitRef {
  exhibitType: string;
  description: string | null;
  url: string;
}

/** A filing in the manifest, with absolute URLs and its accession number. */
export interface FilingRef {
  accessionNo: string;
  cik: string;
  /** As filed, including any amendment suffix: "10-K/A". */
  form: string;
  /** The form with any amendment suffix removed: "10-K". */
  baseForm: string;
  isAmendment: boolean;
  filedDate: string;
  periodOfReport: string | null;
  primaryDocUrl: string;
  filingIndexUrl: string;
  items: string[];
  exhibits: ExhibitRef[];
}

export type ReportStatus =
  | "queued"
  | "resolving"
  | "extracting"
  | "analysing"
  | "writing"
  | "verifying"
  | "complete"
  | "failed";

export interface Report {
  id: string;
  ticker: string;
  cik: string | null;
  status: ReportStatus;
  /** Present only when status is "failed". Written in the interface's voice. */
  errorMessage: string | null;
  createdAt: string;
  completedAt: string | null;
}

/** Typed failure returned by the backend. Never a bare string. */
export interface ApiError {
  code: string;
  /** What happened and what to do, in the interface's voice. */
  message: string;
  detail: string | null;
}
