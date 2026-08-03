"use client";

/**
 * One turn in the assistant conversation.
 *
 * A claim renders one of two ways, deliberately only two — this pass ships
 * Tier 1 and Tier 2 only, there is no "unverified" state to render:
 *
 *  - The claim cites a fact already in this report's `SourceIndex`: render
 *    the exact `SourceMarker` the rest of the document uses, so the marker
 *    opens the same rail card a figure in the prose would.
 *  - The claim cites a fact computed fresh for this answer (a Tier 2 figure
 *    that was never part of the original index): render a small inline
 *    citation with the same tier colour and the same accession/date
 *    formatting the rail already uses, without duplicating that formatting.
 *
 * A `notFound` claim is not an error state — it is the assistant declining
 * to guess, and it reads as plainly as the rest of the interface's honest
 * "Not disclosed" voice.
 */

import { cn } from "@/lib/utils";
import { formatAccession, formatFilingDate } from "@/lib/format";
import { SOURCE_TYPE_LABEL } from "@/lib/provenance";
import type { ChatClaim, ChatTurn, SourceType } from "@/lib/types";
import { SourceMarker, tierClassName } from "@/components/report/figure";
import { useProvenance } from "@/components/report/provenance-context";

const NOT_FOUND_TEXT = "Not found in this report's filed data.";

function isKnownSourceType(value: string): value is SourceType {
  return value in SOURCE_TYPE_LABEL;
}

/** A Tier 2 fact computed for this answer, not part of the original index. */
function FreshCitation({ claim }: { claim: ChatClaim }) {
  if (
    claim.tier === null ||
    claim.accessionNo === null ||
    claim.filedDate === null
  ) {
    return null;
  }

  const label =
    claim.sourceType !== null && isKnownSourceType(claim.sourceType)
      ? SOURCE_TYPE_LABEL[claim.sourceType]
      : (claim.sourceType ?? "Source");

  const citation = (
    <span className={cn("ref text-[0.68rem]", tierClassName(claim.tier))}>
      {label} · {formatAccession(claim.accessionNo)} ·{" "}
      {formatFilingDate(claim.filedDate)}
    </span>
  );

  return (
    <sup className="ml-1 leading-none">
      {claim.sourceUrl === null ? (
        citation
      ) : (
        <a
          href={claim.sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="no-underline hover:underline focus-visible:underline"
        >
          {citation}
        </a>
      )}
    </sup>
  );
}

function Claim({ claim }: { claim: ChatClaim }) {
  const { index } = useProvenance();

  if (claim.notFound) {
    return (
      <p className="text-sm leading-relaxed text-muted-foreground">
        {claim.text.trim() === "" ? NOT_FOUND_TEXT : claim.text}
      </p>
    );
  }

  const card = claim.factId === null ? undefined : index.cardByFactId.get(claim.factId);

  return (
    <p className="text-sm leading-relaxed text-ink">
      {claim.text}
      {card !== undefined ? (
        <SourceMarker card={card} />
      ) : (
        <FreshCitation claim={claim} />
      )}
    </p>
  );
}

export function ChatMessage({ turn }: { turn: ChatTurn }) {
  const isUser = turn.role === "user";

  return (
    <div
      className={cn(
        "border-b border-rule py-3",
        isUser ? "pl-8" : "pr-2",
      )}
    >
      <p className="ref text-[0.68rem] text-muted-foreground">
        {isUser ? "You" : "Assistant"}
      </p>

      <div className="mt-1 space-y-2">
        {turn.claims.length > 0 ? (
          turn.claims.map((claim, position) => (
            // Claims carry no id of their own; position is stable within a
            // single, never-reordered turn.
            <Claim key={position} claim={claim} />
          ))
        ) : (
          <p
            className={cn(
              "text-sm leading-relaxed",
              turn.notFound ? "text-muted-foreground" : "text-ink",
            )}
          >
            {turn.content}
          </p>
        )}
      </div>
    </div>
  );
}
