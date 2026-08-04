/**
 * ============================================================================
 * FIXTURE DATA. NOT A GENERATED REPORT.
 * See the notice in `lib/mock/factory.ts`. Figures below are placeholders for
 * interface development and are not sourced from filings.
 * ============================================================================
 *
 * A foreign private issuer: 20-F, ifrs-full, EUR. This fixture is the degraded
 * case, and it exists to prove the interface holds when things are missing —
 * market data is unavailable, so the valuation chart and the peers section are
 * absent rather than blank; one balance-sheet line is genuinely not disclosed
 * and renders as such rather than as a zero.
 */

import {
  factId,
  makeFact,
  makeSeries,
  seriesFactIds,
  toFilingRef,
  type MockPeriod,
  type MockSource,
} from "@/lib/mock/factory";
import { NOT_DISCLOSED } from "@/lib/constants";
import type { Fact, FigureRow, ReportDocument } from "@/lib/types";

const REPORT_ID = "fixture-foreign";
const CIK = "0001000184";
const CIK_INT = "1000184";

function filing(
  accessionNo: string,
  form: string,
  filedDate: string,
  periodOfReport: string,
  document: string,
  items?: readonly string[],
): MockSource {
  const nodash = accessionNo.replace(/-/g, "");
  return {
    accessionNo,
    form,
    baseForm: form.replace("/A", ""),
    filedDate,
    periodOfReport,
    documentUrl: `https://www.sec.gov/Archives/edgar/data/${CIK_INT}/${nodash}/${document}`,
    indexUrl: `https://www.sec.gov/Archives/edgar/data/${CIK_INT}/${nodash}/${accessionNo}-index.htm`,
    tier: 1,
    sourceType: "sec_xbrl",
    items,
  };
}

const FY2024_20F = filing(
  "0001000184-25-000012",
  "20-F",
  "2025-02-26",
  "2024-12-31",
  "sap-20241231.htm",
);

const FY2023_20F = filing(
  "0001000184-24-000009",
  "20-F",
  "2024-02-28",
  "2023-12-31",
  "sap-20231231.htm",
);

const FY2022_20F = filing(
  "0001000184-23-000007",
  "20-F",
  "2023-03-01",
  "2022-12-31",
  "sap-20221231.htm",
);

const NARRATIVE_20F: MockSource = { ...FY2024_20F, sourceType: "sec_filing" };

const RESULTS_6K = filing(
  "0001000184-25-000048",
  "6-K",
  "2025-07-22",
  "2025-07-22",
  "sap-20250722.htm",
);

const PERIODS: readonly MockPeriod[] = [
  {
    label: "FY2022",
    fiscalYear: 2022,
    periodStart: "2022-01-01",
    periodEnd: "2022-12-31",
    source: FY2022_20F,
  },
  {
    label: "FY2023",
    fiscalYear: 2023,
    periodStart: "2023-01-01",
    periodEnd: "2023-12-31",
    source: FY2023_20F,
  },
  {
    label: "FY2024",
    fiscalYear: 2024,
    periodStart: "2024-01-01",
    periodEnd: "2024-12-31",
    source: FY2024_20F,
  },
];

const PERIOD_LABELS = PERIODS.map((period) => period.label);
const LATEST_END = "2024-12-31";
const EUR_M = "EUR millions";

const statementFacts: Fact[] = [
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "income.revenue",
    label: "Revenue",
    unit: EUR_M,
    values: [30_871, 31_207, 34_176],
    displays: ["30,871", "31,207", "34,176"],
    resolvedTag: "Revenue",
    taxonomy: "ifrs-full",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "income.gross_profit",
    label: "Gross profit",
    unit: EUR_M,
    values: [21_547, 22_310, 25_058],
    displays: ["21,547", "22,310", "25,058"],
    resolvedTag: "GrossProfit",
    taxonomy: "ifrs-full",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "income.operating_income",
    label: "Operating profit",
    unit: EUR_M,
    values: [2_349, 4_144, 6_007],
    displays: ["2,349", "4,144", "6,007"],
    resolvedTag: "ProfitLossFromOperatingActivities",
    taxonomy: "ifrs-full",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "income.net_income",
    label: "Profit after tax",
    unit: EUR_M,
    values: [1_708, 5_967, 3_098],
    displays: ["1,708", "5,967", "3,098"],
    resolvedTag: "ProfitLoss",
    taxonomy: "ifrs-full",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "cashflow.operating",
    label: "Cash from operating activities",
    unit: EUR_M,
    values: [4_357, 6_207, 7_186],
    displays: ["4,357", "6,207", "7,186"],
    resolvedTag: "CashFlowsFromUsedInOperatingActivities",
    taxonomy: "ifrs-full",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "cashflow.capex",
    label: "Capital expenditure",
    unit: EUR_M,
    values: [845, 767, 812],
    displays: ["845", "767", "812"],
    resolvedTag: "PurchaseOfPropertyPlantAndEquipment",
    taxonomy: "ifrs-full",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "cashflow.free_cash_flow",
    label: "Free cash flow",
    unit: EUR_M,
    values: [3_512, 5_440, 6_374],
    displays: ["3,512", "5,440", "6,374"],
    isCalculated: true,
    formula: "Cash from operating activities − capital expenditure",
  }),
  // The filer does not break this line out in the periods presented. It is
  // carried as a fact so the gap itself is sourced, and it renders as
  // "Not disclosed" — never as zero, never as a blank cell.
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "balance.total_debt",
    label: "Total debt",
    unit: EUR_M,
    values: [null, null, null],
    displays: [NOT_DISCLOSED, NOT_DISCLOSED, NOT_DISCLOSED],
    taxonomy: "ifrs-full",
  }),
];

const analysisFacts: Fact[] = [
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "ratio.gross_margin",
    label: "Gross margin",
    unit: "percent",
    values: [69.8, 71.5, 73.3],
    displays: ["69.8%", "71.5%", "73.3%"],
    isCalculated: true,
    formula: "Gross profit ÷ revenue",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "ratio.operating_margin",
    label: "Operating margin",
    unit: "percent",
    values: [7.6, 13.3, 17.6],
    displays: ["7.6%", "13.3%", "17.6%"],
    isCalculated: true,
    formula: "Operating profit ÷ revenue",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "growth.revenue_yoy",
    label: "Revenue growth",
    unit: "percent",
    values: [11.1, 1.1, 9.5],
    displays: ["11.1%", "1.1%", "9.5%"],
    isCalculated: true,
    formula: "Revenue ÷ prior-year revenue − 1",
  }),
];

const SEGMENT_NAMES = ["Cloud", "Software licences and support", "Services"];

const SEGMENT_VALUES: Readonly<Record<string, readonly number[]>> = {
  Cloud: [11_426, 13_664, 17_144],
  "Software licences and support": [15_216, 13_780, 13_024],
  Services: [4_229, 3_763, 4_008],
};

const SEGMENT_DISPLAYS: Readonly<Record<string, readonly string[]>> = {
  Cloud: ["11,426", "13,664", "17,144"],
  "Software licences and support": ["15,216", "13,780", "13,024"],
  Services: ["4,229", "3,763", "4,008"],
};

const segmentFacts: Fact[] = SEGMENT_NAMES.flatMap((name, index) =>
  makeSeries(REPORT_ID, PERIODS, {
    metric: `segment.s${index}.revenue`,
    label: `${name} revenue`,
    unit: EUR_M,
    values: SEGMENT_VALUES[name] ?? [],
    displays: SEGMENT_DISPLAYS[name] ?? [],
    segment: {
      axis: "ifrs-full:SegmentsAxis",
      member: `sap:Segment${index}Member`,
      label: name,
    },
    resolvedTag: "Revenue",
    taxonomy: "ifrs-full",
  }),
);

const narrativeFacts: Fact[] = [
  makeFact(REPORT_ID, {
    metric: "narrative.business",
    label: "Business description",
    value: null,
    displayValue: "Item 4, Information on the company",
    periodEnd: LATEST_END,
    fiscalYear: 2024,
    source: NARRATIVE_20F,
  }),
  makeFact(REPORT_ID, {
    metric: "narrative.risk_factors",
    label: "Risk factors",
    value: null,
    displayValue: "Item 3.D, Risk factors",
    periodEnd: LATEST_END,
    fiscalYear: 2024,
    source: NARRATIVE_20F,
  }),
  makeFact(REPORT_ID, {
    metric: "event.h1_results",
    label: "Half year results",
    value: null,
    displayValue: "Interim results announcement",
    periodEnd: "2025-07-22",
    source: RESULTS_6K,
  }),
];

const FACTS: readonly Fact[] = [
  ...statementFacts,
  ...analysisFacts,
  ...segmentFacts,
  ...narrativeFacts,
];

const latest = (metric: string): string => factId(metric, LATEST_END);

/** Repeated on both sections that market data would have populated. */
const MARKET_UNAVAILABLE =
  "Market data is unavailable, so no valuation comparison is shown. Every figure elsewhere in this report comes from filings and is unaffected.";

export const FOREIGN_FIXTURE: ReportDocument = {
  report: {
    id: REPORT_ID,
    ticker: "SAP",
    cik: CIK,
    status: "complete",
    errorMessage: null,
    createdAt: "2025-11-28T15:02:11Z",
    completedAt: "2025-11-28T15:03:49Z",
  },
  company: {
    cik: CIK,
    ticker: "SAP",
    name: "SAP SE",
    filerType: "foreign",
    sicCode: "7372",
    sector: "Services — prepackaged software",
    fiscalYearEnd: "1231",
    reportingCurrency: "EUR",
  },
  depth: "standard",
  facts: FACTS,
  filings: [
    toFilingRef(FY2024_20F, CIK),
    toFilingRef(FY2023_20F, CIK),
    toFilingRef(FY2022_20F, CIK),
    toFilingRef(RESULTS_6K, CIK),
  ],
  sections: [
    {
      id: "snapshot",
      title: "Snapshot",
      unavailableReason: null,
      prose: [
        {
          id: "snapshot-1",
          text: "SAP SE is a foreign private issuer and files an annual report on Form 20-F. It reports under IFRS in euro, with a fiscal year ending 31 December. Figures are extracted from the ifrs-full taxonomy; where a concept has no us-gaap equivalent, the IFRS tag is recorded on the fact.",
          factIds: [latest("income.revenue")],
        },
      ],
      tables: [
        {
          id: "snapshot-key",
          caption: "At a glance",
          periods: ["Latest"],
          unitNote: "EUR millions",
          rows: [
            { label: "Revenue, FY2024", factIds: [latest("income.revenue")] },
            {
              label: "Operating profit, FY2024",
              factIds: [latest("income.operating_income")],
            },
            {
              label: "Free cash flow, FY2024",
              factIds: [latest("cashflow.free_cash_flow")],
              emphasis: "derived",
            },
            {
              label: "Total debt",
              factIds: [latest("balance.total_debt")],
            },
          ],
        },
      ],
      events: [],
      risks: [],
    },
    {
      id: "business",
      title: "Business",
      unavailableReason: null,
      prose: [
        {
          id: "business-1",
          text: "Item 4 of the 20-F describes enterprise application software sold under licence and, increasingly, as cloud subscriptions. Cloud revenue reached 17,144 million euro in FY2024 and overtook software licences and support for the first time in the periods presented.",
          factIds: [latest("narrative.business"), latest("segment.s0.revenue")],
        },
      ],
      tables: [
        {
          id: "business-segments",
          caption: "Revenue by segment",
          periods: PERIOD_LABELS,
          unitNote: "EUR millions",
          rows: [
            ...SEGMENT_NAMES.map<FigureRow>((name, index) => ({
              label: name,
              factIds: seriesFactIds(PERIODS, `segment.s${index}.revenue`),
            })),
            {
              label: "Total revenue",
              factIds: seriesFactIds(PERIODS, "income.revenue"),
              emphasis: "total",
            },
          ],
        },
      ],
      events: [],
      risks: [],
    },
    {
      id: "financials",
      title: "Financial highlights",
      unavailableReason: null,
      prose: [
        {
          id: "financials-1",
          text: "Revenue of 34,176 million euro in FY2024 was 9.5 per cent above the prior year. Operating profit of 6,007 million more than doubled over two years as restructuring charges taken in FY2022 and FY2023 fell away.",
          factIds: [
            latest("income.revenue"),
            latest("growth.revenue_yoy"),
            latest("income.operating_income"),
          ],
        },
        {
          id: "financials-2",
          text: "Profit after tax of 3,098 million is below the prior year, which included a substantial non-operating gain. The filer does not present a single total-debt line in the periods covered, so that row reads as not disclosed rather than being assembled from components.",
          factIds: [latest("income.net_income"), latest("balance.total_debt")],
        },
      ],
      tables: [
        {
          id: "financials-income",
          caption: "Income statement and cash flow",
          periods: PERIOD_LABELS,
          unitNote: "EUR millions",
          rows: [
            {
              label: "Revenue",
              factIds: seriesFactIds(PERIODS, "income.revenue"),
              emphasis: "total",
            },
            {
              label: "Gross profit",
              factIds: seriesFactIds(PERIODS, "income.gross_profit"),
            },
            {
              label: "Operating profit",
              factIds: seriesFactIds(PERIODS, "income.operating_income"),
            },
            {
              label: "Profit after tax",
              factIds: seriesFactIds(PERIODS, "income.net_income"),
            },
            {
              label: "Cash from operating activities",
              factIds: seriesFactIds(PERIODS, "cashflow.operating"),
            },
            {
              label: "Capital expenditure",
              factIds: seriesFactIds(PERIODS, "cashflow.capex"),
            },
            {
              label: "Free cash flow",
              factIds: seriesFactIds(PERIODS, "cashflow.free_cash_flow"),
              emphasis: "derived",
            },
            {
              label: "Total debt",
              factIds: seriesFactIds(PERIODS, "balance.total_debt"),
            },
          ],
        },
      ],
      events: [],
      risks: [],
    },
    {
      id: "analysis",
      title: "Analysis",
      unavailableReason: null,
      prose: [
        {
          id: "analysis-1",
          text: "Gross margin widened from 69.8 to 73.3 per cent over three years as the revenue mix shifted towards cloud. Operating margin rose further, from 7.6 to 17.6 per cent, because the FY2022 base carried restructuring costs that did not recur.",
          factIds: [
            factId("ratio.gross_margin", "2022-12-31"),
            latest("ratio.gross_margin"),
            factId("ratio.operating_margin", "2022-12-31"),
            latest("ratio.operating_margin"),
          ],
        },
      ],
      tables: [
        {
          id: "analysis-ratios",
          caption: "Margins",
          periods: PERIOD_LABELS,
          unitNote: "Per cent. Every figure below is calculated; formulae are on each row.",
          rows: [
            {
              label: "Gross margin",
              factIds: seriesFactIds(PERIODS, "ratio.gross_margin"),
              emphasis: "derived",
            },
            {
              label: "Operating margin",
              factIds: seriesFactIds(PERIODS, "ratio.operating_margin"),
              emphasis: "derived",
            },
            {
              label: "Revenue growth",
              factIds: seriesFactIds(PERIODS, "growth.revenue_yoy"),
              emphasis: "derived",
            },
          ],
        },
      ],
      events: [],
      risks: [],
    },
    {
      id: "peers",
      title: "Peers and valuation",
      unavailableReason: MARKET_UNAVAILABLE,
      prose: [],
      tables: [],
      events: [],
      risks: [],
    },
    {
      id: "developments",
      title: "Recent developments",
      unavailableReason: null,
      prose: [],
      tables: [],
      events: [
        {
          id: "dev-1",
          date: "2025-07-22",
          form: "6-K",
          items: [],
          headline:
            "Half year results furnished under cover of Form 6-K, with the interim statement attached.",
          factIds: [factId("event.h1_results", "2025-07-22")],
        },
      ],
      risks: [],
    },
    {
      id: "risks",
      title: "Risk factors",
      unavailableReason: null,
      prose: [
        {
          id: "risks-1",
          text: "Summarised from Item 3.D of the FY2024 annual report, in the order the filer presents them.",
          factIds: [latest("narrative.risk_factors")],
        },
      ],
      tables: [],
      events: [],
      risks: [
        {
          id: "risk-1",
          heading: "Execution of the transition to cloud delivery",
          summary:
            "Migrating an installed base from licences to subscriptions changes the timing of revenue recognition and could reduce revenue in transition years.",
          factIds: [latest("narrative.risk_factors")],
        },
        {
          id: "risk-2",
          heading: "Data protection and cross-border transfer",
          summary:
            "Operating cloud services across jurisdictions subjects the filer to divergent data protection regimes and to restrictions on cross-border transfer.",
          factIds: [latest("narrative.risk_factors")],
        },
        {
          id: "risk-3",
          heading: "Currency translation",
          summary:
            "Results are reported in euro while a substantial share of revenue is earned in other currencies, so translation affects reported growth.",
          factIds: [latest("narrative.risk_factors")],
        },
      ],
    },
  ],
  charts: {
    revenueMargin: {
      meta: {
        unitLabel: "EUR millions",
        factIds: [
          ...seriesFactIds(PERIODS, "income.revenue"),
          ...seriesFactIds(PERIODS, "ratio.gross_margin"),
        ],
      },
      points: PERIODS.map((period, i) => ({
        period: period.label,
        revenue: [30_871, 31_207, 34_176][i] ?? null,
        grossMarginPct: [69.8, 71.5, 73.3][i] ?? null,
        operatingMarginPct: [7.6, 13.3, 17.6][i] ?? null,
      })),
    },
    segmentMix: {
      meta: {
        unitLabel: "EUR millions",
        factIds: SEGMENT_NAMES.flatMap((_, index) =>
          seriesFactIds(PERIODS, `segment.s${index}.revenue`),
        ),
      },
      segments: SEGMENT_NAMES,
      points: PERIODS.map((period, i) => ({
        period: period.label,
        values: Object.fromEntries(
          SEGMENT_NAMES.map((name) => [name, SEGMENT_VALUES[name]?.[i] ?? 0]),
        ),
      })),
    },
    cashFlow: {
      meta: {
        unitLabel: "EUR millions",
        factIds: [
          ...seriesFactIds(PERIODS, "cashflow.operating"),
          ...seriesFactIds(PERIODS, "cashflow.capex"),
          ...seriesFactIds(PERIODS, "cashflow.free_cash_flow"),
        ],
      },
      points: PERIODS.map((period, i) => ({
        period: period.label,
        operatingCashFlow: [4_357, 6_207, 7_186][i] ?? null,
        capex: [845, 767, 812][i] ?? null,
        freeCashFlow: [3_512, 5_440, 6_374][i] ?? null,
      })),
    },
    // Tier 3 was unreachable on this run. The report is complete without it.
    peerValuation: null,
  },
  compliance: {
    factCount: FACTS.length,
    tierCounts: {
      1: FACTS.filter((fact) => fact.tier === 1).length,
      2: FACTS.filter((fact) => fact.tier === 2).length,
      3: FACTS.filter((fact) => fact.tier === 3).length,
      4: FACTS.filter((fact) => fact.tier === 4).length,
    },
    citedFigureCount: 41,
    figureCount: 41,
    coverageDisplay: "100%",
    coverageRatio: 1,
    passed: true,
    verifiedAt: "2025-11-28T15:03:44Z",
  },
  //: No link was attached to this run — the ordinary case, and the one the
  //: report has to render without leaving an empty heading behind.
  sourceNotes: [],
};
