"use client";

/**
 * Example tickers.
 *
 * Starting points, not defaults — nothing is preselected. The set deliberately
 * spans a 10-K, a 20-F and a 40-F filer so it is visible from the first screen
 * that the system is not built around one kind of company.
 */

import { EXAMPLE_TICKERS } from "@/lib/constants";

export function ExampleChips({
  onPick,
  disabled = false,
}: {
  onPick: (ticker: string) => void;
  disabled?: boolean;
}) {
  return (
    <div>
      <p className="text-[0.68rem] text-muted-foreground">Try</p>
      <ul className="mt-2 flex flex-wrap gap-2">
        {EXAMPLE_TICKERS.map((example) => (
          <li key={example.ticker}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => {
                onPick(example.ticker);
              }}
              title={`${example.name} · files ${example.form}`}
              className="ref flex items-baseline gap-2 border border-rule px-2.5 py-1 text-sm text-ink transition-colors hover:border-certified hover:text-certified disabled:cursor-not-allowed disabled:opacity-50 motion-reduce:transition-none"
            >
              {example.ticker}
              <span className="text-[0.68rem] text-muted-foreground">
                {example.form}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
