"use client";

/**
 * The input screen.
 *
 * Explicit design direction as of 2026-07-31: the hero fills the entire
 * viewport as a full-bleed background, with exactly two things placed on top
 * of it — the build form on the left, the brand mark and headline on the
 * right. Nothing else renders inside that first viewport. See CLAUDE.md's
 * design system section for the rules this follows.
 *
 * As of 2026-08-04, that background is a three.js scene (`MonolithCanvas`)
 * rather than the static photo it replaced — a filing as a physical object,
 * six translucent slabs stacked into one monolith that part on hover and
 * lift in sequence while a report is being requested. It is in the same
 * cinematic register as the assistant showcase below the fold, and
 * monochrome throughout, so the two no longer read as different design
 * languages stacked on one page. Loaded through `next/dynamic` with
 * `ssr: false` so three.js and the post-processing chain never enter the
 * server-rendered payload or block first paint.
 *
 * Below the fold, as of 2026-08-03: the assistant showcase, in the same
 * exception zone as the hero above it.
 *
 * The data functions are injected, so the same screen runs against the backend
 * and against fixtures without a branch inside it.
 */

import { useCallback, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";

import { AUTOCOMPLETE_MAX_RESULTS } from "@/lib/constants";
import { createReport, resolveTicker, searchTickers } from "@/lib/api";
import type { ApiResult } from "@/lib/api";
import type {
  AnalysisDepth,
  ApiError,
  Report,
  Resolution,
  TickerSuggestion,
} from "@/lib/types";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { AiFinancialAgentSection } from "@/components/input/ai-financial-agent-section";
import { MonolithAnnotations } from "@/components/input/monolith-annotations";
import { TickerForm } from "@/components/input/ticker-form";

const MonolithCanvas = dynamic(
  () =>
    import("@/components/input/monolith-canvas").then(
      (mod) => mod.MonolithCanvas,
    ),
  { ssr: false },
);

export interface InputScreenProps {
  search?: (query: string) => Promise<readonly TickerSuggestion[]>;
  resolve?: (
    query: string,
    depth: AnalysisDepth,
  ) => Promise<ApiResult<Resolution>>;
  /** Injected so the preview harness can show the outcome without navigating. */
  onStart?: (
    resolution: Resolution,
    depth: AnalysisDepth,
    periods: number | null,
    sourceUrls: readonly string[],
  ) => void;
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
  const prefersReducedMotion = useReducedMotion();

  // Hover parts the layers; the token runs the reading sequence exactly once
  // per request. A counter rather than a boolean, so a second request while
  // the first is still settling restarts the animation instead of being
  // swallowed by an unchanged prop.
  const [isMonolithHovered, setIsMonolithHovered] = useState(false);
  const [readToken, setReadToken] = useState(0);
  const [startError, setStartError] = useState<ApiError | null>(null);

  const start = useCallback(
    (
      resolution: Resolution,
      depth: AnalysisDepth,
      periods: number | null,
      sourceUrls: readonly string[],
    ) => {
      if (onStart !== undefined) {
        onStart(resolution, depth, periods, sourceUrls);
        return;
      }

      const company = resolution.company;
      if (company === null) {
        return;
      }

      void createReport(
        company.cik,
        company.ticker,
        depth,
        periods,
        sourceUrls,
      ).then((result: ApiResult<Report>) => {
        if (result.ok) {
          router.push(`/r/${result.data.id}`);
          return;
        }
        // Previously discarded, which left a failed queue looking like a
        // button that did nothing. The scene stops reading, and the message
        // is the backend's own — already written in the interface's voice.
        setReadToken(0);
        setStartError(result.error);
      });
    },
    [onStart, router],
  );

  return (
    <main id="main">
      <div className="relative min-h-screen overflow-hidden bg-[#08080a]">
        {/* Two extremely low-contrast washes, and nothing else behind the
            content. A single flat black reads as unfinished at this scale;
            a visible gradient reads as a template. These sit at 4% and 3%. */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(120%_80%_at_75%_15%,rgba(255,255,255,0.04),transparent_60%)]"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/[0.09] to-transparent"
        />

        <header className="relative z-20 mx-auto flex max-w-[1400px] items-center px-6 pt-8 sm:px-10">
          <span className="flex items-center gap-2.5">
            <span
              aria-hidden="true"
              className="flex size-8 items-center justify-center rounded-lg border border-white/12 bg-white/[0.06] text-[13px] font-semibold text-white"
            >
              T
            </span>
            <span className="text-[15px] font-medium tracking-[-0.01em] text-white">
              Trisheet
            </span>
          </span>
        </header>

        <div className="relative mx-auto grid max-w-[1400px] grid-cols-1 items-center gap-14 px-6 pt-14 pb-24 sm:px-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.02fr)] lg:gap-8 lg:pt-8 lg:pb-16">
          <div className="relative z-10 max-w-xl">
            <p className="text-[11px] font-medium tracking-[0.18em] text-white/40 uppercase">
              Equity research • Sourced from filings
            </p>

            {/* font-sans overrides the global rule that sets every h1 in
                Fraunces. The serif is right for the report view, which reads
                as a document; this page has to read as a product, and the
                whole enterprise register the brief asks for is carried by a
                tightly-tracked grotesque. */}
            <h1 className="mt-6 font-sans text-[2.75rem] leading-[1.04] font-semibold tracking-[-0.035em] text-white sm:text-6xl lg:text-[4.15rem]">
              Every filing.
              <br />
              Every number.
              <br />
              <span className="text-white/50">Completely traceable.</span>
            </h1>

            <p className="mt-7 max-w-[30rem] text-[15px] leading-[1.7] text-white/45 sm:text-base">
              Trisheet turns any stock ticker into an institutional-grade
              equity research report. Every metric links back to the exact SEC
              filing, page, accession number and XBRL tag.
            </p>

            <div className="mt-9">
              <TickerForm
                search={search}
                resolve={resolve}
                onResolved={start}
                onSubmitStart={() => {
                  setStartError(null);
                  setReadToken((token) => token + 1);
                }}
              />
            </div>

            {startError === null ? null : (
              <section
                aria-labelledby="start-error-heading"
                className="mt-5 rounded-xl border border-red-400/20 bg-red-400/[0.06] px-4 py-3"
              >
                <h2
                  id="start-error-heading"
                  className="text-sm font-medium text-red-300"
                >
                  {startError.message}
                </h2>
                {startError.detail === null ? null : (
                  <p className="ref mt-1 text-xs text-white/40">
                    {startError.detail}
                  </p>
                )}
              </section>
            )}

            <p className="mt-8 flex items-center gap-2.5 text-[13px] text-white/30">
              <span aria-hidden="true" className="h-px w-6 bg-white/15" />
              Trusted by analysts, investors and financial teams.
            </p>
          </div>

          {/* The object is decorative, so it is hidden from assistive tech —
              but it stays interactive for pointer parallax and the hover
              separation, which is why this is aria-hidden rather than
              pointer-events-none. */}
          <div
            aria-hidden="true"
            className="relative h-[26rem] animate-in fade-in duration-1000 sm:h-[32rem] lg:h-[42rem]"
            onPointerEnter={() => {
              setIsMonolithHovered(true);
            }}
            onPointerLeave={() => {
              setIsMonolithHovered(false);
            }}
          >
            <MonolithCanvas
              reducedMotion={prefersReducedMotion}
              hovered={isMonolithHovered && !prefersReducedMotion}
              readToken={readToken}
            />
            <MonolithAnnotations />
          </div>
        </div>
      </div>

      <AiFinancialAgentSection />
    </main>
  );
}
