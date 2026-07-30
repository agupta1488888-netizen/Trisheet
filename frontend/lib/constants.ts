/**
 * Interface constants.
 *
 * Every literal the interface renders lives here rather than inline in a
 * component, so that copy is reviewable in one place and nothing is hardcoded
 * to a single company.
 */

import type { AnalysisDepth, DepthOption, SectionId } from "@/lib/types";

/** Missing data reads this. Never "N/A", never blank, never zero. */
export const NOT_DISCLOSED = "Not disclosed";

/** Section headings, in brief order. */
export const SECTION_TITLE: Readonly<Record<SectionId, string>> = {
  snapshot: "Snapshot",
  business: "Business",
  financials: "Financial highlights",
  analysis: "Analysis",
  peers: "Peers and valuation",
  developments: "Recent developments",
  risks: "Risk factors",
};

/** Short labels for the in-page section navigation. */
export const SECTION_NAV_LABEL: Readonly<Record<SectionId, string>> = {
  snapshot: "Snapshot",
  business: "Business",
  financials: "Financials",
  analysis: "Analysis",
  peers: "Peers",
  developments: "Developments",
  risks: "Risks",
};

export const DEFAULT_DEPTH: AnalysisDepth = "standard";

export const DEPTH_OPTIONS: readonly DepthOption[] = [
  {
    value: "brief",
    label: "Brief",
    summary: "Headline figures and the latest annual filing.",
    periodsLabel: "3y",
  },
  {
    value: "standard",
    label: "Standard",
    summary: "Segment detail, peer set and the developments timeline.",
    periodsLabel: "5y",
  },
  {
    value: "full",
    label: "Full",
    summary: "Every disclosed period, narrative sections and risk factors.",
    periodsLabel: "10y",
  },
];

/**
 * Offered as starting points, not defaults. Deliberately spans a domestic
 * filer, a foreign private issuer, a Canadian MJDS filer and a non-technology
 * sector, so the demonstration is never mistaken for one hardcoded company.
 *
 * The forms below are the annual report each filer most recently filed, taken
 * from the live EDGAR verification recorded in the changelog. They are a hint
 * on the chip and nothing more — m01 decides filer type from the filing itself,
 * never from this list.
 */
export interface ExampleTicker {
  ticker: string;
  name: string;
  /** The annual form this filer most recently filed. */
  form: string;
}

export const EXAMPLE_TICKERS: readonly ExampleTicker[] = [
  { ticker: "AAPL", name: "Apple Inc.", form: "10-K" },
  { ticker: "KO", name: "The Coca-Cola Company", form: "10-K" },
  { ticker: "TSM", name: "Taiwan Semiconductor Manufacturing", form: "20-F" },
  { ticker: "CNI", name: "Canadian National Railway Company", form: "40-F" },
];

/** Minimum characters before the ticker field asks the resolver for matches. */
export const AUTOCOMPLETE_MIN_CHARS = 1;

/** Suggestions shown at once. More than this and the list stops being scannable. */
export const AUTOCOMPLETE_MAX_RESULTS = 7;

/** Keystrokes settle for this long before a suggestion request is issued. */
export const AUTOCOMPLETE_DEBOUNCE_MS = 180;

/** Fallback poll interval when Supabase Realtime is unavailable. */
export const PROGRESS_POLL_INTERVAL_MS = 2_000;

/** Tier names, as the compliance strip and rail render them. */
export const TIER_NAME = {
  1: "Filings",
  2: "Company",
  3: "Market",
  4: "News",
} as const;
