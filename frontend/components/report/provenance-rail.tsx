"use client";

/**
 * The provenance rail — the product's signature element.
 *
 * A persistent column beside the report in which each figure's source appears
 * as a reference card: form type, accession number, filing date, link.
 * Pointing at a figure raises its card; pointing at a card raises every figure
 * drawn from it.
 *
 * It is never collapsed into a footer. On a wide screen it is a sticky column.
 * Below that there is no room for a column, so it becomes a panel docked to
 * the bottom of the viewport: still persistent, still beside the reading
 * position, naming the raised source at all times and opening to the full
 * list. What it never becomes is a block of references at the end of the
 * document that you have to scroll past everything else to reach.
 *
 * Presentational only. It renders the index it is given and fetches nothing.
 */

import { useId, useState } from "react";

import { cn } from "@/lib/utils";
import { formatAccession, formatFilingDate } from "@/lib/format";
import {
  SOURCE_TYPE_LABEL,
  TIER_LABEL,
  type SourceCard,
  type SourceIndex,
} from "@/lib/provenance";
import {
  sourceAnchorId,
  useProvenance,
  useSourceHighlight,
} from "@/components/report/provenance-context";

function accentClass(tier: number): string {
  if (tier === 3) {
    return "border-l-market";
  }
  if (tier === 4) {
    return "border-l-flag";
  }
  return "border-l-certified";
}

function tierTextClass(tier: number): string {
  if (tier === 3) {
    return "text-market";
  }
  if (tier === 4) {
    return "text-flag";
  }
  return "text-certified";
}

function ReferenceCard({ card }: { card: SourceCard }) {
  const { isActive, handlers } = useSourceHighlight(card.id);
  const name = card.form ?? SOURCE_TYPE_LABEL[card.sourceType];

  return (
    <li
      id={sourceAnchorId(card.id)}
      data-source-card={card.id}
      data-active={isActive ? "true" : undefined}
      className={cn(
        "scroll-mt-24 border-b border-b-rule border-l-2 py-3 pl-3 transition-colors motion-reduce:transition-none",
        isActive ? cn("bg-wash", accentClass(card.tier)) : "border-l-transparent",
      )}
      {...handlers}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="flex items-baseline gap-1.5">
          <span
            aria-hidden="true"
            className={cn("ref text-[0.68rem]", tierTextClass(card.tier))}
          >
            {card.marker}
          </span>
          <span className="ref text-sm font-medium text-ink">{name}</span>
        </span>
        <span className="figure text-[0.68rem] text-muted-foreground">
          {formatFilingDate(card.filedDate)}
        </span>
      </div>

      <a
        href={card.url}
        target="_blank"
        rel="noopener noreferrer"
        className="ref mt-1 inline-block text-[0.7rem] text-muted-foreground underline-offset-2 hover:text-ink hover:underline focus-visible:text-ink focus-visible:underline"
      >
        {formatAccession(card.accessionNo)}
      </a>

      <div className="mt-1.5 flex items-baseline justify-between gap-2">
        <span className={cn("text-[0.68rem]", tierTextClass(card.tier))}>
          {TIER_LABEL[card.tier]}
        </span>
        <span className="figure text-[0.68rem] text-muted-foreground">
          {card.factIds.length}{" "}
          {card.factIds.length === 1 ? "figure" : "figures"}
        </span>
      </div>
    </li>
  );
}

function CardList({ cards }: { cards: readonly SourceCard[] }) {
  if (cards.length === 0) {
    return (
      <p className="border-t border-rule pt-4 text-sm text-muted-foreground">
        No sources yet.
      </p>
    );
  }

  return (
    <ul className="border-t border-rule">
      {cards.map((card) => (
        <ReferenceCard key={card.id} card={card} />
      ))}
    </ul>
  );
}

/** The one-line summary the docked panel shows while it is closed. */
function ActiveSummary({ index }: { index: SourceIndex }) {
  const { activeSourceId } = useProvenance();
  const card =
    activeSourceId === null
      ? undefined
      : index.cards.find((entry) => entry.id === activeSourceId);

  if (card === undefined) {
    return (
      <span className="truncate text-sm text-muted-foreground">
        {index.cards.length} {index.cards.length === 1 ? "source" : "sources"}
      </span>
    );
  }

  return (
    <span className="ref truncate text-sm text-ink">
      <span className={tierTextClass(card.tier)}>{card.marker} </span>
      {card.form ?? SOURCE_TYPE_LABEL[card.sourceType]}
      <span className="text-muted-foreground">
        {" · "}
        {formatAccession(card.accessionNo)}
      </span>
    </span>
  );
}

export function ProvenanceRail({ index }: { index: SourceIndex }) {
  const [isOpen, setIsOpen] = useState(false);
  const panelId = useId();

  return (
    <>
      {/* Wide: a sticky column beside the report. */}
      <aside
        aria-label="Sources"
        data-provenance-rail="column"
        className="hidden lg:sticky lg:top-8 lg:block lg:max-h-[calc(100vh-4rem)] lg:self-start lg:overflow-y-auto lg:pb-8"
      >
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="text-sm font-medium text-ink">Sources</h2>
          <span className="figure text-[0.68rem] text-muted-foreground">
            {index.cards.length}
          </span>
        </div>
        <p className="mt-1 mb-3 text-[0.7rem] leading-snug text-muted-foreground">
          Every figure is numbered to the filing it came from.
        </p>
        <CardList cards={index.cards} />
      </aside>

      {/* Narrow: a docked panel. Persistent, never a footer. */}
      <aside
        aria-label="Sources"
        data-provenance-rail="docked"
        className="fixed inset-x-0 bottom-0 z-30 border-t border-rule bg-paper lg:hidden"
      >
        <button
          type="button"
          aria-expanded={isOpen}
          aria-controls={panelId}
          onClick={() => {
            setIsOpen((open) => !open);
          }}
          className="flex w-full items-center justify-between gap-3 px-5 py-3 text-left"
        >
          <span className="flex min-w-0 items-baseline gap-2">
            <span className="shrink-0 text-sm font-medium text-ink">
              Sources
            </span>
            <ActiveSummary index={index} />
          </span>
          <span aria-hidden="true" className="ref shrink-0 text-xs text-muted-foreground">
            {isOpen ? "Close" : "Open"}
          </span>
        </button>

        <div
          id={panelId}
          hidden={!isOpen}
          className="max-h-[55vh] overflow-y-auto px-5 pb-5"
        >
          <CardList cards={index.cards} />
        </div>
      </aside>
    </>
  );
}
