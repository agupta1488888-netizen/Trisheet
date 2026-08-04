"use client";

/**
 * The input screen, mounted against the fixture ticker index.
 *
 * The screen component is the one that ships. Only `search` and `resolve` are
 * swapped, which is what makes the autocomplete, the disambiguation surface
 * and the not-found surface checkable before m01 is reachable from the
 * browser.
 */

import { useState } from "react";

import type { ApiResult } from "@/lib/api";
import { AUTOCOMPLETE_MAX_RESULTS } from "@/lib/constants";
import {
  AMBIGUOUS_RESOLUTION,
  NOT_FOUND_RESOLUTION,
  searchTickerIndex,
  TICKER_INDEX,
} from "@/lib/mock/suggestions";
import type {
  AnalysisDepth,
  Resolution,
  TickerSuggestion,
} from "@/lib/types";
import { InputScreen } from "@/components/input/input-screen";
import { PreviewNotice } from "@/components/preview/preview-notice";

/** Stands in for the network, latency included, so debouncing is exercised. */
const LATENCY_MS = 120;

function delay<T>(value: T): Promise<T> {
  return new Promise((resolve) => {
    window.setTimeout(() => {
      resolve(value);
    }, LATENCY_MS);
  });
}

function search(query: string): Promise<readonly TickerSuggestion[]> {
  return delay(searchTickerIndex(query, AUTOCOMPLETE_MAX_RESULTS));
}

/**
 * Fixture resolution. "berkshire" is ambiguous, an unmatched query is not
 * found, and anything in the index resolves to a single filer.
 */
function resolve(query: string): Promise<ApiResult<Resolution>> {
  const needle = query.trim().toLowerCase();

  if (needle.startsWith("berk") || needle.startsWith("brk")) {
    return delay<ApiResult<Resolution>>({
      ok: true,
      data: { ...AMBIGUOUS_RESOLUTION, query },
    });
  }

  const match = TICKER_INDEX.find(
    (entry) =>
      entry.ticker.toLowerCase() === needle ||
      entry.name.toLowerCase() === needle,
  );

  if (match === undefined) {
    return delay<ApiResult<Resolution>>({
      ok: true,
      data: { ...NOT_FOUND_RESOLUTION, query },
    });
  }

  return delay<ApiResult<Resolution>>({
    ok: true,
    data: {
      outcome: "resolved",
      query,
      company: {
        cik: match.cik,
        ticker: match.ticker,
        name: match.name,
        filerType: match.filerType ?? "domestic",
        // Null throughout: the autocomplete index this harness resolves
        // against carries an identity and nothing else. Inventing a
        // headquarters or an employee count here would put a figure on the
        // screen that no filing said, which is the one thing the preview
        // must not rehearse.
        sicCode: null,
        sector: null,
        fiscalYearEnd: null,
        reportingCurrency: null,
        headquarters: null,
        exchange: null,
        stateOfIncorporation: null,
        employees: null,
      },
      candidates: [],
    },
  });
}

export default function PreviewInput() {
  const [started, setStarted] = useState<{
    resolution: Resolution;
    depth: AnalysisDepth;
    periods: number | null;
  } | null>(null);

  return (
    <>
      <PreviewNotice what="The input screen, against the fixture ticker index." />

      <InputScreen
        search={search}
        resolve={resolve}
        onStart={(resolution, depth, periods) => {
          setStarted({ resolution, depth, periods });
        }}
      />

      {started === null ? null : (
        <div
          aria-live="polite"
          className="mx-auto max-w-2xl border-t border-rule px-5 pb-16 sm:px-8"
        >
          <p className="pt-4 text-sm text-ink">
            Resolved to{" "}
            <span className="ref text-certified">
              {started.resolution.company?.ticker}
            </span>{" "}
            · {started.resolution.company?.name}
          </p>
          <p className="ref mt-1 text-xs text-muted-foreground">
            CIK {started.resolution.company?.cik} · {started.depth} analysis
            {started.periods === null ? "" : ` (${started.periods}y)`} · the
            live screen would queue a report and navigate here
          </p>
        </div>
      )}
    </>
  );
}
