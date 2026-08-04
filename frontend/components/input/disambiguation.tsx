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
 *
 * Styled against the dark hero rather than the palette tokens. `text-ink`,
 * `border-rule` and `bg-wash` are defined for the paper-white report view;
 * this component only ever renders inside the search bar's surroundings, and
 * on that background they resolve to near-black on near-black.
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
        className="mt-5 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3.5"
      >
        <h2 id="resolution-heading" className="text-sm font-medium text-white">
          No filer matches that query
        </h2>
        <p className="mt-2 max-w-prose text-sm leading-relaxed text-white/45">
          Nothing in the EDGAR ticker index matches{" "}
          <span className="ref text-white">{resolution.query}</span>. Check the
          ticker, or search by company name — a company may file under a parent
          entity with a different name.
        </p>
        <button
          type="button"
          onClick={onDismiss}
          className="mt-3 text-sm text-white/55 underline underline-offset-4 transition-colors hover:text-white motion-reduce:transition-none"
        >
          Try another query
        </button>
      </section>
    );
  }

  return (
    <section
      aria-labelledby="resolution-heading"
      className="mt-5 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3.5"
    >
      <h2 id="resolution-heading" className="text-sm font-medium text-white">
        More than one filer matches that query
      </h2>
      <p className="mt-1 max-w-prose text-sm text-white/45">
        Choose the entity to profile. Each files separately with the SEC under
        the CIK shown.
      </p>

      <ul className="mt-3 border-t border-white/10">
        {resolution.candidates.map((candidate) => (
          <li key={candidate.cik} className="border-b border-white/10">
            <button
              type="button"
              onClick={() => {
                onChoose(candidate.cik, candidate.ticker);
              }}
              className="flex w-full items-baseline gap-3 rounded-md px-2 py-2.5 text-left transition-colors hover:bg-white/[0.06] focus-visible:outline focus-visible:outline-1 focus-visible:outline-white/40 motion-reduce:transition-none"
            >
              <span className="ref w-20 shrink-0 text-sm text-white">
                {candidate.ticker}
              </span>
              <span className="min-w-0 flex-1 text-sm text-white/80">
                {candidate.name}
              </span>
              <span className="ref shrink-0 text-[0.7rem] text-white/35">
                CIK {candidate.cik}
              </span>
            </button>
          </li>
        ))}
      </ul>

      <button
        type="button"
        onClick={onDismiss}
        className="mt-3 text-sm text-white/45 underline underline-offset-4 transition-colors hover:text-white motion-reduce:transition-none"
      >
        None of these — search again
      </button>
    </section>
  );
}
