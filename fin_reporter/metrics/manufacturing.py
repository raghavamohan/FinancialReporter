"""Manufacturing (Ind-AS) financial metric calculator.

Extracts quarterly metrics for non-banking companies reporting under
Indian Accounting Standards (Ind-AS). Metrics include Revenue from
Operations, EBITDA, PBIT (EBIT), PBT, Net Income, and EPS.

EBITDA is computed using a selectable definition:
    - tickertape: PBT (before exceptionals) + Finance Costs + Depreciation
    - subtract-other-income: previous formula that subtracts Other Income
"""

import datetime as dt
import os

from fin_reporter.constants import (
    MANUFACTURING_BASIC_EPS_TAGS,
    MANUFACTURING_DEPRECIATION_TAGS,
    MANUFACTURING_DILUTED_EPS_TAGS,
    MANUFACTURING_FINANCE_COST_TAGS,
    MANUFACTURING_NET_INCOME_TAGS,
    MANUFACTURING_PBIT_TAGS,
    MANUFACTURING_PBT_TAGS,
    MANUFACTURING_REVENUE_TAGS,
    OTHER_INCOME_TAGS,
    MANUFACTURING_COST_OF_MATERIALS_TAGS,
    MANUFACTURING_PURCHASES_STOCK_TAGS,
    MANUFACTURING_CHANGES_INVENTORIES_TAGS,
    MANUFACTURING_EQUITY_TAGS,
    MANUFACTURING_CURRENT_LIABILITIES_TAGS,
    MANUFACTURING_TOTAL_ASSETS_TAGS,
)
from fin_reporter.models import FinancialMetrics
from fin_reporter.period_resolver import (
    pick_value_for_plan,
    resolve_period_context_plan,
    quarter_code_from_file_path,
    previous_quarter_code,
    find_symbol_quarter_file,
    previous_quarter_end,
)
from fin_reporter.xbrl_parser import (
    extract_facts,
    pick_numeric,
    select_entries,
)


_NAMESPACE_MODE = "ind-as"
_DEFAULT_EBITDA_DEFINITION = "tickertape"
_SUPPORTED_EBITDA_DEFINITIONS = ("tickertape", "subtract-other-income")

# For EBITDA calculation in "subtract-other-income" mode, prefer PBT *before*
# exceptional items so that one-off charges don't distort the operating
# performance measure.
# The standard PBT row (for display) continues to use MANUFACTURING_PBT_TAGS
# which shows PBT after exceptional items.
_EBITDA_PBT_TAGS = (
    "ProfitBeforeExceptionalItemsAndTax",
    "ProfitBeforeTax",
    "ProfitBeforeTaxFromContinuingOperations",
)
_SEGMENT_EBITDA_PBT_TAGS = (
    "SegmentProfitBeforeTax",
)
_SEGMENT_EBITDA_FINANCE_COST_TAGS = (
    "SegmentFinanceCosts",
)


def _calculate_ebitda(
    facts: dict,
    plan: dict | None,
    finance_cost: float | None,
    ebitda_definition: str,
) -> float | None:
    """Compute EBITDA from PBT (pre-exceptional), finance costs, and depreciation.

    Definitions:
        - tickertape:
            EBITDA = SegmentProfitBeforeTax + SegmentFinanceCosts + Depreciation
            Falls back to PBT/Finance Costs tags if segment tags are absent.
        - subtract-other-income:
            EBITDA = PBT (before exceptionals) + Finance Cost
                     + Depreciation − Other Income

    Uses PBT *before* exceptional items to isolate operating performance.

    Returns None if any required component (PBT, Finance Cost, Depreciation)
    is missing. Other Income defaults to 0 if absent when needed.
    """
    depreciation = pick_value_for_plan(
        facts,
        MANUFACTURING_DEPRECIATION_TAGS,
        plan,
        _NAMESPACE_MODE,
    )
    if depreciation is None:
        return None

    if ebitda_definition == "tickertape":
        pbt_for_ebitda = pick_value_for_plan(
            facts,
            _SEGMENT_EBITDA_PBT_TAGS,
            plan,
            _NAMESPACE_MODE,
        )
        finance_for_ebitda = pick_value_for_plan(
            facts,
            _SEGMENT_EBITDA_FINANCE_COST_TAGS,
            plan,
            _NAMESPACE_MODE,
        )
        if pbt_for_ebitda is None:
            pbt_for_ebitda = pick_value_for_plan(
                facts,
                _EBITDA_PBT_TAGS,
                plan,
                _NAMESPACE_MODE,
            )
        if finance_for_ebitda is None:
            finance_for_ebitda = finance_cost
        if pbt_for_ebitda is not None and finance_for_ebitda is not None:
            return pbt_for_ebitda + finance_for_ebitda + depreciation
        return None

    # subtract-other-income mode
    pbt_for_ebitda = pick_value_for_plan(
        facts,
        _EBITDA_PBT_TAGS,
        plan,
        _NAMESPACE_MODE,
    )
    if pbt_for_ebitda is not None and finance_cost is not None:
        other_income = pick_value_for_plan(
            facts,
            OTHER_INCOME_TAGS,
            plan,
            _NAMESPACE_MODE,
        ) or 0.0
        return pbt_for_ebitda + finance_cost + depreciation - other_income

    return None


def _pick_instant_value(
    facts: dict,
    tags: tuple[str, ...],
    target_date: dt.date,
    namespace_mode: str,
) -> float | None:
    """Extract a numeric value for an instant balance sheet tag."""
    entries = select_entries(
        facts,
        tags,
        namespace_mode=namespace_mode,
        instant_date=target_date,
        no_dimensions=True,
    )
    if not entries:
        entries = select_entries(
            facts,
            tags,
            namespace_mode=namespace_mode,
            end_date=target_date,
            no_dimensions=True,
        )
    value, _entry = pick_numeric(entries)
    return value


def _extract_balance_sheet_with_fallback(
    facts: dict,
    file_path: str,
    target_end_date: dt.date,
    namespace_mode: str,
) -> tuple[float | None, float | None, float | None]:
    """Extract Total Assets, Equity, and Current Liabilities with prior quarter fallback."""
    # 1. Try extracting from current facts
    assets = _pick_instant_value(facts, MANUFACTURING_TOTAL_ASSETS_TAGS, target_end_date, namespace_mode)
    equity = _pick_instant_value(facts, MANUFACTURING_EQUITY_TAGS, target_end_date, namespace_mode)
    curr_liab = _pick_instant_value(facts, MANUFACTURING_CURRENT_LIABILITIES_TAGS, target_end_date, namespace_mode)

    if assets is not None and equity is not None:
        return assets, equity, curr_liab

    # 2. Fallback: Search prior quarters' files in the same directory (up to 3 quarters back)
    quarter_code = quarter_code_from_file_path(file_path) if file_path else None
    current_code = quarter_code
    current_date = target_end_date

    for _ in range(3):
        prev_code = previous_quarter_code(current_code) if current_code else None
        prev_file = find_symbol_quarter_file(file_path, prev_code) if prev_code and file_path else None
        prev_end_date = previous_quarter_end(current_date)
        if not prev_end_date:
            break

        current_code = prev_code
        current_date = prev_end_date

        if prev_file and os.path.exists(prev_file):
            prev_facts, _ = extract_facts(prev_file)
            if prev_facts:
                prev_assets = _pick_instant_value(prev_facts, MANUFACTURING_TOTAL_ASSETS_TAGS, prev_end_date, namespace_mode)
                prev_equity = _pick_instant_value(prev_facts, MANUFACTURING_EQUITY_TAGS, prev_end_date, namespace_mode)
                prev_curr_liab = _pick_instant_value(prev_facts, MANUFACTURING_CURRENT_LIABILITIES_TAGS, prev_end_date, namespace_mode)

                # If we found at least assets and equity, use them
                if prev_assets is not None and prev_equity is not None:
                    return prev_assets, prev_equity, prev_curr_liab

    return assets, equity, curr_liab


def build_manufacturing_metrics(
    facts: dict,
    target_period: str,
    file_path: str = "",
    ebitda_definition: str = _DEFAULT_EBITDA_DEFINITION,
) -> FinancialMetrics:
    """Build financial metrics for a manufacturing (Ind-AS) company.

    Args:
        facts: Parsed XBRL facts dict from ``extract_facts``.
        target_period: Target period date string (e.g. "31-Mar-2026").
        file_path: Optional path to current XBRL file for prior-quarter fallbacks.
        ebitda_definition: EBITDA formula mode.

    Returns:
        FinancialMetrics with manufacturing-relevant fields populated.
    """
    target_end_date = dt.datetime.strptime(target_period, "%d-%b-%Y").date()
    warnings: list[str] = []
    ebitda_mode = (
        ebitda_definition
        if ebitda_definition in _SUPPORTED_EBITDA_DEFINITIONS
        else _DEFAULT_EBITDA_DEFINITION
    )
    if ebitda_mode != ebitda_definition:
        warnings.append(
            "Unsupported EBITDA definition; using tickertape"
        )

    # Resolve the period context plan using Revenue as the probe tag.
    plan = resolve_period_context_plan(
        facts,
        MANUFACTURING_REVENUE_TAGS,
        _NAMESPACE_MODE,
        target_end_date,
    )

    revenue = pick_value_for_plan(
        facts,
        MANUFACTURING_REVENUE_TAGS,
        plan,
        _NAMESPACE_MODE,
    )

    pbt = pick_value_for_plan(
        facts,
        MANUFACTURING_PBT_TAGS,
        plan,
        _NAMESPACE_MODE,
    )

    finance_cost = pick_value_for_plan(
        facts,
        MANUFACTURING_FINANCE_COST_TAGS,
        plan,
        _NAMESPACE_MODE,
    )

    ebitda = _calculate_ebitda(
        facts,
        plan,
        finance_cost,
        ebitda_mode,
    )

    # PBIT = PBT + Finance Cost (computed), fallback to direct XBRL tag
    pbit = None
    if pbt is not None and finance_cost is not None:
        pbit = pbt + finance_cost
    if pbit is None:
        pbit = pick_value_for_plan(
            facts,
            MANUFACTURING_PBIT_TAGS,
            plan,
            _NAMESPACE_MODE,
        )

    net_income = pick_value_for_plan(
        facts,
        MANUFACTURING_NET_INCOME_TAGS,
        plan,
        _NAMESPACE_MODE,
    )

    basic_eps = pick_value_for_plan(
        facts,
        MANUFACTURING_BASIC_EPS_TAGS,
        plan,
        _NAMESPACE_MODE,
    )

    diluted_eps = pick_value_for_plan(
        facts,
        MANUFACTURING_DILUTED_EPS_TAGS,
        plan,
        _NAMESPACE_MODE,
    )

    # ── COGS & Gross Profit/Margin ──────────────────────────────────
    gross_profit = None
    gross_margin = None
    mat_consumed = pick_value_for_plan(facts, MANUFACTURING_COST_OF_MATERIALS_TAGS, plan, _NAMESPACE_MODE)
    purchases = pick_value_for_plan(facts, MANUFACTURING_PURCHASES_STOCK_TAGS, plan, _NAMESPACE_MODE)
    changes_inv = pick_value_for_plan(facts, MANUFACTURING_CHANGES_INVENTORIES_TAGS, plan, _NAMESPACE_MODE)

    if revenue is not None:
        cogs = 0.0
        has_cogs = False
        if mat_consumed is not None:
            cogs += mat_consumed
            has_cogs = True
        if purchases is not None:
            cogs += purchases
            has_cogs = True
        if changes_inv is not None:
            cogs += changes_inv
            has_cogs = True

        if has_cogs:
            gross_profit = revenue - cogs
            if revenue > 0:
                gross_margin = (gross_profit / revenue) * 100

    # ── EBITDA & Net Profit Margins ─────────────────────────────────
    ebitda_margin = None
    if ebitda is not None and revenue is not None and revenue > 0:
        ebitda_margin = (ebitda / revenue) * 100

    net_margin = None
    if net_income is not None and revenue is not None and revenue > 0:
        net_margin = (net_income / revenue) * 100

    # ── Balance Sheet & Return Metrics (ROE, ROCE, ROA) ─────────────
    assets = None
    equity = None
    curr_liab = None

    if file_path:
        assets, equity, curr_liab = _extract_balance_sheet_with_fallback(
            facts, file_path, target_end_date, _NAMESPACE_MODE
        )
    else:
        assets = _pick_instant_value(facts, MANUFACTURING_TOTAL_ASSETS_TAGS, target_end_date, _NAMESPACE_MODE)
        equity = _pick_instant_value(facts, MANUFACTURING_EQUITY_TAGS, target_end_date, _NAMESPACE_MODE)
        curr_liab = _pick_instant_value(facts, MANUFACTURING_CURRENT_LIABILITIES_TAGS, target_end_date, _NAMESPACE_MODE)

    # Annualization factor for balance sheet ratios: 4 for quarters, 1 for full years
    annualization = 4
    plan_duration = plan.get("duration_days") if plan else None
    if plan_duration and plan_duration > 300:
        annualization = 1

    roa = None
    if net_income is not None and assets is not None and assets > 0:
        roa = (net_income / assets) * annualization * 100

    roe = None
    if net_income is not None and equity is not None and equity > 0:
        roe = (net_income / equity) * annualization * 100

    roce = None
    if pbit is not None and assets is not None and assets > 0:
        cl = curr_liab or 0.0
        capital_employed = assets - cl
        if capital_employed > 0:
            roce = (pbit / capital_employed) * annualization * 100

    # Populate warnings for missing key metrics
    if revenue is None:
        warnings.append("Revenue not found")
    if ebitda is None:
        warnings.append("EBITDA could not be computed")
    if pbt is None:
        warnings.append("PBT not found")
    if net_income is None:
        warnings.append("Net Income not found")
    if assets is None:
        warnings.append("Balance Sheet assets not found (metrics like ROE/ROCE will be missing)")

    return FinancialMetrics(
        company_type="manufacturing",
        revenue=revenue,
        ebitda=ebitda,
        pbit=pbit,
        pbt=pbt,
        net_income=net_income,
        basic_eps=basic_eps,
        diluted_eps=diluted_eps,
        gross_profit=gross_profit,
        gross_margin=gross_margin,
        ebitda_margin=ebitda_margin,
        net_margin=net_margin,
        roe=roe,
        roce=roce,
        roa=roa,
        equity=equity,
        warnings=warnings,
    )
