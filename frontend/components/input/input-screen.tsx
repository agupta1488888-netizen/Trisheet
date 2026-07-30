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
import Image from "next/image";
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
      <div className="relative overflow-hidden">
        <Image
          src="/hero-background.jpg"
          alt=""
          aria-hidden="true"
          fill
          priority
          sizes="100vw"
          className="object-cover"
        />
        <div className="absolute inset-0 bg-gradient-to-r from-slate-950/95 via-slate-950/78 to-slate-900/40" />

        <div className="relative mx-auto max-w-6xl px-5 py-16 sm:px-8 sm:py-24">
          <header className="flex items-center gap-3.5">
            <span
              aria-hidden="true"
              className="flex size-14 shrink-0 items-center justify-center rounded-2xl border border-white/25 bg-white/15 text-xl font-bold text-white shadow-lg backdrop-blur"
            >
              T
            </span>
            <span className="font-display text-3xl font-semibold tracking-tight text-white sm:text-4xl">
              Trisheet
            </span>
          </header>

          <div className="mt-14 grid grid-cols-1 items-center gap-12 lg:grid-cols-2 lg:gap-16">
            <div>
              <p className="text-xs font-semibold tracking-wide text-emerald-200 uppercase">
                Equity research · sourced from filings
              </p>
              <h1 className="mt-4 text-5xl leading-tight font-semibold text-white sm:text-6xl">
                The company profile that shows its work.
              </h1>
              <p className="mt-5 max-w-xl text-lg leading-relaxed text-slate-100/90">
                Every figure traces back to the SEC filing it came from — the
                accession number, the page, the exact tag. Nothing estimated.
                Nothing recalled from memory.
              </p>
            </div>

            <HeroIllustration />
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-4xl px-5 sm:px-8">
        <div className="-mt-12 rounded-3xl border border-slate-100 bg-white p-8 shadow-2xl shadow-slate-300/50 sm:-mt-16 sm:p-12">
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
