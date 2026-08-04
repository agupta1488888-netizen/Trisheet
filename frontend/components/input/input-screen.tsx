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
 * As of 2026-08-04, that background is a three.js scene (`HeroCanvas`)
 * rather than the static photo it replaced — filing pages suspended at
 * varying depths, with threads of light carrying a figure back to the page
 * it came from. It is in the same cinematic register as the assistant
 * showcase below the fold, and on the same two-colour palette, so the two no
 * longer read as different design languages stacked on one page. Loaded
 * through `next/dynamic` with `ssr: false` so three.js and the
 * post-processing chain never enter the server-rendered payload or block
 * first paint.
 *
 * Below the fold, as of 2026-08-03: the assistant showcase, in the same
 * exception zone as the hero above it.
 *
 * The data functions are injected, so the same screen runs against the backend
 * and against fixtures without a branch inside it.
 */

import { useCallback } from "react";
import dynamic from "next/dynamic";
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
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { AiFinancialAgentSection } from "@/components/input/ai-financial-agent-section";
import { TickerForm } from "@/components/input/ticker-form";

const HeroCanvas = dynamic(
  () => import("@/components/input/hero-canvas").then((mod) => mod.HeroCanvas),
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

  const start = useCallback(
    (resolution: Resolution, depth: AnalysisDepth, periods: number | null) => {
      if (onStart !== undefined) {
        onStart(resolution, depth, periods);
        return;
      }

      const company = resolution.company;
      if (company === null) {
        return;
      }

      void createReport(company.cik, company.ticker, depth, periods).then(
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
    <main id="main">
      <div className="relative min-h-screen overflow-hidden bg-slate-950">
        <div
          className="absolute inset-0 animate-in fade-in duration-1000"
          aria-hidden="true"
        >
          <HeroCanvas reducedMotion={prefersReducedMotion} />
        </div>
        {/* A light overall wash for depth. The heavy lifting is done by the
            content-sized scrim below, not here — a viewport-percentage
            gradient cannot track the content column, whose right edge moves
            between roughly 59% and 73% of the window depending on width. */}
        <div
          aria-hidden="true"
          className="absolute inset-0 bg-gradient-to-r from-slate-950/70 from-30% to-slate-950/10 to-85%"
        />

        {/* Left-aligned rather than centred in a max-w-6xl container. Centring
            put the form card and headline across the middle of the screen and
            squeezed the canvas into a sliver down the right edge; anchoring
            the pair to the left hands the whole right third back to the
            scene, which is the only place it is visible at all. */}
        <div className="relative flex min-h-screen w-full items-center px-5 py-16 sm:px-8 sm:py-24 lg:pl-16 xl:pl-24">
          <div className="relative grid w-full grid-cols-1 items-center gap-12 lg:max-w-5xl lg:grid-cols-2 lg:gap-14">
            {/* Opaque backing for the text, sized to this column rather than
                to a percentage of the viewport, so it ends exactly where the
                content ends at every width. The scene drifts filing pages
                across the whole frame; without this they read straight
                through the headline. Two elements: a solid panel running off
                the left of the screen, then a fixed-width fade so the scene
                emerges rather than starting at a hard vertical seam. The
                vertical inset overshoots and is clipped by the hero's
                `overflow-hidden`, since this grid is only as tall as its
                content but the scene fills the whole hero. */}
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-y-[-100vh] -left-[100vw] right-0 hidden bg-slate-950 lg:block"
            />
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-y-[-100vh] left-full hidden w-32 bg-gradient-to-r from-slate-950 to-transparent lg:block"
            />

            <div className="relative lg:order-1">
              {/* Padding is load-bearing, not decoration: the depth selector
                  inside switches to two columns at an `@sm` (384px)
                  *container* width. In this 484px column, p-14 left the
                  fieldset at 372px — just under the threshold — which
                  collapsed the options into one tall stack and pushed the
                  card past the bottom of the viewport. Do not raise this
                  past p-12 without re-measuring the fieldset. */}
              <div className="rounded-3xl border border-slate-100 bg-white p-6 shadow-2xl shadow-slate-950/40 sm:p-7">
                <TickerForm search={search} resolve={resolve} onResolved={start} />
              </div>
            </div>

            <div className="relative lg:order-2">
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

              <p className="mt-8 text-xs font-semibold tracking-wide text-emerald-200 uppercase">
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
          </div>
        </div>
      </div>

      <AiFinancialAgentSection />
    </main>
  );
}
