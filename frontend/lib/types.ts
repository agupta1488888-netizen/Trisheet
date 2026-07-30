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
 * How a fact came to exist. Part of provenance, not metadata: a reader who
 * disputes a figure needs to know whether it was read off a filing, parsed out
 * of prose or computed.
 */
export type ExtractionMethod =
  /** Consolidated figure from the XBRL company facts API. */
  | "xbrl_company_facts"
  /** Dimensional figure parsed from a filing's XBRL instance document. */
  | "xbrl_dimensional"
  /** Read out of filing narrative text. */
  | "narrative"
  /** Computed from other facts. Always carries a formula. */
  | "calculated"
  /** Tier 3 market data. */
  | "market_data"
  /** The metric was searched for and genuinely not reported. */
  | "not_disclosed";

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

  /* Segmentation. Null for a consolidated figure, which is what the sum of its
   * segments must match. Axis and member are set together or not at all. */
  segmentAxis: string | null;
  segmentMember: string | null;
  segmentLabel: string | null;

  /* Provenance — all required. */
  tier: SourceTier;
  sourceType: SourceType;
  sourceUrl: string;
  accessionNo: string;
  filedDate: string;
  extractionMethod: ExtractionMethod;
  /**
   * How far down the tag fallback ladder the figure was found, from 0 to 1.
   * 1 is an exact hit on the metric's preferred tag; a "not_disclosed" marker
   * carries 0, because there is no value to be confident about.
   */
  confidence: number;

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

/* ===========================================================================
   Request contracts
   =========================================================================== */

/**
 * How far the pipeline goes. Depth changes how many periods are extracted and
 * which modules run — it never changes the provenance rules.
 */
export type AnalysisDepth = "brief" | "standard" | "full";

export interface DepthOption {
  value: AnalysisDepth;
  label: string;
  /** One line, sentence case, stating what the reader gets. */
  summary: string;
  /** Rendered in the data face beside the label. */
  periodsLabel: string;
}

/** A ticker-index entry offered as an autocomplete suggestion. */
export interface TickerSuggestion {
  cik: string;
  ticker: string;
  name: string;
  /** Present when the index knows it; drives the 20-F / 40-F hint. */
  filerType: FilerType | null;
}

/* ===========================================================================
   Progress feed
   =========================================================================== */

/**
 * A pipeline step, one line in the progress feed. Steps are emitted by the
 * backend as they start and again as they settle; the feed is append-then-
 * update, never a spinner.
 */
export type StepState = "pending" | "running" | "done" | "skipped" | "failed";

export interface ProgressStep {
  /** Module scope, e.g. "m03". Rendered in the data face. */
  module: string;
  /** What the step is doing, sentence case: "Extracting XBRL figures". */
  label: string;
  state: StepState;
  /**
   * The real count this step produced — 142 facts, 31 filings, 6 segments.
   * Null until the step settles. Never a percentage, never a guess.
   */
  count: number | null;
  /** Noun the count applies to: "facts", "filings". Null when count is null. */
  countLabel: string | null;
  /** ISO timestamp the step entered its current state. */
  at: string;
  /**
   * Present when state is "skipped" or "failed". States what happened, in the
   * interface's voice — a skipped optional source is not an error.
   */
  detail: string | null;
}

/* ===========================================================================
   Report document
   =========================================================================== */

/** The seven sections, in brief order. */
export const SECTION_ORDER = [
  "snapshot",
  "business",
  "financials",
  "analysis",
  "peers",
  "developments",
  "risks",
] as const;

export type SectionId = (typeof SECTION_ORDER)[number];

/**
 * One paragraph of generated prose.
 *
 * `factIds` names every fact the paragraph rests on. The renderer turns them
 * into superscript markers; a paragraph with no facts carries no markers and
 * makes no numeric claim.
 */
export interface ProseBlock {
  id: string;
  text: string;
  factIds: readonly string[];
}

/** A labelled figure in a section table, bound to the fact behind it. */
export interface FigureRow {
  label: string;
  /** Fact ids, one per column, aligned with `ReportSection.periods`. */
  factIds: readonly (string | null)[];
  /** Set on rows that are a percentage or ratio rather than a currency. */
  emphasis?: "total" | "derived";
}

export interface FigureTable {
  id: string;
  caption: string;
  /** Column headers, in the data face: "FY2023", "FY2024", "FY2025". */
  periods: readonly string[];
  rows: readonly FigureRow[];
  /** e.g. "USD millions, except per-share amounts". */
  unitNote: string;
}

/** An 8-K / 6-K entry in the developments timeline. */
export interface DevelopmentEvent {
  id: string;
  date: string;
  form: string;
  /** EDGAR item numbers, as filed. */
  items: readonly string[];
  headline: string;
  factIds: readonly string[];
}

/** One risk, as the filer discloses it. Never paraphrased into a judgement. */
export interface RiskItem {
  id: string;
  heading: string;
  summary: string;
  factIds: readonly string[];
}

export interface ReportSection {
  id: SectionId;
  title: string;
  /** Rendered when the section could not be built. Never a blank section. */
  unavailableReason: string | null;
  prose: readonly ProseBlock[];
  tables: readonly FigureTable[];
  events: readonly DevelopmentEvent[];
  risks: readonly RiskItem[];
}

/* ===========================================================================
   Chart series

   Values arrive in their display scale, already computed in m07. The browser
   plots them and formats them; it never divides, sums or derives a figure.
   =========================================================================== */

export interface SeriesMeta {
  /** Axis label, e.g. "USD millions". */
  unitLabel: string;
  /** Fact ids backing the series, so a chart can cite like any other figure. */
  factIds: readonly string[];
}

export interface RevenueMarginPoint {
  period: string;
  revenue: number | null;
  grossMarginPct: number | null;
  operatingMarginPct: number | null;
}

export interface SegmentMixPoint {
  period: string;
  /** Segment name to value, in the display scale. Missing key = not disclosed. */
  values: Readonly<Record<string, number>>;
}

export interface CashFlowPoint {
  period: string;
  operatingCashFlow: number | null;
  /** Reported as a positive outflow; the chart plots it below the axis. */
  capex: number | null;
  freeCashFlow: number | null;
}

export interface PeerValuationPoint {
  ticker: string;
  name: string;
  /** True for the company this report is about. Highlighted, never re-ordered. */
  isSubject: boolean;
  evToEbitda: number | null;
  priceToEarnings: number | null;
}

export interface ReportCharts {
  revenueMargin: {
    meta: SeriesMeta;
    points: readonly RevenueMarginPoint[];
  } | null;
  segmentMix: {
    meta: SeriesMeta;
    /** Draw order, bottom to top. Fixed by the backend so colours are stable. */
    segments: readonly string[];
    points: readonly SegmentMixPoint[];
  } | null;
  cashFlow: {
    meta: SeriesMeta;
    points: readonly CashFlowPoint[];
  } | null;
  /**
   * Tier 3. Absent whenever market data is unavailable — the report still
   * renders, it simply has no valuation comparison.
   */
  peerValuation: {
    meta: SeriesMeta;
    points: readonly PeerValuationPoint[];
  } | null;
}

/* ===========================================================================
   Compliance
   =========================================================================== */

/**
 * Counted by m11 at verification time, not by the browser. The strip renders
 * these counts; it does not derive them.
 */
export interface ComplianceSummary {
  factCount: number;
  /** Facts per tier, keyed by tier number. */
  tierCounts: Readonly<Record<SourceTier, number>>;
  /** Figures rendered in the report that carry a citation. */
  citedFigureCount: number;
  figureCount: number;
  /** Preformatted by the backend, e.g. "100%". Never recomputed here. */
  coverageDisplay: string;
  /** 0–1, for the bar geometry only. */
  coverageRatio: number;
  /** True when every figure in the report resolved to a Tier 1 or 2 source. */
  passed: boolean;
  verifiedAt: string;
}

/* ===========================================================================
   The document
   =========================================================================== */

/**
 * Everything the report view renders. Assembled by the backend; the browser
 * treats it as read-only.
 */
export interface ReportDocument {
  report: Report;
  company: Company;
  depth: AnalysisDepth;
  /** Every fact cited anywhere in the document, in first-appearance order. */
  facts: readonly Fact[];
  /**
   * The filing manifest from m02. The provenance rail joins each fact's
   * accession number against this to name the form behind it.
   */
  filings: readonly FilingRef[];
  sections: readonly ReportSection[];
  charts: ReportCharts;
  compliance: ComplianceSummary;
}
