/**
 * The join between figures and their sources.
 *
 * Every figure in the report carries a superscript marker; every marker names
 * a reference card in the provenance rail. This module builds that mapping and
 * nothing else. It is pure: no I/O, no formatting, no derivation of figures.
 *
 * Markers are assigned in first-appearance order over `ReportDocument.facts`,
 * which the backend emits in document order. Two figures drawn from the same
 * filing share one marker and one card.
 */

import type {
  Fact,
  FilingRef,
  SourceTier,
  SourceType,
} from "@/lib/types";

/** How a source names itself when no filing form applies. */
export const SOURCE_TYPE_LABEL: Readonly<Record<SourceType, string>> = {
  sec_filing: "SEC filing",
  sec_xbrl: "XBRL company facts",
  company_site: "Company website",
  investor_presentation: "Investor presentation",
  press_release: "Press release",
  market_data: "Market data",
  news: "News",
};

/** Rail card accent per tier. Tier 1 and 2 certify, 3 is market, 4 is flagged. */
export const TIER_LABEL: Readonly<Record<SourceTier, string>> = {
  1: "Tier 1 · filing",
  2: "Tier 2 · company",
  3: "Tier 3 · market data",
  4: "Tier 4 · news",
};

/** One reference card in the rail. */
export interface SourceCard {
  /** Stable key. The accession number, which is unique per filing. */
  id: string;
  /** 1-based superscript marker. */
  marker: number;
  tier: SourceTier;
  sourceType: SourceType;
  /** Form as filed, when the accession matched the manifest. */
  form: string | null;
  accessionNo: string;
  filedDate: string;
  periodOfReport: string | null;
  /** The filing index when known, otherwise the fact's own source URL. */
  url: string;
  /** Every fact attributed to this card, in document order. */
  factIds: readonly string[];
}

export interface SourceIndex {
  /** Cards in marker order. */
  cards: readonly SourceCard[];
  /** Fact id to the card that sources it. */
  cardByFactId: ReadonlyMap<string, SourceCard>;
  /** Fact id to the fact, so renderers need only carry ids. */
  factById: ReadonlyMap<string, Fact>;
}

const EMPTY_INDEX: SourceIndex = {
  cards: [],
  cardByFactId: new Map(),
  factById: new Map(),
};

/**
 * Builds the marker/card index for a report.
 *
 * @param facts   Every cited fact, in document order.
 * @param filings The manifest, used to name the form behind an accession.
 */
export function buildSourceIndex(
  facts: readonly Fact[],
  filings: readonly FilingRef[],
): SourceIndex {
  if (facts.length === 0) {
    return EMPTY_INDEX;
  }

  const manifest = new Map<string, FilingRef>();
  for (const filing of filings) {
    manifest.set(filing.accessionNo, filing);
  }

  const factById = new Map<string, Fact>();
  const cardByFactId = new Map<string, SourceCard>();
  const cards: SourceCard[] = [];
  const cardsByAccession = new Map<string, SourceCard>();
  // Accumulated separately because SourceCard.factIds is readonly by contract.
  const factIdsByAccession = new Map<string, string[]>();

  for (const fact of facts) {
    factById.set(fact.id, fact);

    const existing = cardsByAccession.get(fact.accessionNo);
    if (existing !== undefined) {
      factIdsByAccession.get(fact.accessionNo)?.push(fact.id);
      cardByFactId.set(fact.id, existing);
      continue;
    }

    const filing = manifest.get(fact.accessionNo);
    const factIds: string[] = [fact.id];
    const card: SourceCard = {
      id: fact.accessionNo,
      marker: cards.length + 1,
      tier: fact.tier,
      sourceType: fact.sourceType,
      form: filing?.form ?? null,
      accessionNo: fact.accessionNo,
      filedDate: filing?.filedDate ?? fact.filedDate,
      periodOfReport: filing?.periodOfReport ?? null,
      url: filing?.filingIndexUrl ?? fact.sourceUrl,
      factIds,
    };

    cards.push(card);
    cardsByAccession.set(fact.accessionNo, card);
    factIdsByAccession.set(fact.accessionNo, factIds);
    cardByFactId.set(fact.id, card);
  }

  return { cards, cardByFactId, factById };
}

/**
 * Markers for a list of fact ids, deduplicated and ascending.
 *
 * A paragraph resting on four figures from one 10-K cites that 10-K once.
 */
export function markersFor(
  index: SourceIndex,
  factIds: readonly string[],
): readonly number[] {
  const markers = new Set<number>();
  for (const factId of factIds) {
    const card = index.cardByFactId.get(factId);
    if (card !== undefined) {
      markers.add(card.marker);
    }
  }
  return [...markers].sort((a, b) => a - b);
}
