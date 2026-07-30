/**
 * ============================================================================
 * FIXTURE DATA. NOT A GENERATED REPORT.
 * See the notice in `lib/mock/factory.ts`.
 * ============================================================================
 *
 * A slice of the EDGAR ticker index, standing in for `/resolve/suggest` until
 * the backend is wired. Deliberately spans domestic, foreign and Canadian
 * filers across several sectors, so the input screen is never tested against
 * one shape of company.
 */

import type { Candidate, Resolution, TickerSuggestion } from "@/lib/types";

export const TICKER_INDEX: readonly TickerSuggestion[] = [
  { cik: "0000320193", ticker: "AAPL", name: "Apple Inc.", filerType: "domestic" },
  { cik: "0000789019", ticker: "MSFT", name: "Microsoft Corporation", filerType: "domestic" },
  { cik: "0001652044", ticker: "GOOGL", name: "Alphabet Inc.", filerType: "domestic" },
  { cik: "0001018724", ticker: "AMZN", name: "Amazon.com, Inc.", filerType: "domestic" },
  { cik: "0000021344", ticker: "KO", name: "The Coca-Cola Company", filerType: "domestic" },
  { cik: "0000077476", ticker: "PEP", name: "PepsiCo, Inc.", filerType: "domestic" },
  { cik: "0000104169", ticker: "WMT", name: "Walmart Inc.", filerType: "domestic" },
  { cik: "0000034088", ticker: "XOM", name: "Exxon Mobil Corporation", filerType: "domestic" },
  { cik: "0000019617", ticker: "JPM", name: "JPMorgan Chase & Co.", filerType: "domestic" },
  { cik: "0000078003", ticker: "PFE", name: "Pfizer Inc.", filerType: "domestic" },
  { cik: "0000320187", ticker: "NKE", name: "NIKE, Inc.", filerType: "domestic" },
  { cik: "0000027419", ticker: "TGT", name: "Target Corporation", filerType: "domestic" },
  { cik: "0001000184", ticker: "SAP", name: "SAP SE", filerType: "foreign" },
  { cik: "0000313616", ticker: "DHI", name: "D.R. Horton, Inc.", filerType: "domestic" },
  { cik: "0001090727", ticker: "UPS", name: "United Parcel Service, Inc.", filerType: "domestic" },
  { cik: "0000895421", ticker: "MS", name: "Morgan Stanley", filerType: "domestic" },
  // Filer type is what the filer most recently filed, not where it is
  // domiciled. ENB is Canadian and files a 10-K; CNI is Canadian and files a
  // 40-F. Both are in the list so the interface is exercised against that
  // distinction rather than assuming it away.
  { cik: "0000895728", ticker: "ENB", name: "Enbridge Inc.", filerType: "domestic" },
  { cik: "0000016868", ticker: "CNI", name: "Canadian National Railway Company", filerType: "canadian" },
  { cik: "0000009342", ticker: "BNS", name: "The Bank of Nova Scotia", filerType: "canadian" },
  { cik: "0001046179", ticker: "TSM", name: "Taiwan Semiconductor Manufacturing Company Limited", filerType: "foreign" },
  { cik: "0001594805", ticker: "SHOP", name: "Shopify Inc.", filerType: "domestic" },
  { cik: "0001341439", ticker: "ORCL", name: "Oracle Corporation", filerType: "domestic" },
  { cik: "0000051143", ticker: "IBM", name: "International Business Machines Corporation", filerType: "domestic" },
  { cik: "0001045810", ticker: "NVDA", name: "NVIDIA Corporation", filerType: "domestic" },
  { cik: "0000097745", ticker: "TMO", name: "Thermo Fisher Scientific Inc.", filerType: "domestic" },
  { cik: "0000753308", ticker: "NEE", name: "NextEra Energy, Inc.", filerType: "domestic" },
  { cik: "0001403161", ticker: "V", name: "Visa Inc.", filerType: "domestic" },
  { cik: "0001141391", ticker: "MA", name: "Mastercard Incorporated", filerType: "domestic" },
  { cik: "0000936468", ticker: "LMT", name: "Lockheed Martin Corporation", filerType: "domestic" },
  { cik: "0000064040", ticker: "SPGI", name: "S&P Global Inc.", filerType: "domestic" },
];

/**
 * Ranks the index against a query the way the resolver should: exact ticker
 * first, then ticker prefix, then a name match. Pure and case-insensitive.
 *
 * Presentational stand-in only — the real ranking lives in m01.
 */
export function searchTickerIndex(
  query: string,
  limit: number,
): readonly TickerSuggestion[] {
  const needle = query.trim().toLowerCase();
  if (needle === "") {
    return [];
  }

  const scored = TICKER_INDEX.map((entry) => {
    const ticker = entry.ticker.toLowerCase();
    const name = entry.name.toLowerCase();
    if (ticker === needle) {
      return { entry, score: 0 };
    }
    if (ticker.startsWith(needle)) {
      return { entry, score: 1 };
    }
    if (name.startsWith(needle)) {
      return { entry, score: 2 };
    }
    if (name.includes(needle)) {
      return { entry, score: 3 };
    }
    return { entry, score: Number.POSITIVE_INFINITY };
  }).filter((row) => Number.isFinite(row.score));

  scored.sort(
    (a, b) => a.score - b.score || a.entry.ticker.localeCompare(b.entry.ticker),
  );

  return scored.slice(0, limit).map((row) => row.entry);
}

const AMBIGUOUS_CANDIDATES: Candidate[] = [
  { cik: "0001067983", ticker: "BRK-A", name: "Berkshire Hathaway Inc." },
  { cik: "0000109694", ticker: "BRK-B", name: "Berkshire Hathaway Inc. (Class B)" },
  { cik: "0000949012", ticker: "BHE", name: "Berkshire Hathaway Energy Company" },
  { cik: "0001571996", ticker: "BRKF", name: "Berkshire Hathaway Finance Corporation" },
];

/** What the resolver returns when a query matches more than one filer. */
export const AMBIGUOUS_RESOLUTION: Resolution = {
  outcome: "ambiguous",
  query: "berkshire",
  company: null,
  candidates: AMBIGUOUS_CANDIDATES,
};

/** What it returns when nothing matches. */
export const NOT_FOUND_RESOLUTION: Resolution = {
  outcome: "not_found",
  query: "zzzz",
  company: null,
  candidates: [],
};
