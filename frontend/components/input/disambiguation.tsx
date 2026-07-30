"use client";

/**
 * Disambiguation.
 *
 * When the resolver matches more than one filer the system does not choose.
 * It shows what it found, with the CIK that distinguishes them, and asks.
 * Nothing here guesses, and nothing is preselected.
 *
 * "Not found" is the same surface: it states what happened and what to do,
 * rather than failing silently or offering a nearest guess.
 */

import type { Resolution } from "@/lib/types";

export function Disambiguation({
  resolution,
  onChoose,
  onDismiss,
}: {
  resolution: Resolution;
  onChoose: (cik: string, ticker: string) => void;
  onDismiss: () => void;
}) {
  if (resolution.outcome === "resolved") {
    return null;
  }

  if (resolution.outcome === "not_found") {
    return (
      <section
        aria-labelledby="resolution-heading"
        className="mt-8 border-t-2 border-t-flag pt-4"
      >
        <h2 id="resolution-heading" className="text-sm font-medium text-flag">
          No filer matches that query
        </h2>
        <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
          Nothing in the EDGAR ticker index matches{" "}
          <span className="ref text-ink">{resolution.query}</span>. Check the
          ticker, or search by company name — a company may file under a parent
          entity with a different name.
        </p>
        <button
          type="button"
          onClick={onDismiss}
          className="mt-3 text-sm text-certified underline underline-offset-4 hover:text-ink"
        >
          Try another query
        </button>
      </section>
    );
  }

  return (
    <section
      aria-labelledby="resolution-heading"
      className="mt-8 border-t-2 border-t-flag pt-4"
    >
      <h2 id="resolution-heading" className="text-sm font-medium text-ink">
        More than one filer matches that query
      </h2>
      <p className="mt-1 max-w-prose text-sm text-muted-foreground">
        Choose the entity to profile. Each files separately with the SEC under
        the CIK shown.
      </p>

      <ul className="mt-4 border-t border-rule">
        {resolution.candidates.map((candidate) => (
          <li key={candidate.cik} className="border-b border-rule">
            <button
              type="button"
              onClick={() => {
                onChoose(candidate.cik, candidate.ticker);
              }}
              className="flex w-full items-baseline gap-3 py-3 text-left hover:bg-wash"
            >
              <span className="ref w-20 shrink-0 text-sm text-certified">
                {candidate.ticker}
              </span>
              <span className="min-w-0 flex-1 text-sm text-ink">
                {candidate.name}
              </span>
              <span className="ref shrink-0 text-[0.7rem] text-muted-foreground">
                CIK {candidate.cik}
              </span>
            </button>
          </li>
        ))}
      </ul>

      <button
        type="button"
        onClick={onDismiss}
        className="mt-3 text-sm text-muted-foreground underline underline-offset-4 hover:text-ink"
      >
        None of these — search again
      </button>
    </section>
  );
}
