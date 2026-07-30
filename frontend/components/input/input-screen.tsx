"use client";

/**
 * The input screen.
 *
 * Explicit design direction as of 2026-07-31: a colourful gradient hero, a
 * decorative illustration, rounded cards with shadows — a mainstream
 * "premium SaaS template" look, chosen after the prior hairline/no-gradient
 * version read as flat. See CLAUDE.md's design system section, updated in
 * the same change, for the rules this now actually follows.
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
import { HeroIllustration } from "@/components/input/hero-illustration";

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
    <main id="main" className="min-h-screen bg-slate-50">
      <div className="bg-gradient-to-br from-emerald-600 via-teal-600 to-blue-700">
        <div className="mx-auto max-w-6xl px-5 py-14 sm:px-8 sm:py-20">
          <header className="flex items-center gap-2.5">
            <span
              aria-hidden="true"
              className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-white/15 text-sm font-semibold text-white backdrop-blur"
            >
              T
            </span>
            <span className="text-lg font-medium text-white">Trisheet</span>
          </header>

          <div className="mt-12 grid grid-cols-1 items-center gap-12 lg:grid-cols-2 lg:gap-16">
            <div>
              <p className="text-xs font-semibold tracking-wide text-emerald-100 uppercase">
                Equity research · sourced from filings
              </p>
              <h1 className="mt-4 text-4xl leading-tight font-semibold text-white sm:text-5xl">
                The company profile that shows its work.
              </h1>
              <p className="mt-5 max-w-xl text-base leading-relaxed text-emerald-50/90">
                Every figure traces back to the SEC filing it came from — the
                accession number, the page, the exact tag. Nothing estimated.
                Nothing recalled from memory.
              </p>
            </div>

            <HeroIllustration />
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-3xl px-5 sm:px-8">
        <div className="-mt-10 rounded-3xl border border-slate-100 bg-white p-6 shadow-2xl shadow-slate-300/50 sm:-mt-14 sm:p-10">
          <TickerForm search={search} resolve={resolve} onResolved={start} />
        </div>
      </div>

      <div className="mx-auto max-w-3xl px-5 py-16 sm:px-8 sm:py-20">
        <ReportPreview />

        <div className="mt-16">
          <TrustSection />
        </div>

        <footer className="mt-16 border-t border-slate-200 pt-6">
          <p className="max-w-prose text-xs leading-relaxed text-muted-foreground">
            Figures are extracted from SEC filings and computed in Python.
            Nothing is estimated. A figure that a company does not disclose
            reads &ldquo;Not disclosed&rdquo;, and every number in the
            finished profile carries a link to the filing it came from.
          </p>
        </footer>
      </div>
    </main>
  );
}
