"use client";

/**
 * Generated prose with its citations.
 *
 * A paragraph carries a marker for every source it rests on, deduplicated —
 * four figures from one 10-K cite that 10-K once. A paragraph with no facts
 * carries no markers, which is the visible signal that it makes no numeric
 * claim.
 */

import type { ProseBlock } from "@/lib/types";
import { markersFor } from "@/lib/provenance";
import { SourceMarker } from "@/components/report/figure";
import { useProvenance } from "@/components/report/provenance-context";

function Citations({ factIds }: { factIds: readonly string[] }) {
  const { index } = useProvenance();
  const markers = markersFor(index, factIds);

  if (markers.length === 0) {
    return null;
  }

  return (
    <span className="whitespace-nowrap">
      {markers.map((marker) => {
        const card = index.cards[marker - 1];
        if (card === undefined) {
          return null;
        }
        return <SourceMarker key={card.id} card={card} />;
      })}
    </span>
  );
}

export function Prose({ blocks }: { blocks: readonly ProseBlock[] }) {
  if (blocks.length === 0) {
    return null;
  }

  return (
    <div className="mt-4 space-y-4">
      {blocks.map((block) => (
        <p
          key={block.id}
          className="max-w-prose text-[0.95rem] leading-relaxed text-ink"
        >
          {block.text}
          <Citations factIds={block.factIds} />
        </p>
      ))}
    </div>
  );
}
