/**
 * ============================================================================
 * FIXTURE DATA. NOT A GENERATED REPORT.
 * See the notice in `lib/mock/factory.ts`. Figures below are placeholders for
 * interface development and are not sourced from filings.
 * ============================================================================
 *
 * A domestic filer: 10-K, us-gaap, USD, market data available. This fixture is
 * the dense case — every section populated, every chart present.
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
import type { Fact, FigureRow, ReportDocument } from "@/lib/types";

const REPORT_ID = "fixture-domestic";
const CIK = "0000320193";
const CIK_INT = "320193";

function archive(accessionNo: string, document: string): string {
  const nodash = accessionNo.replace(/-/g, "");
  return `https://www.sec.gov/Archives/edgar/data/${CIK_INT}/${nodash}/${document}`;
}

function index(accessionNo: string): string {
  const nodash = accessionNo.replace(/-/g, "");
  return `https://www.sec.gov/Archives/edgar/data/${CIK_INT}/${nodash}/${accessionNo}-index.htm`;
}

function filing(
  accessionNo: string,
  form: string,
  filedDate: string,
  periodOfReport: string | null,
  document: string,
  items?: readonly string[],
): MockSource {
  return {
    accessionNo,
    form,
    baseForm: form.replace("/A", ""),
    filedDate,
    periodOfReport,
    documentUrl: archive(accessionNo, document),
    indexUrl: index(accessionNo),
    tier: 1,
    sourceType: "sec_xbrl",
    items,
  };
}

// --- Sources ---------------------------------------------------------------

const FY2025_10K = filing(
  "0000320193-25-000073",
  "10-K",
  "2025-10-31",
  "2025-09-27",
  "aapl-20250927.htm",
);

const FY2024_10K = filing(
  "0000320193-24-000123",
  "10-K",
  "2024-11-01",
  "2024-09-28",
  "aapl-20240928.htm",
);

const FY2023_10K = filing(
  "0000320193-23-000106",
  "10-K",
  "2023-11-03",
  "2023-09-30",
  "aapl-20230930.htm",
);

const NARRATIVE_10K: MockSource = {
  ...FY2025_10K,
  sourceType: "sec_filing",
  documentUrl: archive("0000320193-25-000073", "aapl-20250927.htm"),
};

const EARNINGS_8K = filing(
  "0000320193-25-000068",
  "8-K",
  "2025-10-30",
  "2025-10-30",
  "aapl-20251030.htm",
  ["2.02", "9.01"],
);

const BUYBACK_8K = filing(
  "0000320193-25-000041",
  "8-K",
  "2025-05-01",
  "2025-05-01",
  "aapl-20250501.htm",
  ["2.02", "8.01"],
);

const PROXY = filing(
  "0000320193-25-000013",
  "DEF 14A",
  "2025-01-10",
  "2025-01-10",
  "aapl-20250110.htm",
);

/**
 * Tier 3. Market data has no accession number, so it is keyed by its as-of
 * date. The rail renders it as a market card, and no Section 3 figure may
 * cite it — that rule is enforced in the backend, not here.
 */
const MARKET: MockSource = {
  accessionNo: "MKT-2025-11-28",
  form: "Market data",
  baseForm: "Market data",
  filedDate: "2025-11-28",
  periodOfReport: "2025-11-28",
  documentUrl: "https://finance.yahoo.com/quote/AAPL",
  indexUrl: "https://finance.yahoo.com/quote/AAPL",
  tier: 3,
  sourceType: "market_data",
};

const PERIODS: readonly MockPeriod[] = [
  {
    label: "FY2023",
    fiscalYear: 2023,
    periodStart: "2022-10-02",
    periodEnd: "2023-09-30",
    source: FY2023_10K,
  },
  {
    label: "FY2024",
    fiscalYear: 2024,
    periodStart: "2023-10-01",
    periodEnd: "2024-09-28",
    source: FY2024_10K,
  },
  {
    label: "FY2025",
    fiscalYear: 2025,
    periodStart: "2024-09-29",
    periodEnd: "2025-09-27",
    source: FY2025_10K,
  },
];

const PERIOD_LABELS = PERIODS.map((period) => period.label);
const LATEST_END = "2025-09-27";

// --- Facts -----------------------------------------------------------------

const USD_M = "USD millions";
const PCT = "percent";

const statementFacts: Fact[] = [
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "income.revenue",
    label: "Revenue",
    unit: USD_M,
    values: [383_285, 391_035, 416_161],
    displays: ["383,285", "391,035", "416,161"],
    resolvedTag: "RevenueFromContractWithCustomerExcludingAssessedTax",
    taxonomy: "us-gaap",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "income.gross_profit",
    label: "Gross profit",
    unit: USD_M,
    values: [169_148, 180_683, 196_250],
    displays: ["169,148", "180,683", "196,250"],
    resolvedTag: "GrossProfit",
    taxonomy: "us-gaap",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "income.operating_income",
    label: "Operating income",
    unit: USD_M,
    values: [114_301, 123_216, 134_410],
    displays: ["114,301", "123,216", "134,410"],
    resolvedTag: "OperatingIncomeLoss",
    taxonomy: "us-gaap",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "income.net_income",
    label: "Net income",
    unit: USD_M,
    values: [96_995, 93_736, 102_480],
    displays: ["96,995", "93,736", "102,480"],
    resolvedTag: "NetIncomeLoss",
    taxonomy: "us-gaap",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "income.diluted_eps",
    label: "Diluted earnings per share",
    unit: "USD",
    values: [6.13, 6.08, 6.87],
    displays: ["6.13", "6.08", "6.87"],
    resolvedTag: "EarningsPerShareDiluted",
    taxonomy: "us-gaap",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "balance.total_assets",
    label: "Total assets",
    unit: USD_M,
    values: [352_583, 364_980, 372_820],
    displays: ["352,583", "364,980", "372,820"],
    resolvedTag: "Assets",
    taxonomy: "us-gaap",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "balance.total_debt",
    label: "Total debt",
    unit: USD_M,
    values: [111_088, 106_629, 98_450],
    displays: ["111,088", "106,629", "98,450"],
    resolvedTag: "DebtLongtermAndShorttermCombinedAmount",
    taxonomy: "us-gaap",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "cashflow.operating",
    label: "Cash from operations",
    unit: USD_M,
    values: [110_543, 118_254, 128_310],
    displays: ["110,543", "118,254", "128,310"],
    resolvedTag: "NetCashProvidedByUsedInOperatingActivities",
    taxonomy: "us-gaap",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "cashflow.capex",
    label: "Capital expenditure",
    unit: USD_M,
    values: [10_959, 9_447, 11_150],
    displays: ["10,959", "9,447", "11,150"],
    resolvedTag: "PaymentsToAcquirePropertyPlantAndEquipment",
    taxonomy: "us-gaap",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "cashflow.free_cash_flow",
    label: "Free cash flow",
    unit: USD_M,
    values: [99_584, 108_807, 117_160],
    displays: ["99,584", "108,807", "117,160"],
    isCalculated: true,
    formula: "Cash from operations − capital expenditure",
  }),
];

const analysisFacts: Fact[] = [
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "ratio.gross_margin",
    label: "Gross margin",
    unit: PCT,
    values: [44.1, 46.2, 47.2],
    displays: ["44.1%", "46.2%", "47.2%"],
    isCalculated: true,
    formula: "Gross profit ÷ revenue",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "ratio.operating_margin",
    label: "Operating margin",
    unit: PCT,
    values: [29.8, 31.5, 32.3],
    displays: ["29.8%", "31.5%", "32.3%"],
    isCalculated: true,
    formula: "Operating income ÷ revenue",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "ratio.net_margin",
    label: "Net margin",
    unit: PCT,
    values: [25.3, 24.0, 24.6],
    displays: ["25.3%", "24.0%", "24.6%"],
    isCalculated: true,
    formula: "Net income ÷ revenue",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "ratio.fcf_conversion",
    label: "Free cash flow conversion",
    unit: PCT,
    values: [102.7, 116.1, 114.3],
    displays: ["102.7%", "116.1%", "114.3%"],
    isCalculated: true,
    formula: "Free cash flow ÷ net income",
  }),
  ...makeSeries(REPORT_ID, PERIODS, {
    metric: "growth.revenue_yoy",
    label: "Revenue growth",
    unit: PCT,
    values: [-2.8, 2.0, 6.4],
    displays: ["(2.8%)", "2.0%", "6.4%"],
    isCalculated: true,
    formula: "Revenue ÷ prior-year revenue − 1",
  }),
];

const SEGMENT_NAMES = ["iPhone", "Mac", "iPad", "Wearables", "Services"] as const;

const SEGMENT_VALUES: Readonly<Record<string, readonly number[]>> = {
  iPhone: [200_583, 201_183, 209_320],
  Mac: [29_357, 29_984, 33_410],
  iPad: [28_300, 26_694, 27_980],
  Wearables: [39_845, 37_005, 36_120],
  Services: [85_200, 96_169, 109_331],
};

const SEGMENT_DISPLAYS: Readonly<Record<string, readonly string[]>> = {
  iPhone: ["200,583", "201,183", "209,320"],
  Mac: ["29,357", "29,984", "33,410"],
  iPad: ["28,300", "26,694", "27,980"],
  Wearables: ["39,845", "37,005", "36,120"],
  Services: ["85,200", "96,169", "109,331"],
};

const segmentFacts: Fact[] = SEGMENT_NAMES.flatMap((name) =>
  makeSeries(REPORT_ID, PERIODS, {
    metric: `segment.${name.toLowerCase()}.revenue`,
    label: `${name} revenue`,
    unit: USD_M,
    values: SEGMENT_VALUES[name] ?? [],
    displays: SEGMENT_DISPLAYS[name] ?? [],
    segment: {
      axis: "srt:ProductOrServiceAxis",
      member: `aapl:${name}Member`,
      label: name,
    },
    resolvedTag: "RevenueFromContractWithCustomerExcludingAssessedTax",
    taxonomy: "us-gaap",
  }),
);

const marketFacts: Fact[] = [
  makeFact(REPORT_ID, {
    metric: "market.price",
    label: "Share price",
    value: 268.42,
    displayValue: "268.42",
    unit: "USD",
    periodEnd: "2025-11-28",
    source: MARKET,
  }),
  makeFact(REPORT_ID, {
    metric: "market.market_cap",
    label: "Market capitalisation",
    value: 3_982_400,
    displayValue: "3,982,400",
    unit: USD_M,
    periodEnd: "2025-11-28",
    source: MARKET,
  }),
  makeFact(REPORT_ID, {
    metric: "market.pe_trailing",
    label: "Price / earnings, trailing",
    value: 30.2,
    displayValue: "30.2x",
    unit: "multiple",
    periodEnd: "2025-11-28",
    source: MARKET,
  }),
  makeFact(REPORT_ID, {
    metric: "market.ev_ebitda",
    label: "EV / EBITDA",
    value: 22.4,
    displayValue: "22.4x",
    unit: "multiple",
    periodEnd: "2025-11-28",
    source: MARKET,
  }),
];

const narrativeFacts: Fact[] = [
  makeFact(REPORT_ID, {
    metric: "entity.employees",
    label: "Full-time equivalent employees",
    value: 164_000,
    displayValue: "164,000",
    unit: "people",
    periodEnd: LATEST_END,
    fiscalYear: 2025,
    source: NARRATIVE_10K,
    resolvedTag: "dei:EntityNumberOfEmployees",
    taxonomy: "dei",
  }),
  makeFact(REPORT_ID, {
    metric: "narrative.business",
    label: "Business description",
    value: null,
    displayValue: "Item 1, Business",
    periodEnd: LATEST_END,
    fiscalYear: 2025,
    source: NARRATIVE_10K,
  }),
  makeFact(REPORT_ID, {
    metric: "narrative.risk_factors",
    label: "Risk factors",
    value: null,
    displayValue: "Item 1A, Risk factors",
    periodEnd: LATEST_END,
    fiscalYear: 2025,
    source: NARRATIVE_10K,
  }),
  makeFact(REPORT_ID, {
    metric: "governance.say_on_pay",
    label: "Executive compensation",
    value: null,
    displayValue: "Proxy statement, compensation discussion",
    periodEnd: "2025-01-10",
    source: PROXY,
  }),
];

const developmentFacts: Fact[] = [
  makeFact(REPORT_ID, {
    metric: "event.q4_results",
    label: "Fourth quarter results",
    value: null,
    displayValue: "Item 2.02, results of operations",
    periodEnd: "2025-10-30",
    source: EARNINGS_8K,
  }),
  makeFact(REPORT_ID, {
    metric: "event.capital_return",
    label: "Capital return programme",
    value: null,
    displayValue: "Item 8.01, other events",
    periodEnd: "2025-05-01",
    source: BUYBACK_8K,
  }),
];

const FACTS: readonly Fact[] = [
  ...statementFacts,
  ...analysisFacts,
  ...segmentFacts,
  ...narrativeFacts,
  ...developmentFacts,
  ...marketFacts,
];

// --- Document --------------------------------------------------------------

const latest = (metric: string): string => factId(metric, LATEST_END);

export const DOMESTIC_FIXTURE: ReportDocument = {
  report: {
    id: REPORT_ID,
    ticker: "AAPL",
    cik: CIK,
    status: "complete",
    errorMessage: null,
    createdAt: "2025-11-28T14:31:52Z",
    completedAt: "2025-11-28T14:33:24Z",
  },
  company: {
    cik: CIK,
    ticker: "AAPL",
    name: "Apple Inc.",
    filerType: "domestic",
    sicCode: "3571",
    sector: "Electronic computers",
    fiscalYearEnd: "0927",
    reportingCurrency: "USD",
  },
  depth: "standard",
  facts: FACTS,
  filings: [
    toFilingRef(FY2025_10K, CIK),
    toFilingRef(FY2024_10K, CIK),
    toFilingRef(FY2023_10K, CIK),
    toFilingRef(EARNINGS_8K, CIK),
    toFilingRef(BUYBACK_8K, CIK),
    toFilingRef(PROXY, CIK),
  ],
  sections: [
    {
      id: "snapshot",
      title: "Snapshot",
      unavailableReason: null,
      prose: [
        {
          id: "snapshot-1",
          text: "Apple Inc. files a 10-K with a fiscal year ending on the last Saturday of September and reports in US dollars under us-gaap. The most recent annual filing covers the year to 27 September 2025 and reports revenue of 416,161 million dollars against an operating margin of 32.3 per cent.",
          factIds: [latest("income.revenue"), latest("ratio.operating_margin")],
        },
      ],
      tables: [
        {
          id: "snapshot-key",
          caption: "At a glance",
          periods: ["Latest"],
          unitNote: "USD millions, except per-share amounts and multiples",
          rows: [
            { label: "Revenue, FY2025", factIds: [latest("income.revenue")] },
            {
              label: "Operating income, FY2025",
              factIds: [latest("income.operating_income")],
            },
            {
              label: "Free cash flow, FY2025",
              factIds: [latest("cashflow.free_cash_flow")],
              emphasis: "derived",
            },
            {
              label: "Diluted earnings per share, FY2025",
              factIds: [latest("income.diluted_eps")],
            },
            {
              label: "Employees",
              factIds: [latest("entity.employees")],
            },
            {
              label: "Share price",
              factIds: [factId("market.price", "2025-11-28")],
            },
            {
              label: "Market capitalisation",
              factIds: [factId("market.market_cap", "2025-11-28")],
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
          text: "The filer describes itself in Item 1 as designing, manufacturing and marketing smartphones, personal computers, tablets, wearables and accessories, and selling a range of related services. It reports five revenue categories rather than operating segments defined by product economics, so segment figures below follow the categories as disclosed.",
          factIds: [latest("narrative.business")],
        },
        {
          id: "business-2",
          text: "Services reached 109,331 million dollars in FY2025 and is the only category to have grown in each of the three years presented. iPhone remains the largest category at 209,320 million dollars.",
          factIds: [
            latest("segment.services.revenue"),
            latest("segment.iphone.revenue"),
          ],
        },
        {
          id: "business-3",
          text: "Item 1 reports 164,000 full-time equivalent employees at the end of the fiscal year.",
          factIds: [latest("entity.employees")],
        },
      ],
      tables: [
        {
          id: "business-segments",
          caption: "Revenue by category",
          periods: PERIOD_LABELS,
          unitNote: "USD millions",
          rows: [
            ...SEGMENT_NAMES.map<FigureRow>((name) => ({
              label: name,
              factIds: seriesFactIds(
                PERIODS,
                `segment.${name.toLowerCase()}.revenue`,
              ),
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
          text: "Revenue grew to 416,161 million dollars in FY2025 from 391,035 million a year earlier. Gross profit of 196,250 million and operating income of 134,410 million both rose faster than revenue.",
          factIds: [
            latest("income.revenue"),
            factId("income.revenue", "2024-09-28"),
            latest("income.gross_profit"),
            latest("income.operating_income"),
          ],
        },
        {
          id: "financials-2",
          text: "Cash from operations of 128,310 million funded capital expenditure of 11,150 million, leaving free cash flow of 117,160 million. Total debt fell for a third consecutive year, to 98,450 million.",
          factIds: [
            latest("cashflow.operating"),
            latest("cashflow.capex"),
            latest("cashflow.free_cash_flow"),
            latest("balance.total_debt"),
          ],
        },
      ],
      tables: [
        {
          id: "financials-income",
          caption: "Income statement",
          periods: PERIOD_LABELS,
          unitNote: "USD millions, except per-share amounts",
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
              label: "Operating income",
              factIds: seriesFactIds(PERIODS, "income.operating_income"),
            },
            {
              label: "Net income",
              factIds: seriesFactIds(PERIODS, "income.net_income"),
            },
            {
              label: "Diluted earnings per share",
              factIds: seriesFactIds(PERIODS, "income.diluted_eps"),
            },
          ],
        },
        {
          id: "financials-cash",
          caption: "Cash flow and balance sheet",
          periods: PERIOD_LABELS,
          unitNote: "USD millions",
          rows: [
            {
              label: "Cash from operations",
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
              label: "Total assets",
              factIds: seriesFactIds(PERIODS, "balance.total_assets"),
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
          text: "Gross margin has widened in each of the three years presented, from 44.1 per cent to 47.2 per cent, while operating margin moved from 29.8 to 32.3 per cent. The gap between the two has been broadly stable, so the improvement sits in cost of sales rather than in operating leverage.",
          factIds: [
            factId("ratio.gross_margin", "2023-09-30"),
            latest("ratio.gross_margin"),
            factId("ratio.operating_margin", "2023-09-30"),
            latest("ratio.operating_margin"),
          ],
        },
        {
          id: "analysis-2",
          text: "Free cash flow exceeded net income in all three years, with conversion of 114.3 per cent in FY2025. Revenue returned to growth of 6.4 per cent after a 2.8 per cent decline in FY2023.",
          factIds: [
            latest("ratio.fcf_conversion"),
            latest("growth.revenue_yoy"),
            factId("growth.revenue_yoy", "2023-09-30"),
          ],
        },
      ],
      tables: [
        {
          id: "analysis-ratios",
          caption: "Margins and returns",
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
              label: "Net margin",
              factIds: seriesFactIds(PERIODS, "ratio.net_margin"),
              emphasis: "derived",
            },
            {
              label: "Free cash flow conversion",
              factIds: seriesFactIds(PERIODS, "ratio.fcf_conversion"),
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
      unavailableReason: null,
      prose: [
        {
          id: "peers-1",
          text: "The peer set is drawn from SIC 3571 and adjacent codes with comparable revenue scale. Multiples are market data and carry a Tier 3 marker; they are never used to support a figure in the financial highlights.",
          factIds: [factId("market.pe_trailing", "2025-11-28")],
        },
      ],
      tables: [
        {
          id: "peers-multiples",
          caption: "Valuation",
          periods: ["Current"],
          unitNote: "Multiples. Market data, as at 28 November 2025.",
          rows: [
            {
              label: "Price / earnings, trailing",
              factIds: [factId("market.pe_trailing", "2025-11-28")],
            },
            {
              label: "EV / EBITDA",
              factIds: [factId("market.ev_ebitda", "2025-11-28")],
            },
            {
              label: "Market capitalisation",
              factIds: [factId("market.market_cap", "2025-11-28")],
            },
          ],
        },
      ],
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
          date: "2025-10-30",
          form: "8-K",
          items: ["2.02", "9.01"],
          headline:
            "Fourth quarter and full year results furnished, with the earnings release attached as Exhibit 99.1.",
          factIds: [factId("event.q4_results", "2025-10-30")],
        },
        {
          id: "dev-2",
          date: "2025-05-01",
          form: "8-K",
          items: ["2.02", "8.01"],
          headline:
            "Board authorised an increase to the share repurchase programme and raised the quarterly dividend.",
          factIds: [factId("event.capital_return", "2025-05-01")],
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
          text: "Summarised from Item 1A of the FY2025 annual report, in the order the filer presents them. These are the filer's disclosures, not an assessment of them.",
          factIds: [latest("narrative.risk_factors")],
        },
      ],
      tables: [],
      events: [],
      risks: [
        {
          id: "risk-1",
          heading: "Concentration in a single product line",
          summary:
            "A majority of revenue comes from one product category, and a decline in its unit sales or pricing would have a disproportionate effect on results.",
          factIds: [latest("narrative.risk_factors")],
        },
        {
          id: "risk-2",
          heading: "Manufacturing and supply concentration",
          summary:
            "Substantially all manufacturing is performed by outsourcing partners concentrated in a small number of locations, exposing supply to regional disruption.",
          factIds: [latest("narrative.risk_factors")],
        },
        {
          id: "risk-3",
          heading: "Regulatory action on digital marketplaces",
          summary:
            "Legislation and enforcement affecting app distribution and payment terms could require changes to commercial terms and reduce services revenue.",
          factIds: [latest("narrative.risk_factors")],
        },
        {
          id: "risk-4",
          heading: "Foreign exchange",
          summary:
            "A majority of net sales are made outside the United States, and results are reported in dollars, so a strengthening dollar reduces reported revenue.",
          factIds: [latest("narrative.risk_factors")],
        },
      ],
    },
  ],
  charts: {
    revenueMargin: {
      meta: {
        unitLabel: "USD millions",
        factIds: [
          ...seriesFactIds(PERIODS, "income.revenue"),
          ...seriesFactIds(PERIODS, "ratio.gross_margin"),
          ...seriesFactIds(PERIODS, "ratio.operating_margin"),
        ],
      },
      points: PERIODS.map((period, i) => ({
        period: period.label,
        revenue: [383_285, 391_035, 416_161][i] ?? null,
        grossMarginPct: [44.1, 46.2, 47.2][i] ?? null,
        operatingMarginPct: [29.8, 31.5, 32.3][i] ?? null,
      })),
    },
    segmentMix: {
      meta: {
        unitLabel: "USD millions",
        factIds: SEGMENT_NAMES.flatMap((name) =>
          seriesFactIds(PERIODS, `segment.${name.toLowerCase()}.revenue`),
        ),
      },
      segments: [...SEGMENT_NAMES],
      points: PERIODS.map((period, i) => ({
        period: period.label,
        values: Object.fromEntries(
          SEGMENT_NAMES.map((name) => [name, SEGMENT_VALUES[name]?.[i] ?? 0]),
        ),
      })),
    },
    cashFlow: {
      meta: {
        unitLabel: "USD millions",
        factIds: [
          ...seriesFactIds(PERIODS, "cashflow.operating"),
          ...seriesFactIds(PERIODS, "cashflow.capex"),
          ...seriesFactIds(PERIODS, "cashflow.free_cash_flow"),
        ],
      },
      points: PERIODS.map((period, i) => ({
        period: period.label,
        operatingCashFlow: [110_543, 118_254, 128_310][i] ?? null,
        capex: [10_959, 9_447, 11_150][i] ?? null,
        freeCashFlow: [99_584, 108_807, 117_160][i] ?? null,
      })),
    },
    peerValuation: {
      meta: {
        unitLabel: "Multiple",
        factIds: [
          factId("market.pe_trailing", "2025-11-28"),
          factId("market.ev_ebitda", "2025-11-28"),
        ],
      },
      points: [
        {
          ticker: "AAPL",
          name: "Apple Inc.",
          isSubject: true,
          evToEbitda: 22.4,
          priceToEarnings: 30.2,
        },
        {
          ticker: "MSFT",
          name: "Microsoft Corporation",
          isSubject: false,
          evToEbitda: 23.9,
          priceToEarnings: 34.1,
        },
        {
          ticker: "GOOGL",
          name: "Alphabet Inc.",
          isSubject: false,
          evToEbitda: 16.8,
          priceToEarnings: 24.6,
        },
        {
          ticker: "DELL",
          name: "Dell Technologies Inc.",
          isSubject: false,
          evToEbitda: 9.1,
          priceToEarnings: 14.2,
        },
        {
          ticker: "HPQ",
          name: "HP Inc.",
          isSubject: false,
          evToEbitda: 7.4,
          priceToEarnings: 9.8,
        },
      ],
    },
  },
  compliance: {
    factCount: FACTS.length,
    tierCounts: {
      1: FACTS.filter((fact) => fact.tier === 1).length,
      2: FACTS.filter((fact) => fact.tier === 2).length,
      3: FACTS.filter((fact) => fact.tier === 3).length,
      4: FACTS.filter((fact) => fact.tier === 4).length,
    },
    citedFigureCount: 68,
    figureCount: 68,
    coverageDisplay: "100%",
    coverageRatio: 1,
    passed: true,
    verifiedAt: "2025-11-28T14:33:20Z",
  },
};
