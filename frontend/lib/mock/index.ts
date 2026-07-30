/**
 * ============================================================================
 * FIXTURE DATA. NOT A GENERATED REPORT.
 * See the notice in `lib/mock/factory.ts`.
 * ============================================================================
 *
 * The fixture catalogue. Only routes under `/preview` import this module.
 */

import { DOMESTIC_FIXTURE } from "@/lib/mock/domestic";
import { FOREIGN_FIXTURE } from "@/lib/mock/foreign";
import type { ReportDocument } from "@/lib/types";

export interface Fixture {
  slug: string;
  title: string;
  /** What this fixture is for — the property of the interface it exercises. */
  purpose: string;
  document: ReportDocument;
}

export const FIXTURES: readonly Fixture[] = [
  {
    slug: "domestic",
    title: "Apple Inc. · 10-K · us-gaap · USD",
    purpose:
      "The dense case. Every section populated, all four charts present, market data available.",
    document: DOMESTIC_FIXTURE,
  },
  {
    slug: "foreign",
    title: "SAP SE · 20-F · ifrs-full · EUR",
    purpose:
      "A foreign private issuer with market data unavailable. The peers section states its absence, one balance-sheet row reads Not disclosed, and the valuation chart is omitted rather than blank.",
    document: FOREIGN_FIXTURE,
  },
];

export function findFixture(slug: string): Fixture | undefined {
  return FIXTURES.find((fixture) => fixture.slug === slug);
}

export { DOMESTIC_FIXTURE, FOREIGN_FIXTURE };
