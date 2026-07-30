/**
 * ============================================================================
 * FIXTURE DATA. NOT A GENERATED REPORT.
 *
 * Everything under `lib/mock` exists so the interface can be built and
 * verified before the pipeline is wired. The figures here are placeholders.
 * They are not sourced from filings and no part of the product may read them:
 * only the routes under `/preview` import this directory, and every one of
 * those routes renders a standing notice saying so.
 * ============================================================================
 *
 * Builders that make a well-formed `Fact` inconvenient to get wrong. Each
 * fact is minted from a source descriptor, so a fixture fact carries the same
 * five provenance fields a real one must.
 */

import { NOT_DISCLOSED } from "@/lib/constants";
import type {
  ExtractionMethod,
  Fact,
  FilingRef,
  SourceTier,
  SourceType,
  Taxonomy,
} from "@/lib/types";

/** A filing that fixture facts can be attributed to. */
export interface MockSource {
  accessionNo: string;
  form: string;
  baseForm: string;
  filedDate: string;
  periodOfReport: string | null;
  /** Document URL on sec.gov. Real EDGAR paths, so links behave normally. */
  documentUrl: string;
  indexUrl: string;
  tier: SourceTier;
  sourceType: SourceType;
  items?: readonly string[];
}

export function toFilingRef(source: MockSource, cik: string): FilingRef {
  return {
    accessionNo: source.accessionNo,
    cik,
    form: source.form,
    baseForm: source.baseForm,
    isAmendment: source.form.includes("/A"),
    filedDate: source.filedDate,
    periodOfReport: source.periodOfReport,
    primaryDocUrl: source.documentUrl,
    filingIndexUrl: source.indexUrl,
    items: [...(source.items ?? [])],
    exhibits: [],
  };
}

/** Segment coordinates. Axis and member are set together or not at all. */
export interface SegmentRef {
  axis: string;
  member: string;
  label: string;
}

export interface FactSeed {
  metric: string;
  label: string;
  value: number | null;
  displayValue: string;
  source: MockSource;
  unit?: string | null;
  periodStart?: string | null;
  periodEnd: string;
  fiscalYear?: number | null;
  segment?: SegmentRef;
  resolvedTag?: string | null;
  taxonomy?: Taxonomy | null;
  isCalculated?: boolean;
  formula?: string | null;
  extractionMethod?: ExtractionMethod;
  confidence?: number;
}

/**
 * How a fact came to exist, inferred from the shape of the seed so a fixture
 * cannot describe itself as XBRL-extracted while carrying a formula.
 */
function inferExtractionMethod(seed: FactSeed): ExtractionMethod {
  if (seed.extractionMethod !== undefined) {
    return seed.extractionMethod;
  }
  if (seed.isCalculated === true) {
    return "calculated";
  }
  if (seed.source.tier === 3) {
    return "market_data";
  }
  if (seed.value === null && seed.displayValue === NOT_DISCLOSED) {
    return "not_disclosed";
  }
  if (seed.source.sourceType === "sec_xbrl") {
    return seed.segment === undefined ? "xbrl_company_facts" : "xbrl_dimensional";
  }
  return "narrative";
}

/**
 * Mints a fact. Ids are deterministic — `metric` plus `periodEnd` — so that a
 * fixture can reference a fact by id from a table or a paragraph without the
 * two drifting apart.
 */
export function makeFact(reportId: string, seed: FactSeed): Fact {
  const extractionMethod = inferExtractionMethod(seed);

  return {
    id: factId(seed.metric, seed.periodEnd),
    reportId,
    metric: seed.metric,
    label: seed.label,
    value: seed.value,
    displayValue: seed.displayValue,
    unit: seed.unit ?? null,
    periodStart: seed.periodStart ?? null,
    periodEnd: seed.periodEnd,
    fiscalYear: seed.fiscalYear ?? null,
    segmentAxis: seed.segment?.axis ?? null,
    segmentMember: seed.segment?.member ?? null,
    segmentLabel: seed.segment?.label ?? null,
    tier: seed.source.tier,
    sourceType: seed.source.sourceType,
    sourceUrl: seed.source.documentUrl,
    accessionNo: seed.source.accessionNo,
    filedDate: seed.source.filedDate,
    extractionMethod,
    // A "not disclosed" marker carries no confidence, because there is no
    // value to be confident about.
    confidence: seed.confidence ?? (extractionMethod === "not_disclosed" ? 0 : 1),
    resolvedTag: seed.resolvedTag ?? null,
    taxonomy: seed.taxonomy ?? null,
    isCalculated: seed.isCalculated ?? false,
    formula: seed.formula ?? null,
  };
}

/** The id `makeFact` will assign. Use this to cite a fact from a table row. */
export function factId(metric: string, periodEnd: string): string {
  return `${metric}@${periodEnd}`;
}

/** One fiscal period of a fixture: its label, end date and annual filing. */
export interface MockPeriod {
  label: string;
  fiscalYear: number;
  periodStart: string;
  periodEnd: string;
  source: MockSource;
}

/**
 * Builds one metric across every period in a fixture.
 *
 * `values[i]` and `displays[i]` line up with `periods[i]`. A null value is a
 * period the filer did not disclose; its display string still has to say so,
 * which is why both arrays are required rather than one being derived.
 */
export function makeSeries(
  reportId: string,
  periods: readonly MockPeriod[],
  spec: {
    metric: string;
    label: string;
    unit: string | null;
    values: readonly (number | null)[];
    displays: readonly string[];
    segment?: SegmentRef;
    resolvedTag?: string;
    taxonomy?: Taxonomy;
    isCalculated?: boolean;
    formula?: string;
  },
): Fact[] {
  return periods.map((period, i) =>
    makeFact(reportId, {
      metric: spec.metric,
      label: spec.label,
      value: spec.values[i] ?? null,
      displayValue: spec.displays[i] ?? NOT_DISCLOSED,
      unit: spec.unit,
      periodStart: period.periodStart,
      periodEnd: period.periodEnd,
      fiscalYear: period.fiscalYear,
      source: period.source,
      segment: spec.segment,
      resolvedTag: spec.resolvedTag ?? null,
      taxonomy: spec.taxonomy ?? null,
      isCalculated: spec.isCalculated ?? false,
      formula: spec.formula ?? null,
    }),
  );
}

/** Fact ids for a metric across every period, for a table row. */
export function seriesFactIds(
  periods: readonly MockPeriod[],
  metric: string,
): string[] {
  return periods.map((period) => factId(metric, period.periodEnd));
}
