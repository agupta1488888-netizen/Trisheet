"use client";

/**
 * The 8-K / 6-K timeline.
 *
 * A dated list, most recent first, with the form and the item numbers as
 * filed. Item numbers are what make a current report readable at a glance —
 * 2.02 is results, 5.02 is a departure of directors — so they are shown rather
 * than summarised away.
 */

import { formatFilingDate } from "@/lib/format";
import { markersFor } from "@/lib/provenance";
import type { DevelopmentEvent } from "@/lib/types";
import { SourceMarker } from "@/components/report/figure";
import { useProvenance } from "@/components/report/provenance-context";

export function DevelopmentsTimeline({
  events,
}: {
  events: readonly DevelopmentEvent[];
}) {
  const { index } = useProvenance();

  if (events.length === 0) {
    return (
      <p className="mt-4 text-sm text-muted-foreground">
        No current reports were filed in the period covered.
      </p>
    );
  }

  return (
    <ol className="mt-4 border-t border-rule">
      {events.map((event) => {
        const markers = markersFor(index, event.factIds);
        return (
          <li
            key={event.id}
            className="grid gap-x-6 gap-y-1 border-b border-rule py-3 sm:grid-cols-[7.5rem_4.5rem_1fr]"
          >
            <span className="figure text-left text-xs text-muted-foreground sm:text-right">
              {formatFilingDate(event.date)}
            </span>
            <span className="ref text-xs text-certified">{event.form}</span>
            <span className="text-sm leading-relaxed text-ink">
              {event.headline}
              <span className="whitespace-nowrap">
                {markers.map((marker) => {
                  const card = index.cards[marker - 1];
                  return card === undefined ? null : (
                    <SourceMarker key={card.id} card={card} />
                  );
                })}
              </span>
              {event.items.length > 0 ? (
                <span className="ref mt-1 block text-[0.7rem] text-muted-foreground">
                  {event.items.map((item) => `Item ${item}`).join(" · ")}
                </span>
              ) : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
