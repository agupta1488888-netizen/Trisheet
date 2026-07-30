"use client";

/**
 * The input screen.
 *
 * Restrained by intent: no hero, no gradient, no illustration. A masthead, a
 * field, a depth choice, four examples. The page's only job is to take a
 * ticker and get out of the way.
 *
 * The data functions are injected, so the same screen runs against the backend
 * and against fixtures without a branch inside it.
 */

import { useCallback } from "react";
import { useRouter } from "next/navigation";

import { AUTOCOMPLETE_MAX_RESULTS } from "@/lib/constants";
import { createReport, resolveTicker, searchTickers } from "@/lib/api";
import type { ApiResult } from "@/lib/api";
import type {
  AnalysisDepth,
  Report,
  Resolution,
  TickerSuggestion,
} from "@/lib/types";
import { TickerForm } from "@/components/input/ticker-form";

export interface InputScreenProps {
  search?: (query: string) => Promise<readonly TickerSuggestion[]>;
  resolve?: (
    query: string,
    depth: AnalysisDepth,
  ) => Promise<ApiResult<Resolution>>;
  /** Injected so the preview harness can show the outcome without navigating. */
  onStart?: (resolution: Resolution, depth: AnalysisDepth) => void;
}

function defaultSearch(query: string): Promise<readonly TickerSuggestion[]> {
  return searchTickers(query, AUTOCOMPLETE_MAX_RESULTS);
}

function defaultResolve(query: string): Promise<ApiResult<Resolution>> {
  return resolveTicker(query);
}

export function InputScreen({
  search = defaultSearch,
  resolve = defaultResolve,
  onStart,
}: InputScreenProps) {
  const router = useRouter();

  const start = useCallback(
    (resolution: Resolution, depth: AnalysisDepth) => {
      if (onStart !== undefined) {
        onStart(resolution, depth);
        return;
      }

      const company = resolution.company;
      if (company === null) {
        return;
      }

      void createReport(company.cik, company.ticker, depth).then(
        (result: ApiResult<Report>) => {
          if (result.ok) {
            router.push(`/r/${result.data.id}`);
          }
        },
      );
    },
    [onStart, router],
  );

  return (
    <main
      id="main"
      className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center px-5 py-20 sm:px-8"
    >
      <header>
        <h1 className="text-4xl text-ink">Tearsheet</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Company profiles, sourced from filings.
        </p>
      </header>

      <div className="mt-12 border-t border-rule pt-10">
        <TickerForm search={search} resolve={resolve} onResolved={start} />
      </div>

      <footer className="mt-16 border-t border-rule pt-4">
        <p className="max-w-prose text-xs leading-relaxed text-muted-foreground">
          Figures are extracted from SEC filings and computed in Python. Nothing
          is estimated. A figure that a company does not disclose reads
          &ldquo;Not disclosed&rdquo;, and every number in the finished profile
          carries a link to the filing it came from.
        </p>
      </footer>
    </main>
  );
}
