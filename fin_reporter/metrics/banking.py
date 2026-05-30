"""Banking (IN-GAAP / RBI format) financial metric calculator.

Extracts quarterly metrics for banking companies reporting under IN-GAAP.
Banks have a fundamentally different P&L structure from manufacturing companies:
interest income/expense IS the core business, so EBITDA is not applicable.
Instead, we compute NII, PPOP, and ROA.

Q4 normalization:
    Q4 filings often contain only full-year (FY) cumulative figures. When the
    previous quarter's XBRL file is available in the same directory, we compute
    Q4 = FY_cumulative − Q3_YTD_cumulative for each metric.
"""

import datetime as dt
import os

from fin_reporter.constants import (
    BANK_BASIC_EPS_TAGS,
    BANK_DILUTED_EPS_TAGS,
    BANK_GNPA_TAGS,
    BANK_INTEREST_EARNED_TAGS,
    BANK_INTEREST_EXPENDED_TAGS,
    BANK_NET_PROFIT_TAGS,
    BANK_NNPA_TAGS,
    BANK_OPERATING_PROFIT_TAGS,
    BANK_PBT_TAGS,
    BANK_ROA_TAGS,
    BANK_TOTAL_ASSETS_TAGS,
    OTHER_INCOME_TAGS,
)
from fin_reporter.metrics.base import should_apply_q4_delta
from fin_reporter.models import FinancialMetrics
from fin_reporter.period_resolver import (
    find_symbol_quarter_file,
    pick_cumulative_value,
    pick_value_for_plan,
    previous_quarter_code,
    previous_quarter_end,
    quarter_code_from_file_path,
    resolve_period_context_plan,
)
from fin_reporter.xbrl_parser import (
    extract_facts,
    pick_numeric,
    select_entries,
)

_NAMESPACE_MODE = "in-gaap"


def _normalize_ratio_to_percentage(value: float | None) -> float | None:
    """Normalize a ratio-like value to percentage if needed.

    Some filings report ratios as decimals (e.g. 0.0115 for 1.15%).
    Keep values > 1 unchanged (already percentage-like).
    """
    if value is None:
        return None
    return value * 100 if abs(value) <= 1 else value


def _normalize_roa_to_percentage(
    value: float | None,
    duration_days: int | None,
) -> float | None:
    """Normalize ROA to annualized percentage.

    Decimal ROA values are converted to percentages. For quarter contexts
    (roughly 3-month durations), values are annualized by multiplying by 4.
    """
    if value is None:
        return None
    if abs(value) > 1:
        return value

    annualization = 4 if duration_days and duration_days <= 120 else 1
    return value * 100 * annualization


def _pick_reported_roa(
    facts: dict,
    target_end_date: dt.date,
    context_ref: str | None = None,
) -> tuple[float | None, int | None]:
    """Pick a reported ROA value and its context duration."""
    roa_entries = select_entries(
        facts,
        BANK_ROA_TAGS,
        namespace_mode=_NAMESPACE_MODE,
        context_ref=context_ref,
        end_date=None if context_ref else target_end_date,
        no_dimensions=True,
    )
    reported_roa, roa_entry = pick_numeric(roa_entries, target_end_date)
    duration = roa_entry.get("duration_days") if roa_entry else None
    return reported_roa, duration


def _find_standalone_variant_file(file_path: str) -> str | None:
    """Find standalone filing variant for the same symbol and quarter."""
    quarter_code = quarter_code_from_file_path(file_path)
    if not quarter_code:
        return None
    basename = os.path.basename(file_path)
    parts = basename.split("_", 1)
    if len(parts) < 2:
        return None
    symbol = parts[0]
    directory = os.path.dirname(file_path)
    for ext in (".xml", ".xbrl", ".zip"):
        candidate = os.path.join(
            directory,
            f"{symbol}_{quarter_code}_STANDALONE{ext}",
        )
        if os.path.exists(candidate):
            return candidate
    return None


def _pick_npa_from_facts(
    facts: dict,
    plan: dict | None,
    target_end_date: dt.date,
) -> tuple[float | None, float | None]:
    """Extract GNPA and NNPA from one facts dictionary."""
    gnpa = pick_value_for_plan(facts, BANK_GNPA_TAGS, plan, _NAMESPACE_MODE)
    nnpa = pick_value_for_plan(facts, BANK_NNPA_TAGS, plan, _NAMESPACE_MODE)

    if gnpa is None:
        gnpa_entries = select_entries(
            facts,
            BANK_GNPA_TAGS,
            namespace_mode=_NAMESPACE_MODE,
            end_date=target_end_date,
            no_dimensions=True,
        )
        gnpa, _ = pick_numeric(gnpa_entries, target_end_date)
    if gnpa is None:
        gnpa_entries = select_entries(
            facts,
            BANK_GNPA_TAGS,
            namespace_mode=_NAMESPACE_MODE,
            instant_date=target_end_date,
            no_dimensions=True,
        )
        gnpa, _ = pick_numeric(gnpa_entries)
    if nnpa is None:
        nnpa_entries = select_entries(
            facts,
            BANK_NNPA_TAGS,
            namespace_mode=_NAMESPACE_MODE,
            end_date=target_end_date,
            no_dimensions=True,
        )
        nnpa, _ = pick_numeric(nnpa_entries, target_end_date)
    if nnpa is None:
        nnpa_entries = select_entries(
            facts,
            BANK_NNPA_TAGS,
            namespace_mode=_NAMESPACE_MODE,
            instant_date=target_end_date,
            no_dimensions=True,
        )
        nnpa, _ = pick_numeric(nnpa_entries)

    return gnpa, nnpa


def _extract_bank_roa(
    facts: dict,
    plan: dict | None,
    target_end_date: dt.date,
    net_income: float | None,
    file_path: str,
) -> float | None:
    """Extract or compute Return on Assets for a bank.

    Preference order:
        1. Directly reported ROA tag (via plan context)
        2. Directly reported ROA tag (via end-date, no dimensions)
        3. Computed: (NetIncome / TotalAssets) × annualization × 100
    """
    reported_roa: float | None = None
    reported_duration: int | None = None

    # Use plan context only for direct quarter contexts.
    if plan and plan.get("mode") == "single":
        reported_roa, reported_duration = _pick_reported_roa(
            facts,
            target_end_date,
            context_ref=plan.get("context_ref"),
        )

    # Fallback: reported ROA from end-date contexts.
    if reported_roa in (None, 0):
        reported_roa, reported_duration = _pick_reported_roa(
            facts,
            target_end_date,
        )

    # If consolidated ROA is missing/zero, try standalone variant.
    if reported_roa in (None, 0):
        standalone_file = _find_standalone_variant_file(file_path)
        if standalone_file:
            standalone_facts, _contexts = extract_facts(standalone_file)
            if standalone_facts:
                standalone_roa, standalone_duration = _pick_reported_roa(
                    standalone_facts,
                    target_end_date,
                )
                if standalone_roa not in (None, 0):
                    reported_roa = standalone_roa
                    reported_duration = standalone_duration

    if reported_roa not in (None, 0):
        return _normalize_roa_to_percentage(reported_roa, reported_duration)

    # Compute ROA from Net Income and Total Assets
    total_asset_entries = select_entries(
        facts,
        BANK_TOTAL_ASSETS_TAGS,
        namespace_mode=_NAMESPACE_MODE,
        instant_date=target_end_date,
        no_dimensions=True,
    )
    if not total_asset_entries:
        total_asset_entries = select_entries(
            facts,
            BANK_TOTAL_ASSETS_TAGS,
            namespace_mode=_NAMESPACE_MODE,
            end_date=target_end_date,
            no_dimensions=True,
        )
    total_assets, _asset_entry = pick_numeric(total_asset_entries)

    if net_income is not None and total_assets not in (None, 0):
        plan_duration = plan.get("duration_days") if plan else None
        annualization = 1 if plan_duration and plan_duration > 300 else 4
        return (net_income / total_assets) * annualization * 100

    return None


def _extract_bank_npa(
    facts: dict,
    plan: dict | None,
    target_end_date: dt.date,
    file_path: str,
) -> tuple[float | None, float | None]:
    """Extract Gross NPA % and Net NPA % with standalone fallback.

    If consolidated values are missing/zero, try standalone filing for the same
    symbol and quarter (when available locally).
    """
    gnpa, nnpa = _pick_npa_from_facts(facts, plan, target_end_date)

    needs_fallback = gnpa in (None, 0) or nnpa in (None, 0)
    if needs_fallback:
        standalone_file = _find_standalone_variant_file(file_path)
        if standalone_file:
            standalone_facts, _contexts = extract_facts(standalone_file)
            if standalone_facts:
                standalone_gnpa, standalone_nnpa = _pick_npa_from_facts(
                    standalone_facts,
                    plan=None,
                    target_end_date=target_end_date,
                )
                if gnpa in (None, 0) and standalone_gnpa not in (None, 0):
                    gnpa = standalone_gnpa
                if nnpa in (None, 0) and standalone_nnpa not in (None, 0):
                    nnpa = standalone_nnpa

    gnpa = _normalize_ratio_to_percentage(gnpa)
    nnpa = _normalize_ratio_to_percentage(nnpa)
    return gnpa, nnpa


def _apply_q4_delta_normalization(
    facts: dict,
    file_path: str,
    target_end_date: dt.date,
    interest_earned: float | None,
    other_income: float | None,
    interest_expended: float | None,
    nii: float | None,
    total_income: float | None,
    ppop: float | None,
    pbt: float | None,
    net_income: float | None,
) -> tuple[
    float | None,  # nii
    float | None,  # total_income
    float | None,  # ppop
    float | None,  # pbt
    float | None,  # net_income
]:
    """Apply Q4 delta normalization using previous quarter's XBRL file.

    For Q4 filings (ending 31-Mar), the filing often contains only FY
    cumulative figures. We isolate Q4 by computing:
        Q4_value = FY_cumulative − Q3_YTD_cumulative

    The Q3 YTD cumulative is obtained from the previous quarter's file
    (which must be present in the same directory).
    """
    # Only applies to Q4 (March 31 period end)
    if not (target_end_date.month == 3 and target_end_date.day == 31):
        return nii, total_income, ppop, pbt, net_income

    quarter_code = quarter_code_from_file_path(file_path)
    prev_code = previous_quarter_code(quarter_code) if quarter_code else None
    prev_file = find_symbol_quarter_file(file_path, prev_code) if prev_code else None
    if not prev_file:
        return nii, total_income, ppop, pbt, net_income

    prev_facts, _prev_contexts = extract_facts(prev_file)
    prev_end_date = previous_quarter_end(target_end_date)
    if not prev_end_date or not prev_facts:
        return nii, total_income, ppop, pbt, net_income

    fy_duration = (330, 380)
    prev_duration = (240, 300)

    # Extract FY cumulative values from current filing
    full_ie = pick_cumulative_value(
        facts, BANK_INTEREST_EARNED_TAGS, _NAMESPACE_MODE,
        target_end_date, fy_duration,
    )
    full_oi = pick_cumulative_value(
        facts, OTHER_INCOME_TAGS, _NAMESPACE_MODE,
        target_end_date, fy_duration,
    )
    full_ix = pick_cumulative_value(
        facts, BANK_INTEREST_EXPENDED_TAGS, _NAMESPACE_MODE,
        target_end_date, fy_duration,
    )
    full_op = pick_cumulative_value(
        facts, BANK_OPERATING_PROFIT_TAGS, _NAMESPACE_MODE,
        target_end_date, fy_duration,
    )
    full_pbt = pick_cumulative_value(
        facts, BANK_PBT_TAGS, _NAMESPACE_MODE,
        target_end_date, fy_duration,
    )
    full_net = pick_cumulative_value(
        facts, BANK_NET_PROFIT_TAGS, _NAMESPACE_MODE,
        target_end_date, fy_duration,
    )

    # Extract Q3 YTD cumulative values from previous quarter's filing
    prev_ie = pick_cumulative_value(
        prev_facts, BANK_INTEREST_EARNED_TAGS, _NAMESPACE_MODE,
        prev_end_date, prev_duration,
    )
    prev_oi = pick_cumulative_value(
        prev_facts, OTHER_INCOME_TAGS, _NAMESPACE_MODE,
        prev_end_date, prev_duration,
    )
    prev_ix = pick_cumulative_value(
        prev_facts, BANK_INTEREST_EXPENDED_TAGS, _NAMESPACE_MODE,
        prev_end_date, prev_duration,
    )
    prev_op = pick_cumulative_value(
        prev_facts, BANK_OPERATING_PROFIT_TAGS, _NAMESPACE_MODE,
        prev_end_date, prev_duration,
    )
    prev_pbt = pick_cumulative_value(
        prev_facts, BANK_PBT_TAGS, _NAMESPACE_MODE,
        prev_end_date, prev_duration,
    )
    prev_net = pick_cumulative_value(
        prev_facts, BANK_NET_PROFIT_TAGS, _NAMESPACE_MODE,
        prev_end_date, prev_duration,
    )

    # Compute Q4 deltas and apply if reasonable
    if (
        full_ie is not None
        and full_ix is not None
        and prev_ie is not None
        and prev_ix is not None
    ):
        # NII delta
        q4_ie = full_ie - prev_ie
        q4_ix = full_ix - prev_ix
        q4_nii = q4_ie - q4_ix
        if should_apply_q4_delta(q4_nii, nii):
            nii = q4_nii

        # Total income delta
        q4_oi = (full_oi or 0.0) - (prev_oi or 0.0)
        q4_total_income = q4_ie + q4_oi
        if should_apply_q4_delta(q4_total_income, total_income):
            total_income = q4_total_income

    if full_op is not None and prev_op is not None:
        q4_ppop = full_op - prev_op
        if should_apply_q4_delta(q4_ppop, ppop):
            ppop = q4_ppop

    if full_pbt is not None and prev_pbt is not None:
        q4_pbt = full_pbt - prev_pbt
        if should_apply_q4_delta(q4_pbt, pbt):
            pbt = q4_pbt

    if full_net is not None and prev_net is not None:
        q4_net = full_net - prev_net
        if should_apply_q4_delta(q4_net, net_income):
            net_income = q4_net

    return nii, total_income, ppop, pbt, net_income


def _should_apply_q4_delta(
    target_end_date: dt.date,
    plan: dict | None,
    ppop: float | None,
    pbt: float | None,
    net_income: float | None,
) -> bool:
    """Decide whether Q4 delta normalization should run.

    Apply delta normalization only when Q4 direct-quarter contexts are likely
    unavailable (for example, only FY cumulative contexts are present) or when
    key profitability metrics are missing in direct extraction.
    """
    if not (target_end_date.month == 3 and target_end_date.day == 31):
        return False

    plan_duration = plan.get("duration_days") if plan else None
    if plan_duration and plan_duration <= 120:
        # We already have a direct quarterly context (typically OneD).
        # Keep reported quarter values and avoid replacing them with FY-Q3 deltas.
        return (
            ppop is None
            or pbt is None
            or net_income is None
        )

    return True


def build_bank_metrics(
    facts: dict,
    target_period: str,
    file_path: str,
) -> FinancialMetrics:
    """Build financial metrics for a banking (IN-GAAP) company.

    Args:
        facts: Parsed XBRL facts dict from ``extract_facts``.
        target_period: Target period date string (e.g. "31-Mar-2026").
        file_path: Path to the current XBRL file (needed for Q4 delta
            normalization to locate the previous quarter's file).

    Returns:
        FinancialMetrics with banking-relevant fields populated.
    """
    target_end_date = dt.datetime.strptime(target_period, "%d-%b-%Y").date()
    warnings: list[str] = []

    # Resolve the period context plan using Interest Earned as the probe tag.
    plan = resolve_period_context_plan(
        facts,
        BANK_INTEREST_EARNED_TAGS,
        _NAMESPACE_MODE,
        target_end_date,
        allow_end_date_fallback=False,
    )

    interest_earned = pick_value_for_plan(
        facts, BANK_INTEREST_EARNED_TAGS, plan, _NAMESPACE_MODE,
    )
    other_income = pick_value_for_plan(
        facts, OTHER_INCOME_TAGS, plan, _NAMESPACE_MODE,
    )
    interest_expended = pick_value_for_plan(
        facts, BANK_INTEREST_EXPENDED_TAGS, plan, _NAMESPACE_MODE,
    )

    # NII = Interest Earned − Interest Expended
    nii = None
    if interest_earned is not None and interest_expended is not None:
        nii = interest_earned - interest_expended

    # Total Income = Interest Earned + Other Income
    total_income = None
    if interest_earned is not None and other_income is not None:
        total_income = interest_earned + other_income

    ppop = pick_value_for_plan(
        facts, BANK_OPERATING_PROFIT_TAGS, plan, _NAMESPACE_MODE,
    )

    pbt = pick_value_for_plan(
        facts, BANK_PBT_TAGS, plan, _NAMESPACE_MODE,
    )

    net_income = pick_value_for_plan(
        facts, BANK_NET_PROFIT_TAGS, plan, _NAMESPACE_MODE,
    )

    basic_eps = pick_value_for_plan(
        facts, BANK_BASIC_EPS_TAGS, plan, _NAMESPACE_MODE,
    )

    diluted_eps = pick_value_for_plan(
        facts, BANK_DILUTED_EPS_TAGS, plan, _NAMESPACE_MODE,
    )

    # ── Q4 delta normalization ──────────────────────────────────────
    if _should_apply_q4_delta(target_end_date, plan, ppop, pbt, net_income):
        nii, total_income, ppop, pbt, net_income = _apply_q4_delta_normalization(
            facts,
            file_path,
            target_end_date,
            interest_earned,
            other_income,
            interest_expended,
            nii,
            total_income,
            ppop,
            pbt,
            net_income,
        )

    # ── ROA ─────────────────────────────────────────────────────────
    roa = _extract_bank_roa(
        facts,
        plan,
        target_end_date,
        net_income,
        file_path,
    )

    # ── NPA ─────────────────────────────────────────────────────────
    gnpa_pct, nnpa_pct = _extract_bank_npa(
        facts,
        plan,
        target_end_date,
        file_path,
    )

    # Populate warnings
    if nii is None:
        warnings.append("NII could not be computed")
    if ppop is None:
        warnings.append("PPOP not found")
    if pbt is None:
        warnings.append("PBT not found")
    if net_income is None:
        warnings.append("Net Income not found")

    return FinancialMetrics(
        company_type="bank",
        nii=nii,
        total_income=total_income,
        ppop=ppop,
        pbt=pbt,
        net_income=net_income,
        basic_eps=basic_eps,
        diluted_eps=diluted_eps,
        roa=roa,
        gnpa_pct=gnpa_pct,
        nnpa_pct=nnpa_pct,
        warnings=warnings,
    )
