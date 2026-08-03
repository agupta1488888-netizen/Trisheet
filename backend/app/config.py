"""Application configuration.

Every tunable in the system lives here. No magic numbers or inline literals are
permitted elsewhere in the codebase.

Secrets are read from the environment only. They are never committed and never
logged — `SecretStr` is used so that an accidental repr does not leak them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- SEC EDGAR endpoints ----------------------------------------------------
# Filings are immutable, so responses from these endpoints are cached forever.

SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANY_FACTS_URL_TEMPLATE = (
    "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
)
SEC_COMPANY_CONCEPT_URL_TEMPLATE = (
    "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/{taxonomy}/{tag}.json"
)

#: Older filings are sharded out of the submissions document into these.
SEC_SUBMISSIONS_SHARD_URL_TEMPLATE = "https://data.sec.gov/submissions/{name}"

SEC_ARCHIVES_BASE_URL = "https://www.sec.gov"

#: Directory listing for one filing. `cik_int` is unpadded; EDGAR archive paths
#: use the integer form while the JSON APIs use the zero-padded form.
SEC_FILING_INDEX_URL_TEMPLATE = (
    "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/"
    "{accession_dashed}-index.htm"
)

SEC_FILING_DOCUMENT_URL_TEMPLATE = (
    "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{document}"
)

# --- SEC EDGAR client limits ------------------------------------------------

#: CIK is always zero-padded to this width in EDGAR URLs.
CIK_PAD_WIDTH = 10

#: Global ceiling across all workers, enforced by a shared token bucket.
EDGAR_MAX_REQUESTS_PER_SECOND = 10

#: A request that receives 429 is retried at most this many times.
EDGAR_MAX_RETRY_ATTEMPTS = 3

#: Exponential backoff base. Delay for attempt n is BASE * (2 ** n) seconds,
#: unless the response carries a Retry-After header, which always wins.
EDGAR_BACKOFF_BASE_SECONDS = 1.0

EDGAR_REQUEST_TIMEOUT_SECONDS = 30.0

#: SEC returns 403 to any request without a contact-bearing User-Agent.
EDGAR_USER_AGENT_TEMPLATE = "Trisheet {contact_email}"

# --- Response cache ---------------------------------------------------------

#: Filings are immutable, so their responses are cached with no expiry.
EDGAR_CACHE_TTL_PERMANENT: float | None = None

#: The ticker index changes as companies list and delist.
COMPANY_TICKERS_TTL_SECONDS = 86_400.0

#: XBRL company concepts gain new periods as filings arrive.
XBRL_CONCEPT_TTL_SECONDS = 86_400.0

EDGAR_CACHE_DIR_NAME = ".edgar_cache"

# --- Taxonomies -------------------------------------------------------------

#: Tried in order. Foreign private issuers fall through to IFRS.
XBRL_TAXONOMY_PREFERENCE = ("us-gaap", "ifrs-full")

#: Monetary concepts tried, in order, to discover a filer's reporting currency
#: from its XBRL unit keys. Assets is used because virtually every filer in
#: either taxonomy reports it.
CURRENCY_PROBE_CONCEPTS = (
    ("us-gaap", "Assets"),
    ("ifrs-full", "Assets"),
)

#: Unit keys that are not currencies and must never be read as one.
#: "USD/shares" is the key SEC's companyfacts API uses for per-share
#: concepts (EPS, dividends per share) — not a hyphenated variant.
NON_CURRENCY_UNIT_KEYS = frozenset({"shares", "pure", "USD/shares"})

# --- Forms ------------------------------------------------------------------

#: The annual report each filer type files. Order matters: the most recently
#: filed annual form decides the filer type, so a company that migrated from
#: 20-F to 10-K is classified by what it files now.
ANNUAL_FORM_TO_FILER_TYPE = {
    "10-K": "domestic",
    "20-F": "foreign",
    "40-F": "canadian",
}

#: Forms a report may draw on. Anything else is dropped from the manifest.
PERMITTED_FORMS = frozenset(
    {"10-K", "10-Q", "8-K", "DEF 14A", "20-F", "40-F", "6-K"}
)

#: Current reports, whose exhibits carry the earnings release and slides.
CURRENT_REPORT_FORMS = frozenset({"8-K", "6-K"})

#: Exhibits worth resolving: the press release and the presentation.
EXHIBIT_TYPES_OF_INTEREST = ("EX-99.1", "EX-99.2")

#: Every current report costs one extra request to fetch its index, so only the
#: most recent ones are expanded. Older exhibits are fetched on demand.
MAX_CURRENT_REPORTS_WITH_EXHIBITS = 24

# --- Resolution -------------------------------------------------------------

#: Most candidates returned for an ambiguous query before the list is cut.
MAX_RESOLUTION_CANDIDATES = 10

#: Company-name suffixes stripped before comparing names, so that "Nike" and
#: "NIKE, Inc." compare equal.
COMPANY_NAME_SUFFIXES = (
    "incorporated",
    "corporation",
    "company",
    "limited",
    "holdings",
    "holding",
    "group",
    "plc",
    "inc",
    "corp",
    "ltd",
    "llc",
    "lp",
    "co",
    "sa",
    "nv",
    "ag",
)

# --- Rendering --------------------------------------------------------------

#: What a missing figure reads as. Never "N/A", never blank, never zero.
NOT_DISCLOSED_TEXT = "Not disclosed"

# --- XBRL period classification ---------------------------------------------
# EDGAR reports a period as a start/end pair, not a label. A 10-K carries the
# annual period alongside quarterly and year-to-date ones, so duration is what
# separates them. The bounds are wide because a 52/53-week fiscal calendar
# makes "a year" anything from 358 to 371 days.

ANNUAL_PERIOD_MIN_DAYS = 300
ANNUAL_PERIOD_MAX_DAYS = 400

QUARTERLY_PERIOD_MIN_DAYS = 60
QUARTERLY_PERIOD_MAX_DAYS = 120

#: Annual periods kept per metric, most recent first.
MAX_ANNUAL_PERIODS = 5

#: Bounds on the period count a "custom" depth request may ask for. XBRL
#: company-facts data thins out well before this ceiling for most filers, but
#: the bound exists to keep a malformed request from asking m03 to walk an
#: unbounded number of periods.
CUSTOM_PERIODS_MIN = 1
CUSTOM_PERIODS_MAX = 15

#: Suffix EDGAR appends to an amended form: "10-K" becomes "10-K/A".
AMENDMENT_FORM_SUFFIX = "/A"

# --- Sector templates -------------------------------------------------------
# A bank has no gross margin, a REIT's earnings are meaningless without the
# depreciation added back, and an insurer is judged on its combined ratio.
# Which figures are worth extracting, and which ratios mean anything, follow
# from the filer's industry — so the industry is read off the SIC code EDGAR
# reports and never guessed from the company name or its prose.


class SectorTemplate(StrEnum):
    """Which metric set a filer is analysed with."""

    #: Everything that is not a financial. Margins, returns, working capital.
    GENERAL = "general"
    #: Depository institutions and their holding companies. NIM, CET1.
    BANK = "bank"
    #: Real estate investment trusts. FFO, AFFO, NAV.
    REIT = "reit"
    #: Carriers and underwriters. Loss, expense and combined ratios.
    INSURANCE = "insurance"


#: SIC ranges, inclusive at both ends, mapped to the template they select.
#: Checked in order, so a narrow range must precede any range containing it.
#: Sources are the SEC's own SIC list; a code outside every range is GENERAL,
#: because an unrecognised industry is not a reason to guess at one.
SIC_SECTOR_TEMPLATE_RANGES: tuple[tuple[int, int, SectorTemplate], ...] = (
    # 6798 sits inside no other range but is listed first for emphasis: it is
    # the only code that makes a filer a REIT.
    (6798, 6798, SectorTemplate.REIT),
    # 6020-6036 commercial banks and savings institutions, 6120-6199 savings
    # and credit institutions, 6712 the bank holding companies most large
    # banks actually file under.
    (6020, 6036, SectorTemplate.BANK),
    (6120, 6199, SectorTemplate.BANK),
    (6712, 6712, SectorTemplate.BANK),
    # 6311-6411 life, property/casualty, surety, title and agency carriers.
    (6311, 6411, SectorTemplate.INSURANCE),
)


def sector_template_for_sic(sic_code: str | None) -> SectorTemplate:
    """The metric set a filer's SIC code selects.

    Pure and total: an absent, non-numeric or unrecognised code yields GENERAL
    rather than raising, because a report for an unclassified filer is still a
    report — it simply gets the general metric set.
    """
    if sic_code is None:
        return SectorTemplate.GENERAL

    try:
        code = int(sic_code.strip())
    except (AttributeError, ValueError):
        return SectorTemplate.GENERAL

    for low, high, template in SIC_SECTOR_TEMPLATE_RANGES:
        if low <= code <= high:
            return template
    return SectorTemplate.GENERAL


# --- Metric definitions -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """One extractable figure and every tag that might carry it.

    `us_gaap_tags` and `ifrs_tags` are ordered fallback lists: the first tag
    that resolves wins and is recorded on the resulting Fact. A tag is never
    inlined at a call site, so adding a filer's preferred tag is a change here
    and nowhere else.
    """

    metric: str
    label: str
    #: "duration" for flows (revenue), "instant" for stocks (total assets).
    period_type: Literal["duration", "instant"]
    us_gaap_tags: tuple[str, ...]
    ifrs_tags: tuple[str, ...] = ()
    #: True when a filer may legitimately not report it, so its absence is
    #: expected rather than a gap worth flagging.
    optional: bool = False
    #: Templates this metric is extracted for. Empty means every filer. A
    #: software company is never searched for a combined ratio, so the absence
    #: of one is never reported as a gap.
    sectors: tuple[SectorTemplate, ...] = ()

    def tags_for(self, taxonomy: str) -> tuple[str, ...]:
        """Ordered fallbacks for one taxonomy. Empty when it defines none."""
        if taxonomy == "us-gaap":
            return self.us_gaap_tags
        if taxonomy == "ifrs-full":
            return self.ifrs_tags
        return ()

    def applies_to(self, template: SectorTemplate) -> bool:
        """True when this metric is worth extracting for this filer."""
        return not self.sectors or template in self.sectors


#: Every metric m03 extracts. Ordered: this is also the display order.
METRIC_SPECS: tuple[MetricSpec, ...] = (
    MetricSpec(
        metric="income.revenue",
        label="Revenue",
        period_type="duration",
        us_gaap_tags=(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
        ),
        ifrs_tags=("Revenue", "RevenueFromContractsWithCustomers"),
    ),
    MetricSpec(
        metric="income.cost_of_revenue",
        label="Cost of revenue",
        period_type="duration",
        us_gaap_tags=(
            "CostOfRevenue",
            "CostOfGoodsAndServicesSold",
            "CostOfGoodsSold",
            "CostOfServices",
        ),
        ifrs_tags=("CostOfSales",),
    ),
    MetricSpec(
        metric="income.gross_profit",
        label="Gross profit",
        period_type="duration",
        us_gaap_tags=("GrossProfit",),
        ifrs_tags=("GrossProfit",),
    ),
    MetricSpec(
        metric="income.research_and_development",
        label="Research and development",
        period_type="duration",
        us_gaap_tags=("ResearchAndDevelopmentExpense",),
        ifrs_tags=("ResearchAndDevelopmentExpense",),
        optional=True,
    ),
    MetricSpec(
        metric="income.selling_general_administrative",
        label="Selling, general and administrative",
        period_type="duration",
        us_gaap_tags=(
            "SellingGeneralAndAdministrativeExpense",
            "GeneralAndAdministrativeExpense",
        ),
        ifrs_tags=("SellingGeneralAndAdministrativeExpense",),
        optional=True,
    ),
    MetricSpec(
        metric="income.operating_income",
        label="Operating income",
        period_type="duration",
        us_gaap_tags=("OperatingIncomeLoss",),
        ifrs_tags=("ProfitLossFromOperatingActivities",),
    ),
    MetricSpec(
        metric="income.pretax_income",
        label="Income before tax",
        period_type="duration",
        us_gaap_tags=(
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        ),
        ifrs_tags=("ProfitLossBeforeTax",),
    ),
    MetricSpec(
        metric="income.income_tax_expense",
        label="Income tax expense",
        period_type="duration",
        us_gaap_tags=("IncomeTaxExpenseBenefit",),
        ifrs_tags=("IncomeTaxExpenseContinuingOperations",),
    ),
    MetricSpec(
        metric="income.net_income",
        label="Net income",
        period_type="duration",
        us_gaap_tags=(
            "NetIncomeLoss",
            "ProfitLoss",
            "NetIncomeLossAvailableToCommonStockholdersBasic",
        ),
        ifrs_tags=("ProfitLoss", "ProfitLossAttributableToOwnersOfParent"),
    ),
    MetricSpec(
        metric="income.eps_diluted",
        label="Diluted earnings per share",
        period_type="duration",
        us_gaap_tags=("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"),
        ifrs_tags=("DilutedEarningsLossPerShare",),
    ),
    MetricSpec(
        metric="income.shares_diluted",
        label="Diluted weighted average shares",
        period_type="duration",
        us_gaap_tags=("WeightedAverageNumberOfDilutedSharesOutstanding",),
        ifrs_tags=(
            "WeightedAverageNumberOfDilutedOrdinarySharesOutstanding",
        ),
    ),
    MetricSpec(
        metric="income.eps_basic",
        label="Basic earnings per share",
        period_type="duration",
        us_gaap_tags=("EarningsPerShareBasic", "EarningsPerShareBasicAndDiluted"),
        ifrs_tags=("BasicEarningsLossPerShare",),
    ),
    MetricSpec(
        metric="income.shares_basic",
        label="Basic weighted average shares",
        period_type="duration",
        us_gaap_tags=("WeightedAverageNumberOfSharesOutstandingBasic",),
        ifrs_tags=("WeightedAverageShares",),
    ),
    MetricSpec(
        metric="income.operating_expenses",
        label="Operating expenses",
        period_type="duration",
        us_gaap_tags=("OperatingExpenses", "OperatingCostsAndExpenses"),
        ifrs_tags=("OperatingExpense",),
        optional=True,
    ),
    MetricSpec(
        metric="income.depreciation_amortisation",
        label="Depreciation and amortisation",
        period_type="duration",
        us_gaap_tags=(
            "DepreciationDepletionAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
            "DepreciationAndAmortization",
        ),
        ifrs_tags=("DepreciationAndAmortisationExpense",),
        optional=True,
    ),
    MetricSpec(
        metric="income.interest_expense",
        label="Interest expense",
        period_type="duration",
        us_gaap_tags=(
            "InterestExpense",
            "InterestExpenseDebt",
            "InterestExpenseNonoperating",
            "InterestIncomeExpenseNet",
        ),
        ifrs_tags=("FinanceCosts",),
        optional=True,
    ),
    MetricSpec(
        metric="income.dividends_per_share",
        label="Dividends declared per share",
        period_type="duration",
        us_gaap_tags=(
            "CommonStockDividendsPerShareDeclared",
            "CommonStockDividendsPerShareCashPaid",
        ),
        ifrs_tags=("DividendsPerShareDeclared",),
        optional=True,
    ),
    MetricSpec(
        metric="balance.cash_and_equivalents",
        label="Cash and cash equivalents",
        period_type="instant",
        us_gaap_tags=(
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ),
        ifrs_tags=("CashAndCashEquivalents",),
    ),
    MetricSpec(
        metric="balance.inventory",
        label="Inventory",
        period_type="instant",
        # A filer whose inventory is entirely finished goods often tags only
        # that: NIKE has reported "Inventories" under
        # InventoryFinishedGoodsNetOfReserves since 2012 and left InventoryNet
        # behind.
        us_gaap_tags=(
            "InventoryNet",
            "InventoryFinishedGoodsNetOfReserves",
            "InventoryGross",
        ),
        ifrs_tags=("Inventories",),
        optional=True,
    ),
    MetricSpec(
        metric="balance.accounts_receivable",
        label="Accounts receivable",
        period_type="instant",
        us_gaap_tags=(
            "AccountsReceivableNetCurrent",
            "ReceivablesNetCurrent",
            "AccountsReceivableGrossCurrent",
        ),
        ifrs_tags=("TradeAndOtherCurrentReceivables",),
        optional=True,
    ),
    MetricSpec(
        metric="balance.current_assets",
        label="Total current assets",
        period_type="instant",
        us_gaap_tags=("AssetsCurrent",),
        ifrs_tags=("CurrentAssets",),
        optional=True,
    ),
    MetricSpec(
        metric="balance.total_assets",
        label="Total assets",
        period_type="instant",
        us_gaap_tags=("Assets",),
        ifrs_tags=("Assets",),
    ),
    MetricSpec(
        metric="balance.accounts_payable",
        label="Accounts payable",
        period_type="instant",
        us_gaap_tags=(
            "AccountsPayableCurrent",
            "AccountsPayableTradeCurrent",
            "AccountsPayableAndAccruedLiabilitiesCurrent",
        ),
        ifrs_tags=("TradeAndOtherCurrentPayables",),
        optional=True,
    ),
    MetricSpec(
        metric="balance.current_liabilities",
        label="Total current liabilities",
        period_type="instant",
        us_gaap_tags=("LiabilitiesCurrent",),
        ifrs_tags=("CurrentLiabilities",),
        optional=True,
    ),
    MetricSpec(
        metric="balance.short_term_debt",
        label="Short-term debt",
        period_type="instant",
        us_gaap_tags=(
            "ShortTermBorrowings",
            "DebtCurrent",
            "LongTermDebtCurrent",
            "OtherShortTermBorrowings",
        ),
        ifrs_tags=("ShorttermBorrowings", "CurrentPortionOfLongtermBorrowings"),
        optional=True,
    ),
    MetricSpec(
        metric="balance.total_liabilities",
        label="Total liabilities",
        period_type="instant",
        us_gaap_tags=("Liabilities",),
        ifrs_tags=("Liabilities",),
    ),
    MetricSpec(
        metric="balance.long_term_debt",
        label="Long-term debt",
        period_type="instant",
        us_gaap_tags=("LongTermDebtNoncurrent", "LongTermDebt"),
        ifrs_tags=("NoncurrentPortionOfNoncurrentBorrowings",),
        optional=True,
    ),
    MetricSpec(
        metric="balance.total_equity",
        label="Total equity",
        period_type="instant",
        # StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest
        # goes first: `balance.total_liabilities` is consolidated `Liabilities`,
        # which carries noncontrolling-interest subsidiaries in full, so it only
        # ties to Assets against the equity figure that also includes their
        # NCI. `StockholdersEquity` (parent-only) is a fallback for filers with
        # no NCI, who often tag only that one.
        us_gaap_tags=(
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "StockholdersEquity",
        ),
        ifrs_tags=("Equity", "EquityAttributableToOwnersOfParent"),
    ),
    MetricSpec(
        metric="balance.shares_outstanding",
        label="Shares outstanding",
        period_type="instant",
        us_gaap_tags=("CommonStockSharesOutstanding", "CommonStockSharesIssued"),
        ifrs_tags=("NumberOfSharesOutstanding",),
        optional=True,
    ),
    MetricSpec(
        metric="cashflow.operating",
        label="Net cash from operating activities",
        period_type="duration",
        us_gaap_tags=(
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
        ifrs_tags=("CashFlowsFromUsedInOperatingActivities",),
    ),
    MetricSpec(
        metric="cashflow.investing",
        label="Net cash from investing activities",
        period_type="duration",
        us_gaap_tags=(
            "NetCashProvidedByUsedInInvestingActivities",
            "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations",
        ),
        ifrs_tags=("CashFlowsFromUsedInInvestingActivities",),
    ),
    MetricSpec(
        metric="cashflow.financing",
        label="Net cash from financing activities",
        period_type="duration",
        us_gaap_tags=(
            "NetCashProvidedByUsedInFinancingActivities",
            "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations",
        ),
        ifrs_tags=("CashFlowsFromUsedInFinancingActivities",),
    ),
    MetricSpec(
        metric="cashflow.capital_expenditure",
        label="Capital expenditure",
        period_type="duration",
        us_gaap_tags=(
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
        ),
        ifrs_tags=(
            "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        ),
    ),
    MetricSpec(
        metric="cashflow.dividends_paid",
        label="Dividends paid",
        period_type="duration",
        us_gaap_tags=(
            "PaymentsOfDividendsCommonStock",
            "PaymentsOfDividends",
            "PaymentsOfDividendsMinorityInterest",
        ),
        ifrs_tags=("DividendsPaidClassifiedAsFinancingActivities",),
        optional=True,
    ),
    MetricSpec(
        metric="cashflow.share_repurchases",
        label="Share repurchases",
        period_type="duration",
        us_gaap_tags=(
            "PaymentsForRepurchaseOfCommonStock",
            "PaymentsForRepurchaseOfEquity",
        ),
        ifrs_tags=("PaymentsToAcquireOrRedeemEntitysShares",),
        optional=True,
    ),
)

#: The consolidated metric a segment breakdown subdivides. Segment facts are
#: emitted under SEGMENT_METRIC so that m11 can check they sum to the
#: consolidated figure held under SEGMENT_SOURCE_METRIC.
SEGMENT_SOURCE_METRIC = "income.revenue"
SEGMENT_METRIC = "segment.revenue"
SEGMENT_METRIC_LABEL = "Revenue by segment"

# --- Sector-specific metric definitions -------------------------------------
# Extracted only for the template that needs them. A software company is never
# searched for a combined ratio, so it never reports a gap where one cannot
# exist. These are kept apart from METRIC_SPECS so that a caller which has not
# yet learned about sectors keeps its present behaviour exactly.
#
# Regulatory capital is the one place where filers routinely use extension
# tags: CET1 is frequently reported under a company-specific element rather
# than a us-gaap one. The ladders below try the standard elements and stop.
# When none resolves, the ratio is simply not computed — that is the correct
# outcome, not a defect to be papered over with an approximation.

SECTOR_METRIC_SPECS: dict[SectorTemplate, tuple[MetricSpec, ...]] = {
    SectorTemplate.BANK: (
        MetricSpec(
            metric="bank.net_interest_income",
            label="Net interest income",
            period_type="duration",
            us_gaap_tags=(
                "InterestIncomeExpenseNet",
                "InterestIncomeExpenseAfterProvisionForLoanLoss",
            ),
            sectors=(SectorTemplate.BANK,),
        ),
        MetricSpec(
            metric="bank.interest_earning_assets",
            label="Average interest-earning assets",
            period_type="instant",
            us_gaap_tags=(
                "InterestEarningAssets",
                "AverageInterestEarningAssets",
            ),
            optional=True,
            sectors=(SectorTemplate.BANK,),
        ),
        MetricSpec(
            metric="bank.noninterest_income",
            label="Non-interest income",
            period_type="duration",
            us_gaap_tags=("NoninterestIncome",),
            optional=True,
            sectors=(SectorTemplate.BANK,),
        ),
        MetricSpec(
            metric="bank.noninterest_expense",
            label="Non-interest expense",
            period_type="duration",
            us_gaap_tags=("NoninterestExpense",),
            optional=True,
            sectors=(SectorTemplate.BANK,),
        ),
        MetricSpec(
            metric="bank.provision_for_credit_losses",
            label="Provision for credit losses",
            period_type="duration",
            us_gaap_tags=(
                "ProvisionForLoanLeaseAndOtherLosses",
                "ProvisionForLoanAndLeaseLosses",
                "ProvisionForCreditLossesExpenseReversal",
            ),
            optional=True,
            sectors=(SectorTemplate.BANK,),
        ),
        MetricSpec(
            metric="bank.total_loans",
            label="Loans and leases",
            period_type="instant",
            us_gaap_tags=(
                "LoansAndLeasesReceivableNetReportedAmount",
                "FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss",
                "NotesReceivableNet",
            ),
            optional=True,
            sectors=(SectorTemplate.BANK,),
        ),
        MetricSpec(
            metric="bank.total_deposits",
            label="Total deposits",
            period_type="instant",
            us_gaap_tags=("Deposits", "InterestBearingDepositLiabilities"),
            optional=True,
            sectors=(SectorTemplate.BANK,),
        ),
        MetricSpec(
            metric="bank.cet1_capital",
            label="Common equity tier 1 capital",
            period_type="instant",
            us_gaap_tags=(
                "BankingRegulationCommonEquityTierOneRiskBasedCapitalActual",
                "TierOneRiskBasedCapital",
            ),
            optional=True,
            sectors=(SectorTemplate.BANK,),
        ),
        MetricSpec(
            metric="bank.risk_weighted_assets",
            label="Risk-weighted assets",
            period_type="instant",
            us_gaap_tags=(
                "BankingRegulationRiskWeightedAssetsActual",
                "RiskWeightedAssets",
            ),
            optional=True,
            sectors=(SectorTemplate.BANK,),
        ),
    ),
    SectorTemplate.REIT: (
        MetricSpec(
            metric="reit.real_estate_depreciation",
            label="Real estate depreciation and amortisation",
            period_type="duration",
            us_gaap_tags=(
                "RealEstateInvestmentPropertyDepreciation",
                "DepreciationAndAmortizationRealEstate",
            ),
            optional=True,
            sectors=(SectorTemplate.REIT,),
        ),
        MetricSpec(
            metric="reit.gains_on_property_sales",
            label="Gains on sales of real estate",
            period_type="duration",
            us_gaap_tags=(
                "GainLossOnSaleOfProperties",
                "GainsLossesOnSalesOfInvestmentRealEstate",
                "GainLossOnDispositionOfRealEstate",
            ),
            optional=True,
            sectors=(SectorTemplate.REIT,),
        ),
        MetricSpec(
            metric="reit.impairment_of_real_estate",
            label="Impairment of real estate",
            period_type="duration",
            us_gaap_tags=("ImpairmentOfRealEstate",),
            optional=True,
            sectors=(SectorTemplate.REIT,),
        ),
        MetricSpec(
            metric="reit.recurring_capex",
            label="Recurring capital expenditure",
            period_type="duration",
            us_gaap_tags=(
                "PaymentsForCapitalImprovements",
                "PaymentsForTenantImprovements",
            ),
            optional=True,
            sectors=(SectorTemplate.REIT,),
        ),
        MetricSpec(
            metric="reit.straight_line_rent",
            label="Straight-line rent adjustment",
            period_type="duration",
            us_gaap_tags=(
                "StraightLineRent",
                "AmortizationOfAboveAndBelowMarketLeases",
            ),
            optional=True,
            sectors=(SectorTemplate.REIT,),
        ),
        MetricSpec(
            metric="reit.real_estate_fair_value",
            label="Real estate at fair value",
            period_type="instant",
            us_gaap_tags=(
                "RealEstateInvestmentPropertyAtFairValue",
                "InvestmentPropertyFairValueDisclosure",
            ),
            ifrs_tags=("InvestmentProperty",),
            optional=True,
            sectors=(SectorTemplate.REIT,),
        ),
    ),
    SectorTemplate.INSURANCE: (
        MetricSpec(
            metric="insurance.earned_premiums",
            label="Net premiums earned",
            period_type="duration",
            us_gaap_tags=(
                "PremiumsEarnedNet",
                "PremiumsEarnedNetPropertyAndCasualty",
                "PremiumsEarnedNetLife",
            ),
            sectors=(SectorTemplate.INSURANCE,),
        ),
        MetricSpec(
            metric="insurance.losses_incurred",
            label="Losses and loss adjustment expenses",
            period_type="duration",
            us_gaap_tags=(
                "PolicyholderBenefitsAndClaimsIncurredNet",
                "LiabilityForClaimsAndClaimsAdjustmentExpenseIncurredClaims1",
                "PolicyholderBenefitsAndClaimsIncurredHealthCare",
            ),
            optional=True,
            sectors=(SectorTemplate.INSURANCE,),
        ),
        MetricSpec(
            metric="insurance.underwriting_expenses",
            label="Underwriting expenses",
            period_type="duration",
            us_gaap_tags=(
                "OtherUnderwritingExpense",
                "DeferredPolicyAcquisitionCostAmortizationExpense",
                "InsuranceCommissionsAndFees",
            ),
            optional=True,
            sectors=(SectorTemplate.INSURANCE,),
        ),
        MetricSpec(
            metric="insurance.net_premiums_written",
            label="Net premiums written",
            period_type="duration",
            us_gaap_tags=("PremiumsWrittenNet",),
            optional=True,
            sectors=(SectorTemplate.INSURANCE,),
        ),
    ),
}


def metric_specs_for(template: SectorTemplate) -> tuple[MetricSpec, ...]:
    """Every metric worth extracting for a filer analysed with `template`.

    The general set always applies; a financial filer gets its own metrics on
    top. Order is preserved because it is also the display order.
    """
    general = tuple(spec for spec in METRIC_SPECS if spec.applies_to(template))
    return general + SECTOR_METRIC_SPECS.get(template, ())

# --- Segment dimensions -----------------------------------------------------

#: Axes a revenue breakdown is reported along, in preference order. Only these
#: are read; an arbitrary axis (fair-value hierarchy, debt instrument) is not a
#: segmentation of revenue and must never be presented as one.
SEGMENT_AXES: tuple[str, ...] = (
    "us-gaap:StatementBusinessSegmentsAxis",
    "srt:ProductOrServiceAxis",
    "srt:StatementGeographicalAxis",
    "us-gaap:StatementGeographicalAxis",
)

#: The subset of SEGMENT_AXES that breaks revenue down by where it was earned
#: rather than by what was sold. Growth attribution reports the two separately,
#: because "the Americas grew" and "footwear grew" are different claims about
#: the same revenue and must never be added together.
GEOGRAPHIC_SEGMENT_AXES: tuple[str, ...] = (
    "srt:StatementGeographicalAxis",
    "us-gaap:StatementGeographicalAxis",
)

#: (axis, member) pairs that qualify a segment context without subdividing it
#: further. A context of {ConsolidationItemsAxis: OperatingSegmentsMember,
#: StatementBusinessSegmentsAxis: AmericasSegmentMember} is the Americas
#: segment; the first pair only says "this is a reportable segment".
SEGMENT_NEUTRAL_QUALIFIERS: tuple[tuple[str, str], ...] = (
    ("us-gaap:ConsolidationItemsAxis", "us-gaap:OperatingSegmentsMember"),
    ("srt:ConsolidationItemsAxis", "us-gaap:OperatingSegmentsMember"),
)

#: Members that restate the consolidated total rather than a component of it.
#: Including them would double the segment sum.
SEGMENT_EXCLUDED_MEMBERS: frozenset[str] = frozenset(
    {
        "us-gaap:ConsolidatedEntitiesMember",
        "srt:ConsolidationEliminationsMember",
        "us-gaap:MaterialReconcilingItemsMember",
        "us-gaap:CorporateNonSegmentMember",
        "us-gaap:IntersegmentEliminationMember",
    }
)

#: Instance-document filenames that are not the instance. EDGAR ships the
#: linkbases in the same directory with these suffixes.
XBRL_LINKBASE_SUFFIXES = ("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml")

#: XML namespaces used when reading an XBRL instance document.
XBRLI_NAMESPACE = "http://www.xbrl.org/2003/instance"
XBRLDI_NAMESPACE = "http://xbrl.org/2006/xbrldi"

#: Cap on instance-document size we will parse. Instances run to a few MB; a
#: far larger one means something unexpected, and parsing it would stall the
#: report for no benefit.
MAX_INSTANCE_DOCUMENT_BYTES = 64 * 1024 * 1024

# --- Extraction confidence --------------------------------------------------
# Confidence records how far down the fallback ladder a figure was found. It is
# never a guess about whether the figure is right — a resolved tag is exact.

CONFIDENCE_EXACT = 1.0

#: Charged once per step past a metric's preferred tag.
CONFIDENCE_PENALTY_PER_TAG_FALLBACK = 0.05

#: Charged when the figure came from a taxonomy other than the filer's first.
CONFIDENCE_PENALTY_TAXONOMY_FALLBACK = 0.05

#: Confidence never falls below this while a tag genuinely resolved.
CONFIDENCE_FLOOR = 0.5

#: A NOT_DISCLOSED marker carries no value, so it carries no confidence.
CONFIDENCE_NOT_DISCLOSED = 0.0

# --- Source tiers -----------------------------------------------------------

#: Tiers accepted by section 3 (financial highlights). Enforced in code.
SECTION_3_ALLOWED_TIERS = (1, 2)

#: The tier each source type may claim. A fact whose tier disagrees with its
#: source type is rejected at write time — that pairing is how a market figure
#: would otherwise smuggle itself into section 3 wearing a Tier 1 label.
SOURCE_TYPE_TIERS: dict[str, int] = {
    "sec_filing": 1,
    "sec_xbrl": 1,
    "company_site": 2,
    "investor_presentation": 2,
    "press_release": 2,
    "market_data": 3,
    "news": 4,
}

#: Metric prefixes belonging to each report section, used to answer "may this
#: fact appear here?" and to serve section queries out of the fact store.
#:
#: Everything m07 computes belongs to section 3. That is deliberate: a derived
#: figure is only as sound as what it was derived from, so it inherits section
#: 3's hard tier block rather than sitting outside it. A ratio computed from a
#: market price is not here — it is `valuation.`, and it belongs to section 5.
SECTION_METRIC_PREFIXES: dict[int, tuple[str, ...]] = {
    1: ("profile.",),
    2: ("business.",),
    3: (
        # Reported, from m03.
        "income.",
        "balance.",
        "cashflow.",
        # Computed, from m07. Every one carries its formula.
        "ratio.",
        "derived.",
        "margin.",
        "return.",
        "dupont.",
        "liquidity.",
        "leverage.",
        "working_capital.",
        "shareholder.",
        "per_share.",
        "growth.",
        "common_size.",
        "ttm.",
        "bridge.",
        "attribution.",
        # Sector-specific, from m07.
        "bank.",
        "insurance.",
        "reit.",
    ),
    4: ("segment.",),
    5: ("market.", "valuation."),
    6: ("peer.",),
    7: ("development.",),
    8: ("risk.",),
}

#: Facts are written in batches of this size. Supabase rejects unbounded
#: payloads, and a partial batch is easier to report on than a partial row.
FACT_INSERT_BATCH_SIZE = 200

#: Columns of the unique constraint on `facts`, used as the upsert conflict
#: target. Must match `facts_unique_metric_period` in db/schema.sql.
FACT_CONFLICT_COLUMNS = (
    "report_id",
    "metric",
    "period_end",
    "period_start",
    "segment_axis",
    "segment_member",
)

#: A fact's citation id is derived from what identifies it, so the id a
#: sentence cites is computable from the fact itself — the writer does not need
#: a database round trip to cite, and the checker does not need one to verify.
FACT_ID_PREFIX = "f"
FACT_ID_LENGTH = 12


# --- Analysis (m07) ---------------------------------------------------------
# Every figure the system derives is produced with these. They are tunables,
# not preferences: changing one changes what the reader is shown, so each says
# why it is what it is.

#: Compound growth windows offered, in years. A window needs one more annual
#: period than its length — a 3-year CAGR spans four fiscal year ends.
CAGR_WINDOWS_YEARS = (3, 5)

#: Quarters summed to make a trailing twelve months.
TTM_QUARTER_COUNT = 4

#: Consecutive quarters may abut with at most this much slack. A 52/53-week
#: calendar means quarter boundaries do not land on the same date every year,
#: so exact adjacency would reject legitimate sequences.
TTM_QUARTER_ADJACENCY_TOLERANCE_DAYS = 7

#: Days used to convert a balance into a number of days of activity. The
#: calendar year, not the fiscal one: a 53-week filer's cycle is still quoted
#: in ordinary days, and mixing the two would make years incomparable.
DAYS_IN_YEAR = 365

#: A denominator whose magnitude is below this is treated as zero and the
#: metric is not computed. Guards against a ratio exploding on a rounding
#: residue rather than on a real figure.
ANALYSIS_ZERO_TOLERANCE = 1e-9

#: A decomposition that misses its total by more than this many percentage
#: points reports the gap rather than absorbing it: the unattributed part of
#: revenue growth, the part of a margin move the bridge does not explain.
#:
#: Deliberately far tighter than m11's SEGMENT_SUM_TOLERANCE, which checks
#: whether segment *levels* sum to a reported total and can afford half a per
#: cent of slack. This is a tolerance on a *change*, where the same slack would
#: be most of the number: half a point of revenue growth is hundreds of
#: millions for a large filer, which is a missing segment, not rounding.
#: Filings do round, so it is not zero — but it is small enough that anything
#: suppressed is genuinely below the reporting precision.
RESIDUAL_TOLERANCE_POINTS = 0.05

#: Decimal places a derived value is rounded to before it is stored. Rounding
#: at the boundary is what makes the module's output byte-identical across
#: runs and platforms; it is not a display concern.
PERCENT_DECIMAL_PLACES = 4
RATIO_DECIMAL_PLACES = 6
CURRENCY_DECIMAL_PLACES = 2

#: Units a derived figure is measured in. A ratio is not a percentage and a
#: change in a percentage is neither, which is why these are distinct.
UNIT_PERCENT = "percent"
UNIT_PERCENTAGE_POINTS = "pp"
UNIT_RATIO = "ratio"
UNIT_TIMES = "x"
UNIT_DAYS = "days"
UNIT_SHARES = "shares"
UNIT_PER_SHARE_TEMPLATE = "{currency}/share"

#: How each unit renders. Figures are monospace and right-aligned in the
#: interface, so what is fixed here is precision, not alignment.
PERCENT_DISPLAY_TEMPLATE = "{value:.1f}%"
PERCENTAGE_POINTS_DISPLAY_TEMPLATE = "{value:+.1f}pp"
RATIO_DISPLAY_TEMPLATE = "{value:.2f}"
TIMES_DISPLAY_TEMPLATE = "{value:.2f}x"
DAYS_DISPLAY_TEMPLATE = "{value:.0f} days"
PER_SHARE_DISPLAY_TEMPLATE = "{value:.2f}"

#: Metrics that get a growth series. Ordered: this is the display order.
GROWTH_METRICS = (
    "income.revenue",
    "income.gross_profit",
    "income.operating_income",
    "income.net_income",
    "income.eps_diluted",
    "cashflow.operating",
    "balance.total_assets",
    "balance.total_equity",
)

#: Income statement lines expressed as a percentage of revenue.
COMMON_SIZE_INCOME_METRICS = (
    "income.cost_of_revenue",
    "income.gross_profit",
    "income.research_and_development",
    "income.selling_general_administrative",
    "income.operating_expenses",
    "income.operating_income",
    "income.interest_expense",
    "income.pretax_income",
    "income.income_tax_expense",
    "income.net_income",
)

#: Balance sheet lines expressed as a percentage of total assets.
COMMON_SIZE_BALANCE_METRICS = (
    "balance.cash_and_equivalents",
    "balance.accounts_receivable",
    "balance.inventory",
    "balance.current_assets",
    "balance.accounts_payable",
    "balance.current_liabilities",
    "balance.short_term_debt",
    "balance.long_term_debt",
    "balance.total_liabilities",
    "balance.total_equity",
)

#: Flow metrics a trailing twelve months is built for. Instant metrics are
#: excluded by construction: a balance is already a point in time and summing
#: four of them would be meaningless.
TTM_METRICS = (
    "income.revenue",
    "income.gross_profit",
    "income.operating_income",
    "income.net_income",
    "cashflow.operating",
    "cashflow.capital_expenditure",
)


# --- Peer selection (m08) ---------------------------------------------------
# Peers are selected by a ladder of decreasing authority. The filer's own
# disclosure comes first: a compensation committee that names its benchmarking
# group has stated who it believes its peers are, and a 10-K competition
# paragraph names who it competes with. Only when the filer says nothing does
# the system fall back to industry classification, and only when that fails
# does a model get asked. Which rung answered is recorded on every peer.

#: Peers wanted. The ladder stops as soon as this many have been accepted.
PEER_TARGET_COUNT = 8

#: Hard ceiling on peers returned, however many rungs contributed.
PEER_MAX_COUNT = 12

#: Peers given a full financial and valuation comparison. Smaller than
#: PEER_MAX_COUNT on purpose: a comparison reads the peer's own filings and
#: fetches its own quote, so it costs a full extraction per name rather than a
#: mention in a proxy. An analyst's comp table is a handful of names, not the
#: whole ladder.
PEER_COMPARISON_MAX_COUNT = 5

#: Names harvested from one document before the scan gives up. A proxy that
#: mentions two hundred companies is not naming a peer group.
PEER_MAX_NAMES_PER_DOCUMENT = 60

#: The proxy statement carrying the compensation peer group.
PROXY_FORM = "DEF 14A"

#: Phrases that mark the compensation peer group discussion in a proxy. The
#: text window around a match is where names are looked for; a company name
#: elsewhere in a 90-page proxy is not a peer.
PEER_GROUP_MARKERS = (
    "compensation peer group",
    "peer group",
    "comparator group",
    "compensation comparator",
    "benchmarking group",
    "selected peer companies",
)

#: Phrases that mark the competition discussion in an annual report. The
#: bare word "competition" is deliberately excluded: it appears throughout
#: ordinary risk-factor boilerplate ("intense competition for talent",
#: "highly competitive industry") with no named competitor nearby, and
#: matching it opened a window practically anywhere in the document.
COMPETITION_MARKERS = (
    "we compete",
    "our competitors",
    "principal competitors",
    "competitive landscape",
)

#: Characters of text either side of a marker that are scanned for names. A
#: filer's actual named peer or competitor list sits in the same sentence or
#: short passage as the marker phrase, not thousands of characters away —
#: a wide window pulls in unrelated names (e.g. a proxy's compensation
#: consultant, mentioned near "peer group" but not part of it).
PEER_CONTEXT_WINDOW_CHARS = 400

#: Cap on the document text parsed out of one filing. Proxies and 10-Ks run to
#: a few hundred kilobytes; far more means something unexpected.
MAX_FILING_TEXT_BYTES = 16 * 1024 * 1024

#: A candidate name must be at least this long to be looked up. Shorter tokens
#: match the ticker index by accident far more often than on purpose.
PEER_NAME_MIN_CHARS = 4

#: Longest run of words considered as a single company name.
PEER_NAME_MAX_WORDS = 5

#: Normalised names that match the ticker index but are never a peer mention.
#: These are real listed companies whose names are also ordinary English.
PEER_NAME_STOPWORDS = frozenset(
    {
        "a",
        "at",
        "on",
        "it",
        "so",
        "up",
        "all",
        "and",
        "are",
        "for",
        "new",
        "one",
        "our",
        "the",
        "two",
        "key",
        "open",
        "next",
        "power",
        "select",
        "global",
        "national",
        "international",
        "general",
        "united",
        "american",
        "first",
        "group",
        "company",
        "companies",
        "corporation",
        "holdings",
        "industries",
        "systems",
        "services",
        "technologies",
        "solutions",
        "partners",
        "paid",
        "people",
        "emerging",
        "compensation",
        "committee",
        "consultant",
        "consulting",
        "benchmark",
        "benchmarking",
    }
)

#: EDGAR's company browser, filtered by SIC. `output=atom` yields parseable XML
#: carrying each filer's CIK and name. This is the third rung of the ladder.
SEC_COMPANY_LIST_BY_SIC_URL_TEMPLATE = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&SIC={sic}"
    "&type={form}&dateb=&owner=include&count={count}&action=getcompany&output=atom"
)

#: Filers requested per SIC page. Not all resolve to a live ticker.
SIC_BROWSE_PAGE_SIZE = 100

#: The company list changes as filers register and deregister.
SIC_BROWSE_TTL_SECONDS = 86_400.0

#: Atom namespace EDGAR's company browser answers in.
ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"

#: Two fiscal year ends this many days apart or less are treated as the same
#: year end. A 52/53-week calendar moves a filer's year end by up to six days
#: between years, so 1231 and 0102 are the same year end, not a mismatch.
FISCAL_YEAR_END_TOLERANCE_DAYS = 10

#: Days in a year, for the circular distance between two MMDD year ends.
DAYS_IN_YEAR = 365

#: Tickers the model may propose in one call. It is asked for tickers only —
#: never for figures — and every one is resolved against the live index before
#: it is allowed into the report.
PEER_LLM_MAX_SUGGESTIONS = 10

# --- Recent developments (m09) ----------------------------------------------
# 8-K items are the filer's own index of what happened. Only the items that
# change how a company is understood are kept; the rest (2.02's companion
# 9.01 exhibit index, 5.03 bylaw amendments) are noise in a company profile.

#: 8-K items kept, mapped to what each one means, in EDGAR's numbering.
DEVELOPMENT_ITEMS: dict[str, str] = {
    "1.01": "Entry into a material definitive agreement",
    "1.03": "Bankruptcy or receivership",
    "2.01": "Completion of an acquisition or disposition of assets",
    "2.02": "Results of operations and financial condition",
    "2.03": "Creation of a direct financial obligation",
    "5.02": "Change of directors or principal officers",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
}

#: The item whose EX-99.1 carries the earnings release.
EARNINGS_RELEASE_ITEM = "2.02"

#: The exhibit that carries the press release for an earnings 8-K.
EARNINGS_RELEASE_EXHIBIT = "EX-99.1"

#: Developments kept, most recent first.
MAX_DEVELOPMENTS = 16

#: Earnings releases whose exhibit is fetched and parsed. Each costs a request.
MAX_EARNINGS_RELEASES_PARSED = 6

#: Sentences in a press release that state a headline result.
RESULT_MARKERS = (
    "revenue",
    "net sales",
    "net income",
    "earnings per share",
    "diluted eps",
    "operating income",
    "operating margin",
    "gross margin",
    "free cash flow",
)

#: Sentences in a press release that state guidance rather than a result.
#: Guidance is forward-looking and is labelled as such — never presented as a
#: reported figure.
GUIDANCE_MARKERS = (
    "guidance",
    "outlook",
    "we expect",
    "the company expects",
    "expects to",
    "anticipates",
    "full year",
    "full-year",
    "fiscal year",
    "forecast",
    "projects",
)

#: Headline sentences kept per release. A press release repeats its numbers.
MAX_RESULT_SENTENCES = 3

#: Guidance sentences kept per release.
MAX_GUIDANCE_SENTENCES = 2

#: A sentence longer than this is a paragraph the splitter failed on, not a
#: headline. Quoting it would fill the timeline with a page of boilerplate.
DEVELOPMENT_SENTENCE_MAX_CHARS = 400

#: A sentence shorter than this carries no claim worth quoting.
DEVELOPMENT_SENTENCE_MIN_CHARS = 40

#: Metrics m09 emits. All are textual: the value is a quotation, not a figure.
DEVELOPMENT_EVENT_METRIC = "development.event"
DEVELOPMENT_RESULT_METRIC = "development.result"
DEVELOPMENT_GUIDANCE_METRIC = "development.guidance"

#: Prefix on the display value of a guidance quotation, so that a
#: forward-looking statement can never be read as a reported figure.
GUIDANCE_LABEL_PREFIX = "Guidance:"

# --- Narrative sections (m04) ------------------------------------------------
# The annual report's prose, read out of the filing the filer actually filed.
# Every fact m04 emits is textual: the value is a quotation, never a figure.
# Nothing here is summarised or paraphrased — the filer's own words travel with
# the accession number they came from, and m10 is shown the text rather than
# asked to remember it.


@dataclass(frozen=True, slots=True)
class NarrativeSpec:
    """One item of an annual report, and how to find where it starts.

    `headings` are matched against the document text in order. The item runs
    from its heading to the start of the next item in `terminators`, which is
    how a 10-K's own table of contents is turned into boundaries without
    guessing at character offsets.
    """

    metric: str
    label: str
    #: Phrases that open the item, most specific first.
    headings: tuple[str, ...]
    #: Phrases that open whatever comes next, ending this item.
    terminators: tuple[str, ...]
    #: Forms this item exists in. A 20-F numbers its items differently.
    forms: frozenset[str]


#: A 10-K's items. "Item 1." and "Item 1A." both appear in the table of
#: contents before they appear as real headings, which is why the last match in
#: the document wins rather than the first — see `_locate` in m04.
NARRATIVE_SPECS: tuple[NarrativeSpec, ...] = (
    NarrativeSpec(
        metric="business.description",
        label="Business",
        headings=("item 1. business", "item 1 - business", "item 1. general"),
        terminators=("item 1a.", "item 1a ", "item 1b.", "item 2."),
        forms=frozenset({"10-K"}),
    ),
    NarrativeSpec(
        metric="risk.factors",
        label="Risk factors",
        headings=("item 1a. risk factors", "item 1a - risk factors", "item 1a."),
        terminators=("item 1b.", "item 1c.", "item 2.", "item 3."),
        forms=frozenset({"10-K"}),
    ),
    NarrativeSpec(
        metric="business.mdna",
        label="Management's discussion and analysis",
        headings=(
            "item 7. management's discussion",
            "item 7 - management's discussion",
            "item 7. management",
        ),
        terminators=("item 7a.", "item 8."),
        forms=frozenset({"10-K"}),
    ),
    # A foreign private issuer files a 20-F, whose items are numbered to its
    # own schedule. Item 4 is the business, Item 3.D the risk factors, Item 5
    # the operating and financial review.
    NarrativeSpec(
        metric="business.description",
        label="Information on the company",
        headings=(
            "item 4. information on the company",
            "item 4 - information on the company",
            "item 4. information",
        ),
        terminators=("item 4a.", "item 5."),
        forms=frozenset({"20-F"}),
    ),
    NarrativeSpec(
        metric="risk.factors",
        label="Risk factors",
        headings=("item 3.d. risk factors", "item 3.d risk factors", "risk factors"),
        terminators=("item 4.", "item 4a."),
        forms=frozenset({"20-F"}),
    ),
    NarrativeSpec(
        metric="business.mdna",
        label="Operating and financial review",
        headings=(
            "item 5. operating and financial review",
            "item 5 - operating and financial review",
            "item 5. operating",
        ),
        terminators=("item 6.",),
        forms=frozenset({"20-F"}),
    ),
    # A 40-F filer discloses under Canadian rules and attaches its annual
    # information form as an exhibit, so only the risk factors are reliably
    # locatable in the primary document.
    NarrativeSpec(
        metric="risk.factors",
        label="Risk factors",
        headings=("risk factors",),
        terminators=("management's discussion", "signatures"),
        forms=frozenset({"40-F"}),
    ),
)

#: A located section shorter than this is a table-of-contents entry or a
#: cross-reference, not the section itself.
NARRATIVE_MIN_SECTION_CHARS = 500

#: How much longer a heading's enclosing paragraph may run than the heading
#: phrase itself and still count as an isolated heading line rather than a
#: fragment of prose or of a longer, unrelated heading. A filer that does not
#: set its real headings in capitals (some 20-F filers use title case) gets no
#: signal from the capitalisation check, so this is the fallback that keeps a
#: bare phrase like "risk factors" from matching a cross-reference sentence or
#: a differently-scoped heading that happens to contain it, e.g. "Financial
#: Risk Factors and Risk Management" in the notes to the financial statements.
ISOLATED_HEADING_SLACK_CHARS = 24

#: Characters of a section carried into the report. A 10-K's risk factors run
#: to tens of thousands of words; what a profile needs is the disclosure, not
#: the whole of it. The text is truncated at a sentence boundary and the
#: truncation is stated, never hidden.
NARRATIVE_MAX_SECTION_CHARS = 24_000

#: Characters of the section shown as its display value. The full text travels
#: in the fact's own value for m10 to read; this is what a table cell shows.
NARRATIVE_SUMMARY_CHARS = 280

#: Appended when a section was cut. The reader is told, and given the filing.
NARRATIVE_TRUNCATION_NOTE = " […] Section truncated; read it in the filing."

#: Individually numbered risks pulled out of the risk factors item, for the
#: report's risk list. Each is the filer's own heading, never a paraphrase.
MAX_RISK_ITEMS = 12

#: A risk heading is a short line of prose. Longer than this and it is a
#: paragraph that happened to follow a blank line.
RISK_HEADING_MAX_CHARS = 220
RISK_HEADING_MIN_CHARS = 24

#: Words a risk heading opens with often enough to be worth recognising. A
#: heading is a claim about a hazard, so it reads like one.
RISK_HEADING_MARKERS = (
    "we ",
    "our ",
    "the company",
    "a ",
    "an ",
    "if ",
    "changes in",
    "failure to",
    "adverse",
    "risks",
)

# --- Market data (m05) ------------------------------------------------------
# Everything here is Tier 3. m05 is the only module permitted to reach a market
# data provider, and every fact it emits carries a metric prefix that belongs
# to section 5 — which is what makes it structurally incapable of reaching
# section 3, independently of the tier check m06 and m11 also apply.

#: Market data is not immutable, so unlike filings it expires.
MARKET_CACHE_TTL_SECONDS = 900.0

#: One provider gets this long before the chain moves to the next.
MARKET_REQUEST_TIMEOUT_SECONDS = 10.0

#: Attempts per provider before failover. Market data is optional, so this is
#: deliberately low: failing over is cheaper than retrying.
MARKET_MAX_ATTEMPTS_PER_PROVIDER = 2

#: Providers tried in order. The first that answers with a usable quote wins.
#: Both are public endpoints requiring no key; a keyed provider would be added
#: here and nowhere else.
MARKET_PROVIDER_CHAIN = ("yahoo", "stooq")

YAHOO_CHART_URL_TEMPLATE = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1d"
)
STOOQ_QUOTE_URL_TEMPLATE = (
    "https://stooq.com/q/l/?s={symbol}.us&f=sd2t2ohlcv&h&e=csv"
)

#: Sent on every market request. Some providers reject an unidentified client.
MARKET_USER_AGENT_TEMPLATE = "Trisheet {contact_email}"

#: Metric prefixes m05 is allowed to emit. Anything else is dropped before it
#: leaves the module, with the rejection logged.
MARKET_METRIC_PREFIXES = ("market.", "valuation.")

#: Stands in for an accession number on a market fact. A market figure has no
#: filing behind it, and the field is required — so it names the provider and
#: the moment the figure was read, which is the whole of its provenance.
MARKET_ACCESSION_TEMPLATE = "MARKET-{provider}-{as_of}"

# --- Prose generation (m10) -------------------------------------------------
# The model receives Fact objects and nothing else. No raw filing text, no web
# page, no market payload reaches the prompt — only figures that have already
# passed m06's provenance gate, rendered as a table.

#: Latest and most capable. The writing task is bounded — restate supplied
#: figures in prose — so capability here buys faithfulness, not creativity.
LLM_MODEL = "claude-opus-5"

#: Ceiling on sampling temperature. The writer must restate what it is given,
#: so variance is a defect rather than a feature. Applied with min(), so a
#: configured value above it is clamped rather than honoured.
#:
#: Not every model accepts a sampling parameter — the current Opus and Sonnet
#: generations reject `temperature` outright. `LLM_MODELS_ACCEPTING_TEMPERATURE`
#: records which do, and m10 sends the parameter only to those. On a model that
#: does not take one, the same intent is expressed by the effort setting below
#: and by the structured output schema, which leaves the model no room to
#: improvise a figure.
LLM_MAX_TEMPERATURE = 0.2

#: The temperature actually requested, clamped by the ceiling above.
LLM_TEMPERATURE = 0.2

#: Models that accept `temperature`. Sending it to any other returns HTTP 400.
LLM_MODELS_ACCEPTING_TEMPERATURE = frozenset(
    {"claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"}
)

#: Reasoning depth. Low: the model is restating supplied figures, not solving.
LLM_EFFORT = "low"

LLM_MAX_OUTPUT_TOKENS = 16_000
LLM_TIMEOUT_SECONDS = 120.0
LLM_MAX_RETRIES = 2

# --- Model cost --------------------------------------------------------------
# Published rates for LLM_MODEL, in US dollars per million tokens. Cost is
# computed per report at the rates in force when the run happened and then
# stored on the report — never re-derived later from a price list that has since
# changed, which would quietly restate history.
#
# Change these when the published rates change. A run priced before the change
# keeps the figure it was given.

LLM_INPUT_COST_PER_MTOK = 5.00
LLM_OUTPUT_COST_PER_MTOK = 25.00

#: A cached read is billed at a tenth of the input rate.
LLM_CACHE_READ_MULTIPLIER = 0.1

#: Writing the cache costs a quarter more than an ordinary input token.
LLM_CACHE_WRITE_MULTIPLIER = 1.25

TOKENS_PER_MILLION = 1_000_000

#: Facts serialised into one prompt. A report has a few hundred; beyond this
#: the table stops being something a model can hold, and the sections are
#: written separately anyway.
LLM_MAX_FACTS_IN_PROMPT = 200


@dataclass(frozen=True, slots=True)
class WriterSection:
    """One section of prose, and the facts it may draw on.

    `metric_sections` are keys of `SECTION_METRIC_PREFIXES`. A section's prompt
    contains those facts and no others, so a sentence in the business section
    cannot cite a figure it was never shown.
    """

    section_id: str
    title: str
    metric_sections: tuple[int, ...]
    #: What this section is for, in the interface's voice. Goes in the prompt.
    brief: str
    #: True when the section may be omitted entirely for want of facts.
    optional: bool = False


#: The sections m10 writes, in report order. Mirrors SECTION_ORDER in
#: frontend/lib/types.ts.
WRITER_SECTIONS: tuple[WriterSection, ...] = (
    WriterSection(
        section_id="snapshot",
        title="Snapshot",
        metric_sections=(1, 5),
        brief=(
            "State what the company is and how the market currently prices it. "
            "Two or three sentences."
        ),
    ),
    WriterSection(
        section_id="business",
        title="Business",
        metric_sections=(2,),
        brief=(
            "Describe what the company sells and how it makes money, using only "
            "the filer's own description."
        ),
        optional=True,
    ),
    WriterSection(
        section_id="financials",
        title="Financial highlights",
        metric_sections=(3,),
        brief=(
            "Summarise the reported figures. State the direction of each headline "
            "line and the period it covers. Do not compute anything."
        ),
    ),
    WriterSection(
        section_id="analysis",
        title="Analysis",
        metric_sections=(3, 4),
        brief=(
            "Explain what the computed ratios and the segment breakdown show. "
            "Every ratio has already been calculated; restate it, never derive it."
        ),
        optional=True,
    ),
    WriterSection(
        section_id="peers",
        title="Peers",
        metric_sections=(6,),
        brief=(
            "Name the comparable companies and state how each was selected. "
            "Disclose any fiscal year mismatch."
        ),
        optional=True,
    ),
    WriterSection(
        section_id="developments",
        title="Recent developments",
        metric_sections=(7,),
        brief=(
            "Summarise the filed current reports in date order. Quote guidance as "
            "guidance, never as a reported result."
        ),
        optional=True,
    ),
    WriterSection(
        section_id="risks",
        title="Risks",
        metric_sections=(8,),
        brief=(
            "State the risks the filer itself discloses. Do not rank them, do not "
            "add any the filer did not state."
        ),
        optional=True,
    ),
)

#: A generated sentence must cite at least this many facts when it contains a
#: figure. Prose that makes no numeric claim may cite none.
MIN_FACT_IDS_PER_NUMERIC_SENTENCE = 1

# --- Verification (m11) -----------------------------------------------------
# The blocking gate. Tolerances are stated rather than implied: a filing rounds
# its own figures, so "equal" means "equal within a bound someone chose and
# wrote down".

#: Segment figures may miss the consolidated total by this fraction. Filings
#: round segment tables independently of the income statement.
SEGMENT_SUM_TOLERANCE = 0.005

#: Assets minus liabilities minus equity, as a fraction of assets.
BALANCE_SHEET_TOLERANCE = 0.005

#: The change in cash may miss the sum of the three cash flow sections by this
#: fraction of the total. The gap is the effect of exchange rates on cash,
#: which is reported on its own line and is not extracted as a metric — so the
#: tolerance here is wider than the balance sheet's on purpose.
CASH_FLOW_TIE_TOLERANCE = 0.02

#: A figure in prose matches a fact when it is a correct rounding of that fact
#: to the precision the prose used. That is the whole rule: "$391.0 billion" is
#: a true statement about 391,035,000,000 and "$392 billion" is not, because
#: the second does not round back.
#:
#: This tolerance is not a licence to restate a figure loosely. It exists only
#: to absorb the representation error in a float that has been through a
#: division — which is why it is six orders of magnitude below the smallest
#: rounding step any filing uses.
PROSE_FIGURE_TOLERANCE = 1e-9

#: Scale words a figure in prose may carry, and what each multiplies by.
PROSE_SCALE_WORDS: dict[str, float] = {
    "thousand": 1e3,
    "k": 1e3,
    "million": 1e6,
    "mm": 1e6,
    "bn": 1e9,
    "billion": 1e9,
    "trillion": 1e12,
}

#: Numbers in prose that are never a claim about a figure: a fiscal year, a
#: quarter, an 8-K item number. Checked before a number is called unsourced.
PROSE_YEAR_MIN = 1900
PROSE_YEAR_MAX = 2100

#: Every figure in the report must resolve to a fact. Coverage below this fails
#: the gate. It is 1.0 because "most figures are sourced" is not the product.
REQUIRED_CITATION_COVERAGE = 1.0


@dataclass(frozen=True, slots=True)
class RangeBound:
    """A sanity bound on a metric, with the reason it exists.

    Bounds catch a figure that is arithmetically consistent but impossible —
    a 4000% gross margin from a unit error, a negative share count. They are
    deliberately wide: this is a smoke alarm, not a judgement about the
    business.
    """

    #: Exact metric, or a prefix ending in "." matching a family of metrics.
    metric: str
    minimum: float | None
    maximum: float | None
    reason: str


#: Percentages are stored as percent numbers, not fractions: a 43% margin is
#: 43.0 and carries the unit "percent". m07 produces them that way and m11
#: checks them that way, so the two cannot disagree about what 43.0 means.
#:
#: Bounds are matched most-specific first: an exact metric beats a prefix, and
#: a longer prefix beats a shorter one.
RANGE_SANITY_BOUNDS: tuple[RangeBound, ...] = (
    # Reported figures.
    RangeBound("income.revenue", 0.0, None, "Revenue is not negative."),
    RangeBound(
        "income.shares_diluted", 0.0, None, "A share count is not negative."
    ),
    RangeBound(
        "balance.total_assets", 0.0, None, "Total assets are not negative."
    ),
    RangeBound(
        "balance.cash_and_equivalents",
        0.0,
        None,
        "A cash balance is not negative.",
    ),
    RangeBound("balance.inventory", 0.0, None, "Inventory is not negative."),
    RangeBound("segment.revenue", None, None, "Segment revenue may be negative."),
    # Margins. Nothing can be more of revenue than all of it, so 100 is a
    # ceiling; the floor is wide because a pre-revenue filer's losses dwarf its
    # sales, which is a fact about the company rather than an extraction error.
    RangeBound(
        "margin.", -10_000.0, 100.0, "A margin cannot exceed 100% of revenue."
    ),
    RangeBound(
        "return.effective_tax_rate",
        -1_000.0,
        1_000.0,
        "A tax rate outside +/-1000% is an extraction error, not a tax rate.",
    ),
    # Liquidity and leverage are multiples, not percentages.
    RangeBound(
        "liquidity.", 0.0, 1_000.0, "A liquidity ratio is positive and bounded."
    ),
    RangeBound(
        "working_capital.",
        -10_000.0,
        10_000.0,
        "A working capital period is measured in days, not years.",
    ),
    RangeBound(
        "insurance.", 0.0, 10_000.0, "An insurance ratio is not negative."
    ),
    # Market figures. Tier 3, but a negative price is still impossible.
    RangeBound("market.price", 0.0, None, "A traded price is not negative."),
    RangeBound(
        "market.market_cap",
        0.0,
        None,
        "A market capitalisation is not negative.",
    ),
    RangeBound("market.volume", 0.0, None, "Volume is not negative."),
)

#: Unit m07 stamps on a percentage. Any fact carrying it is bounded by
#: `PERCENT_SANITY_BOUNDS`, whatever its metric — so a percentage metric added
#: later is covered without anyone remembering to extend the table above.
UNIT_PERCENT_NAME = "percent"

#: Wide on purpose. This catches a figure that is off by a factor of a
#: hundred, not a business having a bad year.
PERCENT_SANITY_BOUNDS = (-100_000.0, 100_000.0)


class CheckName:
    """Names of the checks m11 runs. Stable strings — the UI renders them."""

    FIGURES_SOURCED = "figures_sourced"
    CITATION_COVERAGE = "citation_coverage"
    SECTION_3_TIERS = "section_3_tiers"
    SEGMENT_SUM = "segment_sum"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW_TIE = "cash_flow_tie"
    RANGE_SANITY = "range_sanity"


# --- Analysis depth (m00, the orchestrator) ---------------------------------
# Depth is a budget, not a quality setting. It decides how many periods are
# tabulated and which optional modules run. It never relaxes a provenance rule:
# a brief report is a smaller report, never a less sourced one.


@dataclass(frozen=True, slots=True)
class DepthProfile:
    """What one depth setting buys."""

    #: Annual periods shown in the financial tables.
    periods: int
    #: Read the annual report's narrative sections.
    narrative: bool
    #: Read the XBRL instance document for the segment breakdown.
    segments: bool
    #: Run the peer ladder.
    peers: bool
    #: Build the 8-K timeline.
    developments: bool
    #: Ask a model to propose peers when no filed source named any.
    allow_model_peers: bool


DEPTH_PROFILES: dict[str, DepthProfile] = {
    "brief": DepthProfile(
        periods=3,
        narrative=False,
        segments=False,
        peers=False,
        developments=True,
        allow_model_peers=False,
    ),
    "standard": DepthProfile(
        periods=5,
        narrative=True,
        segments=True,
        peers=True,
        developments=True,
        allow_model_peers=False,
    ),
    "full": DepthProfile(
        periods=10,
        narrative=True,
        segments=True,
        peers=True,
        developments=True,
        allow_model_peers=True,
    ),
    "custom": DepthProfile(
        # Placeholder only: the run always overrides this with the caller's
        # requested period count before this profile is used. Same module
        # scope as "full" — depth's job here is purely how many years show.
        periods=MAX_ANNUAL_PERIODS,
        narrative=True,
        segments=True,
        peers=True,
        developments=True,
        allow_model_peers=True,
    ),
}

DEFAULT_DEPTH = "standard"

# --- Pipeline steps ----------------------------------------------------------
# The progress feed's vocabulary. Labels are sentence case and name what is
# happening to the filer's data, not what the code is doing.

#: Module scope to the label the feed renders. Order is run order.
PIPELINE_STEP_LABELS: tuple[tuple[str, str], ...] = (
    ("m01", "Resolving the filer"),
    ("m02", "Reading the filing history"),
    ("m03", "Extracting reported figures"),
    ("m04", "Reading the annual report"),
    ("m05", "Fetching market data"),
    ("m07", "Computing derived metrics"),
    ("m08", "Selecting peers"),
    ("m09", "Building the developments timeline"),
    ("m06", "Storing facts with their provenance"),
    ("m10", "Writing the prose"),
    ("m11", "Verifying every figure"),
    ("m12", "Assembling the outputs"),
)

#: Counts the feed shows, per step. A step with no natural count shows none.
STEP_COUNT_LABELS: dict[str, str] = {
    "m02": "filings",
    "m03": "facts",
    "m04": "sections",
    "m05": "figures",
    "m07": "derived",
    "m08": "peers",
    "m09": "events",
    "m06": "stored",
    "m10": "sections",
    "m11": "checks",
    "m12": "artifacts",
}

# --- Artifact storage (m12) --------------------------------------------------

#: Supabase Storage bucket the rendered reports are written to. Public: a
#: trisheet is a document meant to be handed to someone, and nothing in it was
#: not already public on EDGAR.
STORAGE_BUCKET = "reports"

#: Object key within the bucket. The report id makes it unique; the ticker and
#: date make it recognisable in a downloads folder.
ARTIFACT_PATH_TEMPLATE = "{report_id}/trisheet-{ticker}-{date}.{extension}"

ARTIFACT_CONTENT_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
}

STORAGE_UPLOAD_TIMEOUT_SECONDS = 60.0

# --- Document assembly (m12) -------------------------------------------------

#: Periods shown across a figure table. Wider than this and the columns stop
#: being readable on a printed page.
MAX_TABLE_PERIODS = 5

#: Segments plotted in the mix chart. Beyond this the bands are unreadable, and
#: the remainder is not silently merged into an "other" the filer never named.
MAX_CHART_SEGMENTS = 8

#: Figures are tabulated in millions of the reporting currency. Per-share
#: amounts, ratios and percentages keep their own units.
DISPLAY_SCALE_DIVISOR = 1_000_000
DISPLAY_SCALE_NOTE = "{currency} millions, except per-share amounts and ratios"

#: Prefixes whose facts are already in their display scale and must not be
#: divided again.
UNSCALED_METRIC_PREFIXES = (
    "margin.",
    "return.",
    "growth.",
    "ratio.",
    "liquidity.",
    "leverage.",
    "dupont.",
    "working_capital.",
    "common_size.",
    "bridge.",
    "per_share.",
    "attribution.",
    "income.eps_",
    "income.dividends_per_share",
)

# --- Monitoring --------------------------------------------------------------

#: Window the metrics endpoint reports over unless a caller asks for another.
METRICS_DEFAULT_WINDOW_HOURS = 24
METRICS_MAX_WINDOW_HOURS = 24 * 30

#: Settled runs read per window. A window with more than this is sampled from
#: the most recent, and the response says so rather than implying totality.
METRICS_MAX_ROWS = 1_000

#: Percentiles published for latency. p50 is the typical run, p95 the one a
#: reader waits through.
LATENCY_PERCENTILES = (50, 95)

#: What a figure reads as when the window holds nothing to compute it from.
#: Never 0% — "no runs yet" and "nothing succeeded" are different statements.
METRIC_UNAVAILABLE_TEXT = "No data"

# --- Chat assistant (chat_agent) ---------------------------------------------
# A tool-calling assistant that answers a question about a completed report,
# restricted to two source tiers: facts already stored for the report (tier
# 1), and metrics derivable from data already fetched from EDGAR for the same
# filer but not emitted in the original report (tier 2). Company websites,
# general web search and valuation assumptions are separate, deliberately
# deferred phases — no tool here reaches either.

#: A chat message longer than this is not a question about a report.
CHAT_MESSAGE_MAX_CHARS = 2_000

#: Tool-call round trips the loop may make in one turn before it gives up and
#: answers not-found. Loop safety, independent of any cross-request rate
#: limit, which is out of scope for this pass.
CHAT_MAX_TOOL_CALLS_PER_TURN = 4

#: Token overlap between a question and a fact's metric path or label
#: required before the cheap pre-check answers directly from the fact store,
#: skipping the model loop entirely for the common case.
CHAT_FACT_MATCH_MIN_OVERLAP = 2

#: Facts described to the model in one tool result. A tool call that matches
#: far more than this is not a targeted lookup, and the rest would only spend
#: context without helping the model answer.
CHAT_TOOL_RESULT_FACT_LIMIT = 20

# --- DCF assumption defaults --------------------------------------------------
# Fixed, clearly-labelled illustrative values — never derived from this
# filer's market data, beta or peers. Deriving a discount rate from market
# inputs would be the system forming a view, the same reasoning m07's REIT
# NAV comment gives for refusing to invent a capitalisation rate. A caller may
# override any of these with its own supplied assumption; what never happens
# is the system picking one on its own from data that could make it look
# sourced.

#: Illustrative discount rate (a WACC proxy), applied when the caller supplies
#: none.
DCF_DEFAULT_DISCOUNT_RATE = 0.09

#: Illustrative annual growth rate applied to the base free cash flow over the
#: projection window, when the caller supplies none.
DCF_DEFAULT_FCF_GROWTH_RATE = 0.03

#: Illustrative perpetual growth rate used in the Gordon-growth terminal
#: value, when the caller supplies none. Must be below the discount rate or
#: the terminal value is not a finite number.
DCF_DEFAULT_TERMINAL_GROWTH_RATE = 0.02

#: Years of free cash flow explicitly projected before the terminal value
#: takes over, when the caller supplies none.
DCF_DEFAULT_PROJECTION_YEARS = 5

# --- Company-website fetch (webfetch) -----------------------------------------
# The one place besides edgar.py (sec.gov), m05_market.py (the configured
# market provider), llm.py (Anthropic) and db.py (Supabase) that opens an
# outbound connection — and the only one of those five aimed at a URL a user
# supplied rather than a fixed, known host. Every setting below exists to
# bound what that can reach.

#: Redirect hops followed before refusing. Each hop's target is checked
#: against the same host guard as the original URL — a redirect is not an
#: escape hatch.
WEBFETCH_MAX_REDIRECTS = 3

#: A response larger than this is abandoned mid-stream, not truncated and
#: used anyway — a page this large is not something to hand a model whole.
WEBFETCH_MAX_RESPONSE_BYTES = 2_000_000

#: Short on purpose: this is a user-facing chat turn, not a background job.
WEBFETCH_TIMEOUT_SECONDS = 8.0

#: Characters of a fetched page's text shown to the model. A page can be
#: arbitrarily long; only its leading portion enters the prompt.
CHAT_WEBFETCH_MAX_PAGE_CHARS = 12_000

# --- Chat rate limiting --------------------------------------------------------
# The chat endpoint has no auth of any kind (see main.py), and every turn costs
# a paid model call — a company-site fetch or a web search turn more so.
# Enforced by counting this report's own recent chat_messages rows rather than
# a separate store, so the limit is visible in the same table that already
# holds the history it is counting.

#: Assistant turns permitted for one report within the rolling window below.
#: Deliberately generous for ordinary use, tight enough to bound a script.
CHAT_RATE_LIMIT_MAX_TURNS = 50

#: The rolling window `CHAT_RATE_LIMIT_MAX_TURNS` applies over.
CHAT_RATE_LIMIT_WINDOW_MINUTES = 10

# --- General web search (tier 4, last resort) ---------------------------------
# Reached only after tiers 1-3 (own facts, wider derivation, company site)
# have all come up empty. Gated behind its own flag, separate from
# `is_configured`, so it can be switched off in an environment even where the
# model itself is otherwise configured — the rate limit above is what this
# flag is deliberately not turned on without.

#: Off by default. A deployment turns this on only once `CHAT_RATE_LIMIT_*`
#: is known to be enforced (a configured database — see `is_rate_limited`),
#: since this is the one tier that spends money on a source outside this
#: system's own data.
CHAT_WEB_SEARCH_ENABLED = False

#: Server-side searches Anthropic's hosted tool may run for one chat turn.
LLM_WEB_SEARCH_MAX_USES = 3


class Settings(BaseSettings):
    """Environment-backed settings. Instantiated once via `get_settings`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    port: int = 8000

    #: Sent to SEC in every User-Agent. Required — SEC blocks anonymous traffic.
    edgar_contact_email: str = Field(default="", description="Contact for SEC")

    anthropic_api_key: SecretStr = SecretStr("")

    supabase_url: str = ""
    supabase_service_role_key: SecretStr = SecretStr("")

    #: Comma-separated. Kept as a string so that pydantic-settings does not
    #: attempt to JSON-decode it, which is a common source of startup failure.
    cors_allowed_origins: str = "http://localhost:3000"

    @property
    def cors_origins(self) -> list[str]:
        """Allowed browser origins, parsed from the comma-separated setting."""
        return [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]

    @property
    def edgar_user_agent(self) -> str:
        """User-Agent header sent on every sec.gov and data.sec.gov request."""
        return EDGAR_USER_AGENT_TEMPLATE.format(
            contact_email=self.edgar_contact_email
        )

    @property
    def edgar_configured(self) -> bool:
        """EDGAR is the only hard dependency; startup checks this."""
        return bool(self.edgar_contact_email)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Returns the process-wide settings instance."""
    return Settings()
