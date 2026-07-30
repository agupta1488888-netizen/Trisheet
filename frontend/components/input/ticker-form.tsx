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
import { DEFAULT_DEPTH } from "@/lib/constants";
import type {
  AnalysisDepth,
  ApiError,
  Resolution,
  TickerSuggestion,
} from "@/lib/types";
import { DepthSelector } from "@/components/input/depth-selector";
import { Disambiguation } from "@/components/input/disambiguation";
import { ExampleChips } from "@/components/input/example-chips";
import { TickerCombobox } from "@/components/input/ticker-combobox";

export interface TickerFormProps {
  search: (query: string) => Promise<readonly TickerSuggestion[]>;
  resolve: (
    query: string,
    depth: AnalysisDepth,
  ) => Promise<ApiResult<Resolution>>;
  /** Called once a single filer is settled on, whether directly or by choice. */
  onResolved: (resolution: Resolution, depth: AnalysisDepth) => void;
}

export function TickerForm({ search, resolve, onResolved }: TickerFormProps) {
  const inputId = useId();
  const depthName = useId();

  const [query, setQuery] = useState("");
  const [depth, setDepth] = useState<AnalysisDepth>(DEFAULT_DEPTH);
  const [isResolving, setIsResolving] = useState(false);
  const [resolution, setResolution] = useState<Resolution | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const submit = useCallback(
    async (rawQuery: string) => {
      const trimmed = rawQuery.trim();
      if (trimmed === "" || isResolving) {
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
        onResolved(result.data, depth);
        return;
      }

      setResolution(result.data);
    },
    [depth, isResolving, onResolved, resolve],
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
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit(query);
        }}
        noValidate
      >
        <label
          htmlFor={inputId}
          className="text-[0.68rem] text-muted-foreground"
        >
          Company
        </label>

        <div className="mt-2">
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

        <p id={`${inputId}-hint`} className="mt-2 text-xs text-muted-foreground">
          US-listed companies, by ticker or name. Every figure in the profile
          traces back to an SEC filing.
        </p>

        <div className="mt-10">
          <DepthSelector
            name={depthName}
            value={depth}
            onChange={setDepth}
            disabled={isResolving}
          />
        </div>

        <div className="mt-10">
          <button
            type="submit"
            disabled={isResolving || query.trim() === ""}
            className="border border-certified bg-certified px-5 py-2 text-sm text-paper transition-colors hover:border-ink hover:bg-ink disabled:cursor-not-allowed disabled:border-rule disabled:bg-transparent disabled:text-muted-foreground motion-reduce:transition-none"
          >
            {isResolving ? "Resolving…" : "Build profile"}
          </button>
        </div>

        <div className="mt-10 border-t border-rule pt-4">
          <ExampleChips
            disabled={isResolving}
            onPick={(ticker) => {
              setQuery(ticker);
              setResolution(null);
              setError(null);
            }}
          />
        </div>
      </form>

      <p className="sr-only" aria-live="polite">
        {isResolving ? "Resolving the query against the EDGAR index." : ""}
      </p>

      {error === null ? null : (
        <section
          aria-labelledby="resolve-error-heading"
          className="mt-8 border-t-2 border-t-flag pt-4"
        >
          <h2
            id="resolve-error-heading"
            className="text-sm font-medium text-flag"
          >
            {error.message}
          </h2>
          {error.detail === null ? null : (
            <p className="ref mt-1 text-xs text-muted-foreground">
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
