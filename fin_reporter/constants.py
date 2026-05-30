"""
XBRL Tag Mappings for Indian Financial Reporting.

Manufacturing companies report under Ind-AS (namespace tokens: ind-as, in-capmkt).
Banks report under IN-GAAP (namespace tokens: in-gaap, in-capmkt, cmbnk).

Each tag tuple is ordered by preference — the first matching tag in the XBRL wins.
Tags are searched in order; once a value is found, remaining tags are skipped.
"""

# ─── Scaling ─────────────────────────────────────────────────────────────────
# XBRL values are in absolute rupees. Divide by this to get crores.
CRORE_DIVISOR = 10_000_000


# ═════════════════════════════════════════════════════════════════════════════
# MANUFACTURING COMPANIES (Ind-AS)
# ═════════════════════════════════════════════════════════════════════════════

# REVENUE FROM OPERATIONS
#   Source: Statement of Profit & Loss → Revenue from Operations
#   Includes: Sale of products, services, other operating revenue
#   Excludes: Other income (interest, dividends, gains)
MANUFACTURING_REVENUE_TAGS = (
    "RevenueFromOperations",
)

# PROFIT BEFORE TAX (PBT)
#   Source: P&L → Profit before tax
#   ProfitBeforeExceptionalItemsAndTax used when exceptional items are
#   reported separately and ProfitBeforeTax is not present.
MANUFACTURING_PBT_TAGS = (
    "ProfitBeforeTax",
    "ProfitBeforeExceptionalItemsAndTax",
    "ProfitBeforeTaxFromContinuingOperations",
)

# FINANCE COSTS
#   Source: P&L → Finance Costs
#   Includes: Interest on borrowings, other finance charges
#   Under Ind-AS 116, includes interest on lease liabilities
MANUFACTURING_FINANCE_COST_TAGS = (
    "FinanceCosts",
    "FinanceCost",
)

# DEPRECIATION AND AMORTISATION
#   Source: P&L → Depreciation and amortisation expense
#   Includes: Depreciation on tangible assets, amortisation of intangibles,
#   impairment losses. Under Ind-AS 116, includes RoU asset depreciation.
MANUFACTURING_DEPRECIATION_TAGS = (
    "DepreciationDepletionAndAmortisationExpense",
    "DepreciationAndAmortisationExpense",
    "DepreciationAmortisationAndImpairmentExpense",
    "DepreciationExpense",
    "Depreciation",
)

# OTHER INCOME
#   Source: P&L → Other Income
#   Includes: Interest income, dividend income, gains on disposal of assets,
#   fair value gains, miscellaneous income
OTHER_INCOME_TAGS = (
    "OtherIncome",
    "OtherNonOperatingIncome",
    "NonOperatingIncome",
    "OtherIncomeExcludingInterestIncome",
)

# PBIT / EBIT (Profit Before Interest and Tax)
#   Source: Computed as PBT + Finance Costs, or direct XBRL tag
MANUFACTURING_PBIT_TAGS = (
    "ProfitBeforeTaxAndFinanceCosts",
    "ProfitBeforeFinanceCostsAndTax",
)

# NET INCOME (Profit attributable to owners of parent)
#   Prefer continuing-operations profit first so discontinued-ops gains
#   (e.g. demergers) do not inflate quarterly net income.
#   Owner-attributable tags follow for consolidated minority handling.
MANUFACTURING_NET_INCOME_TAGS = (
    "ProfitLossForPeriodFromContinuingOperations",
    "ProfitLossAttributableToOwnersOfParent",
    "ProfitOrLossAttributableToOwnersOfParent",
    "ProfitLossForPeriod",
)

# BASIC EPS
#   Source: P&L → Earnings per equity share → Basic
#   Reports earnings per share before dilution effects
MANUFACTURING_BASIC_EPS_TAGS = (
    "BasicEarningsLossPerShareFromContinuingOperations",
    "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
    "BasicEarningsLossPerShare",
    "BasicEarningsPerShareFromContinuingOperations",
)

# DILUTED EPS
#   Source: P&L → Earnings per equity share → Diluted
#   Includes dilutive potential equity shares (stock options, convertibles)
MANUFACTURING_DILUTED_EPS_TAGS = (
    "DilutedEarningsLossPerShareFromContinuingOperations",
    "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
    "DilutedEarningsLossPerShare",
)


# ═════════════════════════════════════════════════════════════════════════════
# BANKING COMPANIES (IN-GAAP / RBI Format)
# ═════════════════════════════════════════════════════════════════════════════

# INTEREST EARNED
#   Source: Banking P&L → Income → Interest Earned
#   Includes: Interest/discount on advances/bills, income on investments,
#   interest on balances with RBI and inter-bank funds, others
BANK_INTEREST_EARNED_TAGS = (
    "InterestEarned",
    "InterestEarnedOnLoans",
)

# INTEREST EXPENDED
#   Source: Banking P&L → Expenditure → Interest Expended
#   Includes: Interest on deposits, interest on RBI/inter-bank borrowings,
#   others
BANK_INTEREST_EXPENDED_TAGS = (
    "InterestExpended",
    "InterestExpenses",
)

# OPERATING PROFIT BEFORE PROVISIONS (PPOP)
#   Source: Banking P&L → Operating Profit
#   Formula: NII + Other Income − Operating Expenses
#   This is the banking equivalent of EBITDA. EBITDA is NOT applicable
#   to banks because interest is the core business, not a financing cost.
BANK_OPERATING_PROFIT_TAGS = (
    "OperatingProfitBeforeProvisionsAndContingencies",
    "OperatingProfitBeforeProvisionAndContingencies",
    "ProfitBeforeProvisions",
)

# PBT (Profit Before Tax) for banks
#   Source: Banking P&L → Profit/Loss from ordinary activities before tax
#   This is after provisions and contingencies have been deducted from PPOP
BANK_PBT_TAGS = (
    "ProfitLossFromOrdinaryActivitiesBeforeTax",
    "ProfitBeforeExtraordinaryItemsAndTax",
    "ProfitBeforeTax",
    "SegmentProfitBeforeTax",
)

# NET PROFIT (attributable to owners)
#   Source: Banking P&L → Net Profit/Loss after tax and minority interest
BANK_NET_PROFIT_TAGS = (
    "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates",
    "ProfitOrLossAttributableToOwnersOfParent",
    "ProfitLossAttributableToOwnersOfParent",
    "NetProfitAttributableToOwners",
    # Fallback for standalone filings
    "ProfitLossFromOrdinaryActivitiesAfterTax",
    "ProfitLossForThePeriod",
)

# BASIC EPS for banks
#   Banks under IN-GAAP use "before/after extraordinary items" terminology
BANK_BASIC_EPS_TAGS = (
    "BasicEarningsPerShareAfterExtraordinaryItems",
    "BasicEarningsPerShareBeforeExtraordinaryItems",
    "BasicEarningsPerShareFromContinuingOperations",
    "BasicEarningsPerShare",
    "BasicEarningsLossPerShareFromContinuingOperations",
    "BasicEarningsLossPerShare",
)

# DILUTED EPS for banks
BANK_DILUTED_EPS_TAGS = (
    "DilutedEarningsPerShareAfterExtraordinaryItems",
    "DilutedEarningsPerShareBeforeExtraordinaryItems",
    "DilutedEarningsPerShareFromContinuingOperations",
    "DilutedEarningsLossPerShare",
)

# TOTAL ASSETS (balance sheet, instant context)
#   Source: Balance Sheet → Total Assets
#   Used for ROA calculation
BANK_TOTAL_ASSETS_TAGS = (
    "Assets",
    "TotalAssets",
    # Legacy banking filings may expose total balance-sheet size via
    # segment-aggregated tags instead of plain Assets/TotalAssets.
    "NetSegmentAssets",
    "SegmentAssets",
)

# RETURN ON ASSETS (may be directly reported)
#   Source: Key ratios disclosed in quarterly results
#   If available, prefer the XBRL-reported value over computed
BANK_ROA_TAGS = (
    "ReturnOnAssets",
)

# GROSS NPA %
#   Source: Asset quality disclosures
BANK_GNPA_TAGS = (
    "PercentageOfGrossNpa",
)

# NET NPA %
#   Source: Asset quality disclosures
BANK_NNPA_TAGS = (
    "PercentageOfNpa",
)


# ═════════════════════════════════════════════════════════════════════════════
# XBRL CONTEXT IDENTIFICATION
# ═════════════════════════════════════════════════════════════════════════════

# Axes and members used to identify consolidated contexts in XBRL
CONSOLIDATED_AXES = (
    "ComponentsOfFinancialStatementsAxis",
    "TypesOfFinancialStatementsAxis",
    "ConsolidatedAndStandaloneFinancialStatementsAxis",
)
CONSOLIDATED_MEMBERS = (
    "ConsolidatedMember",
    "ConsolidatedFinancialStatementsMember",
)

# Namespace token matching — XBRL namespace URIs contain these tokens
# to identify the reporting taxonomy.
# Example: "http://www.mca.gov.in/xbrl/ind-as/..." contains "ind-as"
# Example: "http://www.sebi.gov.in/xbrl/2026-01-31/in-capmkt" contains "in-capmkt"
CANONICAL_NAMESPACE_TOKENS = {
    "ind-as": ("ind-as", "in-bse-fin", "in-capmkt"),
    "in-gaap": ("in-gaap", "in-bse-fin", "in-capmkt", "cmbnk"),
    "either": ("ind-as", "in-gaap", "in-bse-fin", "in-capmkt"),
}

# Tags used to detect filing nature from XBRL content
FILING_NATURE_TAG = "NatureOfReportStandaloneConsolidated"
COMPANY_NAME_TAGS = ("NameOfTheCompany", "NameOfBank")
