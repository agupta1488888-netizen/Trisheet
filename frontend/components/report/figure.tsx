"use client";

/**
 * A figure and the marker that names its source.
 *
 * Figures are right-aligned, monospace and tabular. The marker is a link to
 * the reference card in the rail, so it works with a pointer, with a keyboard
 * and with a screen reader — it is a citation, not decoration.
 *
 * A fact that cannot be found in the source index renders as "Not disclosed".
 * There is no path here that produces a bare number.
 */

import { cn } from "@/lib/utils";
import { NOT_DISCLOSED } from "@/lib/constants";
import { formatAccession, formatFilingDate } from "@/lib/format";
import {
  SOURCE_TYPE_LABEL,
  sourceUrlFor,
  type SourceCard,
} from "@/lib/provenance";
import {
  sourceAnchorId,
  useProvenance,
  useSourceHighlight,
} from "@/components/report/provenance-context";

/** Tier colour. Tier 1 and 2 certify; 3 is market data; 4 is flagged. */
export function tierClassName(tier: number): string {
  if (tier === 3) {
    return "text-market";
  }
  if (tier === 4) {
    return "text-flag";
  }
  return "text-certified";
}

/** How a source names itself: its form when filed, else its kind. */
function cardName(card: SourceCard): string {
  return card.form ?? SOURCE_TYPE_LABEL[card.sourceType];
}

function cardDescription(card: SourceCard): string {
  return `Source ${card.marker}: ${cardName(card)}, filed ${formatFilingDate(card.filedDate)}, accession ${formatAccession(card.accessionNo)}`;
}

/** The superscript reference marker. Rendered on its own for prose citations. */
export function SourceMarker({
  card,
  className,
}: {
  card: SourceCard;
  className?: string;
}) {
  const { isActive, handlers } = useSourceHighlight(card.id);

  return (
    <sup className={cn("ml-0.5 leading-none", className)}>
      <a
        href={`#${sourceAnchorId(card.id)}`}
        aria-label={cardDescription(card)}
        data-source-marker={card.id}
        className={cn(
          "ref rounded-xs px-px text-[0.62rem] no-underline underline-offset-2",
          "hover:underline focus-visible:underline",
          tierClassName(card.tier),
          isActive && "bg-certified/12 underline",
          card.tier === 3 && isActive && "bg-market/12",
          card.tier === 4 && isActive && "bg-flag/12",
        )}
        {...handlers}
      >
        {card.marker}
      </a>
    </sup>
  );
}

/**
 * One cited figure.
 *
 * `align` exists because a figure inside a sentence should not be pushed to
 * the right margin. Tabular figures in a table always are.
 */
export function Figure({
  factId,
  align = "right",
  className,
}: {
  factId: string;
  align?: "right" | "inline";
  className?: string;
}) {
  const { index } = useProvenance();
  const fact = index.factById.get(factId);
  const card = index.cardByFactId.get(factId);
  const { isActive, handlers } = useSourceHighlight(card?.id ?? null);

  if (fact === undefined || card === undefined) {
    return (
      <span className={cn("text-sm text-muted-foreground", className)}>
        {NOT_DISCLOSED}
      </span>
    );
  }

  const isMissing = fact.value === null && fact.displayValue === NOT_DISCLOSED;
  // A missing figure has no position in a filing to open, whatever URL its
  // filing resolves to.
  const sourceUrl = isMissing ? null : sourceUrlFor(index, factId);

  return (
    <span
      data-fact-id={fact.id}
      data-source-id={card.id}
      className={cn(
        "group/figure whitespace-nowrap",
        align === "right" ? "figure" : "ref",
        isActive && "bg-certified/8",
        isActive && card.tier === 3 && "bg-market/8",
        className,
      )}
      title={
        fact.isCalculated && fact.formula !== null
          ? `Calculated: ${fact.formula}`
          : undefined
      }
      {...handlers}
    >
      {/*
        The figure opens the filing at its own position; the marker beside it
        raises the reference card. Two destinations because a reader wanting
        the source and a reader wanting to see it in the filing are asking
        different questions, and the number is what they reach for.
      */}
      {sourceUrl === null ? (
        <span
          className={cn(
            "tabular-nums",
            isMissing && "font-sans text-sm text-muted-foreground",
          )}
        >
          {fact.displayValue}
        </span>
      ) : (
        <a
          href={sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`${fact.label}: ${fact.displayValue}. Open in ${cardName(card)}.`}
          className="tabular-nums text-inherit no-underline decoration-rule underline-offset-4 hover:underline focus-visible:underline"
        >
          {fact.displayValue}
        </a>
      )}
      <SourceMarker card={card} />
    </span>
  );
}

/**
 * The "calculated" label. A derived figure is rendered with its formula or it
 * is not rendered — this is how the formula reaches the reader.
 */
/**
 * The "assumption" tag: the discounted-cash-flow analogue of `CalculatedLabel`.
 *
 * A calculated figure names the formula that produced it. An assumed one has
 * no formula and no filing — only a stated choice — so this names the choice.
 * Shared by the assistant and the valuation workbench so a reader meeting the
 * same caveat in both places meets the same words for it.
 */
export function AssumptionLabel({
  note,
  children = "assumption",
}: {
  note: string;
  children?: React.ReactNode;
}) {
  return (
    <span className="ml-2 align-middle text-[0.68rem] text-muted-foreground">
      <abbr
        title={note}
        className="cursor-help no-underline decoration-dotted underline-offset-2 hover:underline"
      >
        {children}
      </abbr>
    </span>
  );
}

export function CalculatedLabel({ factId }: { factId: string }) {
  const { index } = useProvenance();
  const fact = index.factById.get(factId);

  if (fact === undefined || !fact.isCalculated || fact.formula === null) {
    return null;
  }

  return (
    <span className="ml-2 align-middle text-[0.68rem] text-muted-foreground">
      <abbr
        title={fact.formula}
        className="cursor-help no-underline decoration-dotted underline-offset-2 hover:underline"
      >
        calculated
      </abbr>
    </span>
  );
}
