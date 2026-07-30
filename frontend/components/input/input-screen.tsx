"use client";

/**
 * The input screen.
 *
 * A masthead, a hero statement, the field, a sample of the finished product,
 * and the three claims a reader can go verify. Every visual choice still
 * answers to the same rule as the report itself: hairlines instead of cards,
 * tabular monospace figures, no gradient, no glow — a research document's
 * idea of premium, not a SaaS landing page's.
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
import { ReportPreview } from "@/components/input/report-preview";
import { TrustSection } from "@/components/input/trust-section";

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
      className="mx-auto flex max-w-3xl flex-col px-5 py-16 sm:px-8 sm:py-20"
    >
      <header className="flex items-center gap-2.5">
        <span
          aria-hidden="true"
          className="ref flex size-7 shrink-0 items-center justify-center border border-ink text-sm text-ink"
        >
          T
        </span>
        <span className="text-lg text-ink">Trisheet</span>
      </header>

      <div className="mt-14 sm:mt-20">
        <p className="text-[0.72rem] tracking-wide text-certified uppercase">
          Equity research · sourced from filings
        </p>
        <h1 className="mt-3 max-w-2xl text-4xl leading-tight text-ink sm:text-5xl">
          The company profile that shows its work.
        </h1>
        <p className="mt-4 max-w-xl text-base leading-relaxed text-muted-foreground">
          Every figure traces back to the SEC filing it came from — the
          accession number, the page, the exact tag. Nothing estimated.
          Nothing recalled from memory.
        </p>
      </div>

      <div className="mt-12 max-w-xl border-t border-rule pt-10">
        <TickerForm search={search} resolve={resolve} onResolved={start} />
      </div>

      <div className="mt-16 sm:mt-20">
        <ReportPreview />
      </div>

      <div className="mt-16 sm:mt-20">
        <TrustSection />
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
