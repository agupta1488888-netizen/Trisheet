/**
 * ============================================================================
 * FIXTURE DATA. NOT A GENERATED REPORT.
 * See the notice in `lib/mock/factory.ts`.
 * ============================================================================
 *
 * A scripted run of the pipeline, used to verify the progress feed without a
 * backend. The counts are what the feed exists to show: a step that has
 * settled says what it produced.
 */

import type { ProgressStep, ReportStatus } from "@/lib/types";

/** One frame of the scripted run: the steps as they stood at that moment. */
export interface ProgressFrame {
  status: ReportStatus;
  steps: readonly ProgressStep[];
  /** Milliseconds to hold this frame before advancing. */
  holdMs: number;
}

const BASE_TIME = Date.parse("2025-11-28T14:31:52Z");

/** Timestamps advance in real seconds so the feed reads like a live run. */
function at(secondsIn: number): string {
  return new Date(BASE_TIME + secondsIn * 1_000).toISOString();
}

function step(
  module: string,
  label: string,
  state: ProgressStep["state"],
  secondsIn: number,
  count: number | null = null,
  countLabel: string | null = null,
  detail: string | null = null,
): ProgressStep {
  return { module, label, state, count, countLabel, at: at(secondsIn), detail };
}

const RESOLVE_DONE = step(
  "m01",
  "Resolving ticker to a filer",
  "done",
  3,
  1,
  "match",
);
const DISCOVERY_DONE = step(
  "m02",
  "Building the filing manifest",
  "done",
  9,
  31,
  "filings",
);
const FINANCIALS_DONE = step(
  "m03",
  "Extracting XBRL figures",
  "done",
  24,
  142,
  "figures",
);
const NARRATIVE_DONE = step(
  "m04",
  "Reading narrative sections",
  "done",
  33,
  4,
  "sections",
);
const MARKET_DONE = step("m05", "Fetching market data", "done", 36, 5, "quotes");
const FACTSTORE_DONE = step(
  "m06",
  "Writing facts with provenance",
  "done",
  41,
  138,
  "facts",
  "4 figures discarded: no resolvable source",
);
const ANALYSIS_DONE = step(
  "m07",
  "Computing margins, growth and cash flow",
  "done",
  48,
  27,
  "derived figures",
);
const PEERS_DONE = step("m08", "Selecting peers", "done", 54, 5, "companies");
const DEVELOPMENTS_DONE = step(
  "m09",
  "Assembling the 8-K timeline",
  "done",
  58,
  9,
  "events",
);
const WRITER_DONE = step("m10", "Writing prose", "done", 78, 11, "paragraphs");
const FACTCHECK_DONE = step(
  "m11",
  "Verifying every figure against its source",
  "done",
  88,
  68,
  "figures verified",
);
const ASSEMBLER_DONE = step("m12", "Assembling the report", "done", 92, 7, "sections");

/**
 * The script. Each frame is the full step list as it stood, because the feed
 * updates a line in place when a step settles rather than appending twice.
 */
export const PROGRESS_SCRIPT: readonly ProgressFrame[] = [
  {
    status: "queued",
    holdMs: 900,
    steps: [step("m01", "Resolving ticker to a filer", "running", 0)],
  },
  {
    status: "resolving",
    holdMs: 1_100,
    steps: [
      RESOLVE_DONE,
      step("m02", "Building the filing manifest", "running", 3),
    ],
  },
  {
    status: "extracting",
    holdMs: 1_400,
    steps: [
      RESOLVE_DONE,
      DISCOVERY_DONE,
      step("m03", "Extracting XBRL figures", "running", 9),
    ],
  },
  {
    status: "extracting",
    holdMs: 1_200,
    steps: [
      RESOLVE_DONE,
      DISCOVERY_DONE,
      FINANCIALS_DONE,
      step("m04", "Reading narrative sections", "running", 24),
      step("m05", "Fetching market data", "running", 24),
    ],
  },
  {
    status: "extracting",
    holdMs: 1_200,
    steps: [
      RESOLVE_DONE,
      DISCOVERY_DONE,
      FINANCIALS_DONE,
      NARRATIVE_DONE,
      MARKET_DONE,
      step("m06", "Writing facts with provenance", "running", 36),
    ],
  },
  {
    status: "analysing",
    holdMs: 1_300,
    steps: [
      RESOLVE_DONE,
      DISCOVERY_DONE,
      FINANCIALS_DONE,
      NARRATIVE_DONE,
      MARKET_DONE,
      FACTSTORE_DONE,
      step("m07", "Computing margins, growth and cash flow", "running", 41),
    ],
  },
  {
    status: "analysing",
    holdMs: 1_200,
    steps: [
      RESOLVE_DONE,
      DISCOVERY_DONE,
      FINANCIALS_DONE,
      NARRATIVE_DONE,
      MARKET_DONE,
      FACTSTORE_DONE,
      ANALYSIS_DONE,
      PEERS_DONE,
      step("m09", "Assembling the 8-K timeline", "running", 54),
    ],
  },
  {
    status: "writing",
    holdMs: 1_800,
    steps: [
      RESOLVE_DONE,
      DISCOVERY_DONE,
      FINANCIALS_DONE,
      NARRATIVE_DONE,
      MARKET_DONE,
      FACTSTORE_DONE,
      ANALYSIS_DONE,
      PEERS_DONE,
      DEVELOPMENTS_DONE,
      step("m10", "Writing prose", "running", 58),
    ],
  },
  {
    status: "verifying",
    holdMs: 1_500,
    steps: [
      RESOLVE_DONE,
      DISCOVERY_DONE,
      FINANCIALS_DONE,
      NARRATIVE_DONE,
      MARKET_DONE,
      FACTSTORE_DONE,
      ANALYSIS_DONE,
      PEERS_DONE,
      DEVELOPMENTS_DONE,
      WRITER_DONE,
      step("m11", "Verifying every figure against its source", "running", 78),
    ],
  },
  {
    status: "complete",
    holdMs: Number.POSITIVE_INFINITY,
    steps: [
      RESOLVE_DONE,
      DISCOVERY_DONE,
      FINANCIALS_DONE,
      NARRATIVE_DONE,
      MARKET_DONE,
      FACTSTORE_DONE,
      ANALYSIS_DONE,
      PEERS_DONE,
      DEVELOPMENTS_DONE,
      WRITER_DONE,
      FACTCHECK_DONE,
      ASSEMBLER_DONE,
    ],
  },
];

/**
 * The degraded run: market data is unreachable and the step is skipped, not
 * failed. The report still completes — only SEC EDGAR is a hard dependency.
 */
export const PROGRESS_SCRIPT_DEGRADED: readonly ProgressFrame[] = [
  {
    status: "extracting",
    holdMs: 1_200,
    steps: [
      RESOLVE_DONE,
      DISCOVERY_DONE,
      step("m03", "Extracting XBRL figures", "running", 9),
    ],
  },
  {
    status: "analysing",
    holdMs: 1_400,
    steps: [
      RESOLVE_DONE,
      DISCOVERY_DONE,
      FINANCIALS_DONE,
      NARRATIVE_DONE,
      step(
        "m05",
        "Fetching market data",
        "skipped",
        36,
        null,
        null,
        "Provider did not respond. The report continues without a valuation comparison.",
      ),
      step("m06", "Writing facts with provenance", "running", 36),
    ],
  },
  {
    status: "complete",
    holdMs: Number.POSITIVE_INFINITY,
    steps: [
      RESOLVE_DONE,
      DISCOVERY_DONE,
      FINANCIALS_DONE,
      NARRATIVE_DONE,
      step(
        "m05",
        "Fetching market data",
        "skipped",
        36,
        null,
        null,
        "Provider did not respond. The report continues without a valuation comparison.",
      ),
      FACTSTORE_DONE,
      ANALYSIS_DONE,
      step(
        "m08",
        "Selecting peers",
        "skipped",
        54,
        null,
        null,
        "Peer multiples need market data, which was unavailable.",
      ),
      DEVELOPMENTS_DONE,
      WRITER_DONE,
      FACTCHECK_DONE,
      ASSEMBLER_DONE,
    ],
  },
];

/** A run that stops. The interface says what happened and what to do next. */
export const PROGRESS_SCRIPT_FAILED: readonly ProgressFrame[] = [
  {
    status: "resolving",
    holdMs: 1_200,
    steps: [
      RESOLVE_DONE,
      step("m02", "Building the filing manifest", "running", 3),
    ],
  },
  {
    status: "failed",
    holdMs: Number.POSITIVE_INFINITY,
    steps: [
      RESOLVE_DONE,
      step(
        "m02",
        "Building the filing manifest",
        "failed",
        11,
        0,
        "filings",
        "No annual filing found for this CIK.",
      ),
    ],
  },
];

export const FAILED_REPORT_MESSAGE =
  "No annual filing found for this CIK. The company may file under a different entity — try searching by company name.";
