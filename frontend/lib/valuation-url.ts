/**
 * The workbench's assumptions, to and from the query string.
 *
 * Both directions live in one file so they cannot drift: a writer that emits a
 * key the reader ignores would produce links that quietly lose the scenario
 * they were shared to carry.
 *
 * Two rules shape the format. Rates are percentages as a reader types them,
 * because the query string is user-facing and gets pasted into messages —
 * `?r=9&g=6` is legible where `?r=0.09&g=0.06` is not. And a default is
 * written as absence, so a clean URL is the default view and stays clean.
 */

import type { ValuationAssumptions } from "@/lib/types";

/** The view a reader gets with no query string at all. */
export const DEFAULT_ASSUMPTIONS: ValuationAssumptions = {
  mode: "reverse",
  discountRatePct: null,
  fcfGrowthRatePct: null,
  terminalGrowthRatePct: null,
  projectionYears: null,
};

/** Bounds mirroring `ValuationQuery`. The server is still the enforcer. */
const BOUNDS = {
  discountRatePct: { min: 0, max: 60 },
  fcfGrowthRatePct: { min: -50, max: 100 },
  terminalGrowthRatePct: { min: -5, max: 10 },
  projectionYears: { min: 1, max: 15 },
} as const;

function numberIn(
  raw: string | null,
  bound: { min: number; max: number },
  integer = false,
): number | null {
  if (raw === null || raw.trim() === "") {
    return null;
  }
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) {
    return null;
  }
  if (integer && !Number.isInteger(parsed)) {
    return null;
  }
  if (parsed < bound.min || parsed > bound.max) {
    return null;
  }
  return parsed;
}

/**
 * Assumptions read off a query string.
 *
 * Never throws, and an unparseable or out-of-range key is ignored rather than
 * surfaced: a shared link with one bad parameter still opens on the rest of
 * the scenario instead of on an error.
 */
export function fromQuery(
  params: URLSearchParams | Readonly<Record<string, string | string[] | undefined>>,
): ValuationAssumptions {
  const read = (key: string): string | null => {
    if (params instanceof URLSearchParams) {
      return params.get(key);
    }
    const value = params[key];
    if (Array.isArray(value)) {
      return value[0] ?? null;
    }
    return value ?? null;
  };

  const mode = read("mode");
  return {
    mode: mode === "forward" ? "forward" : "reverse",
    discountRatePct: numberIn(read("r"), BOUNDS.discountRatePct),
    fcfGrowthRatePct: numberIn(read("g"), BOUNDS.fcfGrowthRatePct),
    terminalGrowthRatePct: numberIn(read("tg"), BOUNDS.terminalGrowthRatePct),
    projectionYears: numberIn(read("n"), BOUNDS.projectionYears, true),
  };
}

/**
 * A query string for these assumptions, omitting every default.
 *
 * Returns "" for the default view, which is what keeps a clean URL clean.
 */
export function toQuery(assumptions: ValuationAssumptions): string {
  const params = new URLSearchParams();
  if (assumptions.mode !== "reverse") {
    params.set("mode", assumptions.mode);
  }
  const pairs: readonly [string, number | null][] = [
    ["r", assumptions.discountRatePct],
    // Omitted in reverse mode even when set: the growth rate is solved there,
    // not chosen, so carrying one would describe a view the reader is not in.
    ["g", assumptions.mode === "reverse" ? null : assumptions.fcfGrowthRatePct],
    ["tg", assumptions.terminalGrowthRatePct],
    ["n", assumptions.projectionYears],
  ];
  for (const [key, value] of pairs) {
    if (value !== null) {
      params.set(key, String(value));
    }
  }
  return params.toString();
}

/** True when these are the defaults, so the reset control can hide itself. */
export function isDefault(assumptions: ValuationAssumptions): boolean {
  return toQuery(assumptions) === "";
}
