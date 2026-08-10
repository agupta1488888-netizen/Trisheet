"use client";

/**
 * Where a reader moves an assumption and sees what it does.
 *
 * Appended to the record rather than part of it: the seven numbered sections
 * are what the filings say, and this is what follows from them under stated
 * assumptions. It carries no section number for the same reason.
 *
 * State lives in the query string, so a scenario can be sent to someone. The
 * first response is fetched on the server from the URL the reader arrived on,
 * which is what stops a shared link flashing the defaults before correcting
 * itself.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  VALUATION_DEBOUNCE_MS,
  WORKBENCH_ASSUMPTION_NOTE,
  WORKBENCH_HEADING,
  WORKBENCH_NOTE,
  COPY_CONFIRMATION_MS,
} from "@/lib/constants";
import { fetchValuation } from "@/lib/api";
import { DEFAULT_ASSUMPTIONS, isDefault, toQuery } from "@/lib/valuation-url";
import type {
  ValuationAssumptions,
  ValuationFigure,
  ValuationResponse,
} from "@/lib/types";
import { AssumptionFigure } from "@/components/report/workbench/assumption-figure";
import { AssumptionInputs } from "@/components/report/workbench/assumption-inputs";
import { ImpliedGrowth } from "@/components/report/workbench/implied-growth";
import { ScenarioComparison } from "@/components/report/workbench/scenario-comparison";
import { SensitivityGrid } from "@/components/report/workbench/sensitivity-grid";

export const WORKBENCH_SECTION_ID = "valuation-workbench";

export function ValuationWorkbench({
  reportId,
  initial,
  initialAssumptions,
  onInputsChange,
}: {
  reportId: string;
  initial: ValuationResponse | null;
  initialAssumptions: ValuationAssumptions;
  /** Lets the page merge this section's real facts into the rail. */
  onInputsChange?: (response: ValuationResponse) => void;
}) {
  const [assumptions, setAssumptions] =
    useState<ValuationAssumptions>(initialAssumptions);
  const [response, setResponse] = useState<ValuationResponse | null>(initial);
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  const [copied, setCopied] = useState(false);
  // The first render already has the server's answer for this URL. Fetching
  // again on mount would spend a request to learn what is already on screen.
  const settled = useRef(true);

  // Held in a ref, and deliberately not in the effect's dependencies.
  //
  // The parent hands this down as an inline closure, so its identity changes
  // on every parent render — and the callback itself re-renders the parent, by
  // handing it the facts to merge into the rail. As a dependency it would
  // therefore drive a loop: fetch, merge, re-render, new identity, fetch. A
  // ref makes the effect depend only on what should actually re-run it, which
  // is the assumptions, whatever the parent does with its own rendering.
  const notify = useRef(onInputsChange);
  useEffect(() => {
    notify.current = onInputsChange;
  }, [onInputsChange]);

  useEffect(() => {
    if (settled.current) {
      settled.current = false;
      return;
    }

    const query = toQuery(assumptions);
    // replaceState, not the router: this route is force-dynamic, so a router
    // navigation would re-run the server component and refetch the whole
    // document on every keystroke. And replace rather than push, so one back
    // press leaves the report instead of walking through thirty edits.
    window.history.replaceState(
      null,
      "",
      query === "" ? window.location.pathname : `?${query}`,
    );

    let cancelled = false;
    setPending(true);
    const timer = window.setTimeout(() => {
      void fetchValuation(reportId, query).then((result) => {
        if (cancelled) {
          return;
        }
        setPending(false);
        if (result.ok) {
          setFailed(false);
          setResponse(result.data);
          notify.current?.(result.data);
        } else {
          setFailed(true);
        }
      });
    }, VALUATION_DEBOUNCE_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [assumptions, reportId]);

  const setMode = useCallback((mode: ValuationAssumptions["mode"]) => {
    setAssumptions((current) => ({ ...current, mode }));
  }, []);

  const notes = response?.notes ?? [WORKBENCH_NOTE];

  const estimate = response?.estimate ?? null;
  const estimateRows: readonly [string, ValuationFigure | null][] =
    estimate === null || estimate.unavailableReason !== null
      ? []
      : [
          ["Enterprise value", estimate.enterpriseValue],
          ["Equity value", estimate.equityValue],
          ["Value per share", estimate.valuePerShare],
        ];

  return (
    <section
      id={WORKBENCH_SECTION_ID}
      aria-labelledby={`${WORKBENCH_SECTION_ID}-heading`}
      className="scroll-mt-24 border-t border-rule pt-8"
    >
      <h2
        id={`${WORKBENCH_SECTION_ID}-heading`}
        className="font-display text-xl text-ink"
      >
        {WORKBENCH_HEADING}
      </h2>

      {notes.map((note) => (
        <p
          key={note.slice(0, 40)}
          className="mt-3 max-w-prose text-sm leading-relaxed text-muted-foreground"
        >
          {note}
        </p>
      ))}

      <div className="mt-8 space-y-10">
        {response === null || response.unavailableReason !== null ? (
          <p className="border-l-2 border-l-market pl-4 text-sm leading-relaxed text-muted-foreground">
            {response?.unavailableReason ??
              "There is not enough disclosed for this filer to build a valuation."}
          </p>
        ) : null}

        {response?.implied != null ? (
          <ImpliedGrowth implied={response.implied} />
        ) : null}

        {estimateRows.length > 0 ? (
          <dl className="border-t border-rule">
            {estimateRows.map(([label, figure]) => (
              <div
                key={label}
                className="flex items-baseline justify-between gap-6 border-b border-rule py-2"
              >
                <dt className="text-sm text-ink">{label}</dt>
                <dd className="text-right">
                  <AssumptionFigure
                    figure={figure}
                    note={WORKBENCH_ASSUMPTION_NOTE}
                  />
                </dd>
              </div>
            ))}
          </dl>
        ) : null}

        <div>
          <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
            <button
              type="button"
              onClick={() =>
                setMode(assumptions.mode === "reverse" ? "forward" : "reverse")
              }
              className="text-sm text-certified underline-offset-4 hover:underline focus-visible:underline"
            >
              {assumptions.mode === "reverse"
                ? "Use your own assumptions"
                : "Back to what the price implies"}
            </button>

            {!isDefault(assumptions) ? (
              <button
                type="button"
                onClick={() => {
                  setAssumptions(DEFAULT_ASSUMPTIONS);
                }}
                className="text-sm text-muted-foreground underline-offset-4 hover:text-ink hover:underline focus-visible:text-ink focus-visible:underline"
              >
                Reset to defaults
              </button>
            ) : null}

            {/* The only place a shareable link is materialised. Assumptions
                are already in the address bar; this saves a reader selecting
                it, and is what makes a scenario sendable. */}
            <button
              type="button"
              onClick={() => {
                void navigator.clipboard
                  .writeText(window.location.href)
                  .then(() => {
                    setCopied(true);
                    window.setTimeout(() => {
                      setCopied(false);
                    }, COPY_CONFIRMATION_MS);
                  })
                  .catch(() => {
                    // A browser that refuses clipboard access is not an error
                    // worth interrupting a reader over — the URL is visible.
                    setCopied(false);
                  });
              }}
              className="text-sm text-muted-foreground underline-offset-4 hover:text-ink hover:underline focus-visible:text-ink focus-visible:underline"
            >
              {copied ? "Link copied" : "Copy link to these assumptions"}
            </button>
          </div>

          {assumptions.mode === "forward" ? (
            <div className="mt-4 max-w-md">
              <AssumptionInputs
                assumptions={assumptions}
                onChange={setAssumptions}
              />
            </div>
          ) : null}
        </div>

        {response?.sensitivity != null ? (
          <SensitivityGrid
            grid={response.sensitivity}
            note={WORKBENCH_ASSUMPTION_NOTE}
          />
        ) : null}

        {response != null ? (
          <ScenarioComparison
            scenarios={response.scenarios}
            note={WORKBENCH_ASSUMPTION_NOTE}
          />
        ) : null}

        <p className="text-xs text-muted-foreground" aria-live="polite">
          {failed
            ? "The valuation service did not answer. The figures above are the last it returned."
            : pending
              ? "Recomputing."
              : ""}
        </p>
      </div>
    </section>
  );
}
