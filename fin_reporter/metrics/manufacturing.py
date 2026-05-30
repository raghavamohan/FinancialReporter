"""Manufacturing (Ind-AS) financial metric calculator.

Extracts quarterly metrics for non-banking companies reporting under
Indian Accounting Standards (Ind-AS). Metrics include Revenue from
Operations, EBITDA, PBIT (EBIT), PBT, Net Income, and EPS.

EBITDA is computed using a selectable definition:
    - tickertape: PBT (before exceptionals) + Finance Costs + Depreciation
    - subtract-other-income: previous formula that subtracts Other Income
"""

import datetime as dt

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
)
from fin_reporter.models import FinancialMetrics
from fin_reporter.period_resolver import (
    pick_value_for_plan,
    resolve_period_context_plan,
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


def build_manufacturing_metrics(
    facts: dict,
    target_period: str,
    ebitda_definition: str = _DEFAULT_EBITDA_DEFINITION,
) -> FinancialMetrics:
    """Build financial metrics for a manufacturing (Ind-AS) company.

    Args:
        facts: Parsed XBRL facts dict from ``extract_facts``.
        target_period: Target period date string (e.g. "31-Mar-2026").
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

    # Populate warnings for missing key metrics
    if revenue is None:
        warnings.append("Revenue not found")
    if ebitda is None:
        warnings.append("EBITDA could not be computed")
    if pbt is None:
        warnings.append("PBT not found")
    if net_income is None:
        warnings.append("Net Income not found")

    return FinancialMetrics(
        company_type="manufacturing",
        revenue=revenue,
        ebitda=ebitda,
        pbit=pbit,
        pbt=pbt,
        net_income=net_income,
        basic_eps=basic_eps,
        diluted_eps=diluted_eps,
        warnings=warnings,
    )
