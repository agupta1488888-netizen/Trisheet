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

/**
 * EDGAR's MMDD fiscal year end as a date a reader recognises: "0531" reads
 * "31 May". No year is involved — the field says which day of the year the
 * filer closes its books, not when it last closed them.
 *
 * Returns "Not disclosed" for a missing value and the input unchanged for one
 * that is not four digits, rather than guessing at a malformed one.
 */
export function formatFiscalYearEnd(mmdd: string | null): string {
  if (mmdd === null) {
    return "Not disclosed";
  }
  if (!/^\d{4}$/.test(mmdd)) {
    return mmdd;
  }
  const month = Number(mmdd.slice(0, 2));
  const day = Number(mmdd.slice(2, 4));
  if (month < 1 || month > 12 || day < 1 || day > 31) {
    return mmdd;
  }
  // A leap year, so 29 February is a date this can express.
  const parsed = new Date(Date.UTC(2024, month - 1, day));
  return new Intl.DateTimeFormat(LOCALE, {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  }).format(parsed);
}

/**
 * A file size a reader can judge before clicking: "1.4 MB".
 *
 * Binary units, because that is what an operating system will report for the
 * same file — a download shown as 1.4 MB that lands as 1.3 MB looks like a
 * different file.
 */
export function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB"] as const;
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const decimals = unit === 0 || value >= 100 ? 0 : 1;
  return `${value.toFixed(decimals)} ${units[unit]}`;
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
