"use client";

/**
 * What today's price implies, stated as one sentence and one figure.
 *
 * This is the default view because it is the only valuation this report can
 * offer without forecasting anything: it takes the market's own valuation as
 * given and solves for the growth rate that would justify it. The market
 * capitalisation in the sentence is a real tier 3 fact and renders as one,
 * with its own card in the rail; the growth rate is solved and renders as an
 * assumption.
 */

import { Figure } from "@/components/report/figure";
import {
  AssumptionRate,
} from "@/components/report/workbench/assumption-figure";
import type { ImpliedGrowth as ImpliedGrowthData } from "@/lib/types";

export function ImpliedGrowth({ implied }: { implied: ImpliedGrowthData }) {
  if (implied.unavailableReason !== null) {
    return (
      <p className="border-l-2 border-l-market pl-4 text-sm leading-relaxed text-muted-foreground">
        {implied.unavailableReason}
      </p>
    );
  }

  const rate = implied.impliedGrowthRate;

  return (
    <div className="space-y-6">
      <p className="text-sm leading-relaxed text-ink">
        At a{" "}
        <span className="figure text-muted-foreground">
          {implied.discountRate.display}
        </span>{" "}
        discount rate, a market capitalisation of{" "}
        {implied.marketCapFactId !== null ? (
          <Figure factId={implied.marketCapFactId} />
        ) : (
          <span className="figure text-muted-foreground">
            {implied.marketCap?.display ?? "an unavailable figure"}
          </span>
        )}{" "}
        implies free cash flow growth of <AssumptionRate assumption={rate} /> a
        year for {implied.projectionYears} years.
      </p>

      {implied.boundHit !== null ? (
        <p className="border-l-2 border-l-flag pl-4 text-sm leading-relaxed text-muted-foreground">
          The market values this filer{" "}
          {implied.boundHit === "upper" ? "above" : "below"} what any growth
          rate in the range searched would justify, so the figure above is a
          bound rather than a solution.
        </p>
      ) : null}

      <dl className="border-t border-rule">
        {[implied.discountRate, implied.terminalGrowthRate].map((assumption) => (
          <div
            key={assumption.name}
            className="flex items-baseline justify-between gap-6 border-b border-rule py-2"
          >
            <dt className="text-sm text-muted-foreground">
              {assumption.name === "discount_rate"
                ? "Discount rate"
                : "Terminal growth rate"}
            </dt>
            <dd className="text-right">
              <AssumptionRate assumption={assumption} />
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
