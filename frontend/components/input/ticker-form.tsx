"use client";

/**
 * The input screen's form.
 *
 * One field, one depth choice, one button. The form owns the resolution
 * outcome, because "we found four companies called that" is not an error and
 * should not be rendered as one — it is a question, asked in place.
 *
 * The `search` and `resolve` functions are injected so this component works
 * identically against the backend and against fixtures.
 */

import { useCallback, useId, useState } from "react";

import type { ApiResult } from "@/lib/api";
import { CUSTOM_PERIODS_MAX, CUSTOM_PERIODS_MIN, DEFAULT_DEPTH } from "@/lib/constants";
import type {
  AnalysisDepth,
  ApiError,
  Resolution,
  TickerSuggestion,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { DepthMenu } from "@/components/input/depth-menu";
import { Disambiguation } from "@/components/input/disambiguation";
import { TickerCombobox } from "@/components/input/ticker-combobox";

export interface TickerFormProps {
  search: (query: string) => Promise<readonly TickerSuggestion[]>;
  resolve: (
    query: string,
    depth: AnalysisDepth,
  ) => Promise<ApiResult<Resolution>>;
  /** Called once a single filer is settled on, whether directly or by choice. */
  onResolved: (
    resolution: Resolution,
    depth: AnalysisDepth,
    periods: number | null,
  ) => void;
  /** Fired the moment a report is requested, so the hero can start reading. */
  onSubmitStart?: () => void;
}

function isCustomPeriodsValid(periods: number | null): boolean {
  return (
    periods !== null &&
    Number.isInteger(periods) &&
    periods >= CUSTOM_PERIODS_MIN &&
    periods <= CUSTOM_PERIODS_MAX
  );
}

export function TickerForm({
  search,
  resolve,
  onResolved,
  onSubmitStart,
}: TickerFormProps) {
  const inputId = useId();
  const depthName = useId();

  const [query, setQuery] = useState("");
  const [depth, setDepth] = useState<AnalysisDepth>(DEFAULT_DEPTH);
  const [customPeriods, setCustomPeriods] = useState<number | null>(null);
  const [isResolving, setIsResolving] = useState(false);
  const [resolution, setResolution] = useState<Resolution | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const isCustomDepthInvalid =
    depth === "custom" && !isCustomPeriodsValid(customPeriods);

  const submit = useCallback(
    async (rawQuery: string) => {
      const trimmed = rawQuery.trim();
      if (trimmed === "" || isResolving || isCustomDepthInvalid) {
        return;
      }

      setIsResolving(true);
      setError(null);
      setResolution(null);

      const result = await resolve(trimmed, depth);
      setIsResolving(false);

      if (!result.ok) {
        setError(result.error);
        return;
      }

      if (result.data.outcome === "resolved") {
        onResolved(result.data, depth, depth === "custom" ? customPeriods : null);
        return;
      }

      setResolution(result.data);
    },
    [customPeriods, depth, isCustomDepthInvalid, isResolving, onResolved, resolve],
  );

  const choose = useCallback(
    (_cik: string, ticker: string) => {
      setResolution(null);
      setQuery(ticker);
      void submit(ticker);
    },
    [submit],
  );

  return (
    <>
      {/* One bar: field, depth, action. The label is visually hidden rather
          than deleted — the control still has to announce what it is, but a
          "COMPANY" caption above a search field that already says so in its
          placeholder is redundant at this size. */}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onSubmitStart?.();
          void submit(query);
        }}
        noValidate
      >
        <label htmlFor={inputId} className="sr-only">
          Company ticker or name
        </label>

        <div
          className={cn(
            "flex items-center gap-1 rounded-2xl border border-white/12 bg-white/[0.035] p-1.5 backdrop-blur-xl",
            "shadow-[0_1px_0_0_rgba(255,255,255,0.05)_inset,0_20px_50px_-20px_rgba(0,0,0,0.9)]",
            "transition-colors focus-within:border-white/25 focus-within:bg-white/[0.055] motion-reduce:transition-none",
          )}
        >
          <div className="min-w-0 flex-1">
            <TickerCombobox
              inputId={inputId}
              value={query}
              onValueChange={(next) => {
                setQuery(next);
                setResolution(null);
                setError(null);
              }}
              onSelect={(suggestion: TickerSuggestion) => {
                setQuery(suggestion.ticker);
              }}
              search={search}
              disabled={isResolving}
            />
          </div>

          <span aria-hidden="true" className="h-6 w-px shrink-0 bg-white/10" />

          <DepthMenu
            name={depthName}
            value={depth}
            onChange={setDepth}
            customPeriods={customPeriods}
            onCustomPeriodsChange={setCustomPeriods}
            disabled={isResolving}
          />

          <button
            type="submit"
            disabled={
              isResolving || query.trim() === "" || isCustomDepthInvalid
            }
            className={cn(
              "shrink-0 rounded-xl bg-white px-4 py-2.5 text-sm font-medium whitespace-nowrap text-[#08080a]",
              "transition-[background-color,opacity] hover:bg-white/90 motion-reduce:transition-none",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white/50",
              "disabled:cursor-not-allowed disabled:bg-white/15 disabled:text-white/35",
            )}
          >
            {isResolving ? "Reading filing…" : "Generate report"}
          </button>
        </div>

        <p id={`${inputId}-hint`} className="mt-3 text-[13px] text-white/35">
          US-listed companies, by ticker or name. Every figure traces back to
          an SEC filing.
        </p>
      </form>

      <p className="sr-only" aria-live="polite">
        {isResolving ? "Resolving the query against the EDGAR index." : ""}
      </p>

      {error === null ? null : (
        <section
          aria-labelledby="resolve-error-heading"
          className="mt-5 rounded-xl border border-red-400/20 bg-red-400/[0.06] px-4 py-3"
        >
          <h2
            id="resolve-error-heading"
            className="text-sm font-medium text-red-300"
          >
            {error.message}
          </h2>
          {error.detail === null ? null : (
            <p className="ref mt-1 text-xs text-white/40">
              {error.detail}
            </p>
          )}
        </section>
      )}

      {resolution === null ? null : (
        <Disambiguation
          resolution={resolution}
          onChoose={choose}
          onDismiss={() => {
            setResolution(null);
          }}
        />
      )}
    </>
  );
}
