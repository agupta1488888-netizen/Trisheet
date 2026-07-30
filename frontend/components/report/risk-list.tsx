"use client";

/**
 * Risk factors, in the order the filer presents them.
 *
 * These are summaries of the filer's own disclosure, not an assessment of it,
 * and each one carries the marker for the filing it was read from.
 */

import { markersFor } from "@/lib/provenance";
import type { RiskItem } from "@/lib/types";
import { SourceMarker } from "@/components/report/figure";
import { useProvenance } from "@/components/report/provenance-context";

export function RiskList({ risks }: { risks: readonly RiskItem[] }) {
  const { index } = useProvenance();

  if (risks.length === 0) {
    return (
      <p className="mt-4 text-sm text-muted-foreground">
        The annual filing does not present a risk factors section.
      </p>
    );
  }

  return (
    <ol className="mt-4 border-t border-rule">
      {risks.map((risk, position) => {
        const markers = markersFor(index, risk.factIds);
        return (
          <li
            key={risk.id}
            className="grid gap-x-4 border-b border-rule py-3 sm:grid-cols-[2rem_1fr]"
          >
            <span
              aria-hidden="true"
              className="figure hidden text-xs text-muted-foreground sm:block"
            >
              {position + 1}
            </span>
            <div>
              <h4 className="text-sm font-medium text-ink">
                {risk.heading}
                <span className="whitespace-nowrap">
                  {markers.map((marker) => {
                    const card = index.cards[marker - 1];
                    return card === undefined ? null : (
                      <SourceMarker key={card.id} card={card} />
                    );
                  })}
                </span>
              </h4>
              <p className="mt-1 max-w-prose text-sm leading-relaxed text-muted-foreground">
                {risk.summary}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
