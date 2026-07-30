/**
 * Presentation formatting.
 *
 * This module turns values that are already final into strings. It does not
 * scale, sum, divide or derive anything: every figure the report renders was
 * computed in `m07_analysis` and arrives ready to display. Chart series arrive
 * in their display scale, so the axis formatter groups digits and appends a
 * unit — it never converts one.
 *
 * Pure functions only.
 */

import { NOT_DISCLOSED } from "@/lib/constants";

const LOCALE = "en-US";

const compactAxis = new Intl.NumberFormat(LOCALE, {
  notation: "compact",
  maximumFractionDigits: 1,
});

const plain = new Intl.NumberFormat(LOCALE, {
  maximumFractionDigits: 0,
});

const oneDecimal = new Intl.NumberFormat(LOCALE, {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const twoDecimal = new Intl.NumberFormat(LOCALE, {
  minimumFractionDigits: 1,
  maximumFractionDigits: 2,
});

/** Axis ticks. Compact so the axis stays narrow: 12.3K, 1.2M. */
export function formatAxisValue(value: number): string {
  return compactAxis.format(value);
}

/** Whole numbers with thousands separators, for tooltips and counts. */
export function formatCount(value: number): string {
  return plain.format(value);
}

/**
 * A chart value in a tooltip. Null renders as "Not disclosed" — a gap in a
 * series is a disclosure gap, never a zero.
 */
export function formatSeriesValue(value: number | null): string {
  return value === null ? NOT_DISCLOSED : twoDecimal.format(value);
}

/** A percentage that arrives already expressed as percent, e.g. 42.3 -> "42.3%". */
export function formatPercent(value: number | null): string {
  return value === null ? NOT_DISCLOSED : `${oneDecimal.format(value)}%`;
}

/** A valuation multiple, e.g. 18.4 -> "18.4x". */
export function formatMultiple(value: number | null): string {
  return value === null ? NOT_DISCLOSED : `${oneDecimal.format(value)}x`;
}

/**
 * An ISO date as a filing date reads on a reference card: "12 Sep 2025".
 * Returns the input unchanged when it is not a date we can parse, rather than
 * inventing one.
 */
export function formatFilingDate(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  return new Intl.DateTimeFormat(LOCALE, {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(parsed);
}

/** Clock time for the progress feed, in the data face: "14:32:07". */
export function formatFeedTime(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  return new Intl.DateTimeFormat(LOCALE, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(parsed);
}

/**
 * An accession number, hyphenated as EDGAR writes it:
 * 000032019325000073 -> 0000320193-25-000073. Already-hyphenated input is
 * returned unchanged.
 */
export function formatAccession(accessionNo: string): string {
  if (accessionNo.includes("-")) {
    return accessionNo;
  }
  if (accessionNo.length !== 18) {
    return accessionNo;
  }
  return `${accessionNo.slice(0, 10)}-${accessionNo.slice(10, 12)}-${accessionNo.slice(12)}`;
}
