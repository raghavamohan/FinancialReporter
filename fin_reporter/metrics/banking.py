"""Banking (IN-GAAP / RBI format) financial metric calculator.

Extracts quarterly metrics for banking companies reporting under IN-GAAP.
Banks have a fundamentally different P&L structure from manufacturing companies:
interest income/expense IS the core business, so EBITDA is not applicable.
Instead, we compute NII, PPOP, and ROA.

Q4 normalization:
    Q4 filings often contain only full-year (FY) cumulative figures. When the
    previous quarter's XBRL file is available in the same directory, we compute
    Q4 = FY_cumulative − Q3_YTD_cumulative for each metric.

Q2/Q3 normalization:
    Some banks publish only H1 (Q2) or 9M (Q3) cumulative P&L contexts. When a
    direct ~3-month context is missing, we derive the quarter from the prior
    quarter's cached XBRL file: Q2 = H1 − Q1, Q3 = 9M − H1.
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
    BANK_EQUITY_SHARE_CAPITAL_TAGS,
    BANK_RESERVES_AND_SURPLUS_TAGS,
    BANK_OPERATING_EXPENSES_TAGS,
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

_QUARTER_DURATION = (80, 100)
_H1_DURATION = (170, 190)
_NINE_MONTH_DURATION = (240, 300)
_FY_DURATION = (330, 380)


def _normalize_ratio_to_percentage(value: float | None) -> float | None:
    """Normalize a ratio-like value to percentage if needed.

    Some filings report ratios as decimals (e.g. 0.0115 for 1.15%).
    Keep values > 1 unchanged (already percentage-like).
    """
    if value is None:
        return None
    return value * 100 if abs(value) <= 1 else value


def _normalize_reported_roa(value: float | None) -> float | None:
    """Normalize a disclosed ReturnOnAssets tag to a percentage.

    Indian bank XBRL filings typically report ROA already on an annualized
    basis (for example ``0.0107`` means 1.07%), even when the context period
    is a single quarter. Do not apply an extra ×4 based on context duration.
    """
    if value is None:
        return None
    if abs(value) <= 1:
        return value * 100
    return value


def _is_plausible_reported_roa(value: float | None) -> bool:
    """Return True when a reported ROA looks like a real bank ratio (%)."""
    if value is None:
        return False
    # Large Indian banks rarely report quarterly ROA below ~0.8%.
    return 0.8 <= value <= 6.0


def _computed_roa_annualization(plan: dict | None) -> int:
    """Annualization factor for ROA computed from quarterly profit and assets."""
    plan_duration = plan.get("duration_days") if plan else None
    if plan_duration and plan_duration > 300:
        return 1
    return 4


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


def _pick_bank_instant_value(
    facts: dict,
    tags: tuple[str, ...],
    target_date: dt.date,
) -> float | None:
    """Extract a numeric value for an instant bank balance sheet tag."""
    entries = select_entries(
        facts,
        tags,
        namespace_mode=_NAMESPACE_MODE,
        instant_date=target_date,
        no_dimensions=True,
    )
    if not entries:
        entries = select_entries(
            facts,
            tags,
            namespace_mode=_NAMESPACE_MODE,
            end_date=target_date,
            no_dimensions=True,
        )
    value, _entry = pick_numeric(entries)
    return value


def _extract_bank_equity(facts: dict, target_date: dt.date) -> float | None:
    """Compute Total Equity for a bank from Share Capital and Reserves & Surplus."""
    cap = _pick_bank_instant_value(facts, BANK_EQUITY_SHARE_CAPITAL_TAGS, target_date)
    res = _pick_bank_instant_value(facts, BANK_RESERVES_AND_SURPLUS_TAGS, target_date)
    if cap is not None or res is not None:
        return (cap or 0.0) + (res or 0.0)
    return None


def _extract_bank_balance_sheet_with_fallback(
    facts: dict,
    file_path: str,
    target_end_date: dt.date,
) -> tuple[float | None, float | None]:
    """Extract bank Total Assets and Equity with prior-quarter file fallback."""
    assets = _pick_bank_instant_value(facts, BANK_TOTAL_ASSETS_TAGS, target_end_date)
    equity = _extract_bank_equity(facts, target_end_date)

    if assets is not None and equity is not None:
        return assets, equity

    # Fallback to prior quarters' files (up to 3 quarters back)
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
                prev_assets = _pick_bank_instant_value(prev_facts, BANK_TOTAL_ASSETS_TAGS, prev_end_date)
                prev_equity = _extract_bank_equity(prev_facts, prev_end_date)

                if prev_assets is not None and prev_equity is not None:
                    return prev_assets, prev_equity

    return assets, equity


def _extract_bank_roa(
    facts: dict,
    plan: dict | None,
    target_end_date: dt.date,
    net_income: float | None,
    file_path: str,
) -> float | None:
    """Extract or compute Return on Assets for a bank.

    Preference order:
        1. Reported ReturnOnAssets on consolidated filing (already annualized %)
        2. Plausible reported ROA on standalone filing (0.3–6%)
        3. Computed: (quarterly NetIncome / TotalAssets) × annualization × 100
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
                normalized_standalone = _normalize_reported_roa(standalone_roa)
                if _is_plausible_reported_roa(normalized_standalone):
                    return normalized_standalone

    normalized_reported = _normalize_reported_roa(reported_roa)
    if _is_plausible_reported_roa(normalized_reported):
        return normalized_reported

    # Compute ROA from Net Income and Total Assets with prior fallback
    total_assets, _equity = _extract_bank_balance_sheet_with_fallback(
        facts, file_path, target_end_date
    )

    if net_income is not None and total_assets not in (None, 0):
        annualization = _computed_roa_annualization(plan)
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


def _intermediate_quarter_ytd_durations(
    target_end_date: dt.date,
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Return (current_ytd_duration, prior_ytd_duration) for Q2/Q3 delta logic."""
    if target_end_date.month == 9 and target_end_date.day == 30:
        return _H1_DURATION, _QUARTER_DURATION
    if target_end_date.month == 12 and target_end_date.day == 31:
        return _NINE_MONTH_DURATION, _H1_DURATION
    return None


def _load_previous_quarter_facts(
    file_path: str,
    target_end_date: dt.date,
) -> tuple[dict, dt.date] | tuple[None, None]:
    """Load prior-quarter XBRL facts from the same cache directory."""
    quarter_code = quarter_code_from_file_path(file_path)
    prev_code = previous_quarter_code(quarter_code) if quarter_code else None
    prev_file = find_symbol_quarter_file(file_path, prev_code) if prev_code else None
    if not prev_file:
        return None, None

    prev_facts, _prev_contexts = extract_facts(prev_file)
    prev_end_date = previous_quarter_end(target_end_date)
    if not prev_end_date or not prev_facts:
        return None, None
    return prev_facts, prev_end_date


def _apply_period_delta_metrics(
    facts: dict,
    prev_facts: dict,
    target_end_date: dt.date,
    prev_end_date: dt.date,
    current_duration: tuple[int, int],
    prior_duration: tuple[int, int],
    nii: float | None,
    total_income: float | None,
    ppop: float | None,
    pbt: float | None,
    net_income: float | None,
    basic_eps: float | None,
) -> tuple[
    float | None,
    float | None,
    float | None,
    float | None,
    float | None,
    float | None,
]:
    """Derive a quarter by subtracting prior-period YTD cumulative from current YTD."""
    full_ie = pick_cumulative_value(
        facts, BANK_INTEREST_EARNED_TAGS, _NAMESPACE_MODE,
        target_end_date, current_duration,
    )
    full_oi = pick_cumulative_value(
        facts, OTHER_INCOME_TAGS, _NAMESPACE_MODE,
        target_end_date, current_duration,
    )
    full_ix = pick_cumulative_value(
        facts, BANK_INTEREST_EXPENDED_TAGS, _NAMESPACE_MODE,
        target_end_date, current_duration,
    )
    full_op = pick_cumulative_value(
        facts, BANK_OPERATING_PROFIT_TAGS, _NAMESPACE_MODE,
        target_end_date, current_duration,
    )
    full_pbt = pick_cumulative_value(
        facts, BANK_PBT_TAGS, _NAMESPACE_MODE,
        target_end_date, current_duration,
    )
    full_net = pick_cumulative_value(
        facts, BANK_NET_PROFIT_TAGS, _NAMESPACE_MODE,
        target_end_date, current_duration,
    )
    full_eps = pick_cumulative_value(
        facts, BANK_BASIC_EPS_TAGS, _NAMESPACE_MODE,
        target_end_date, current_duration,
    )

    prev_ie = pick_cumulative_value(
        prev_facts, BANK_INTEREST_EARNED_TAGS, _NAMESPACE_MODE,
        prev_end_date, prior_duration,
    )
    prev_oi = pick_cumulative_value(
        prev_facts, OTHER_INCOME_TAGS, _NAMESPACE_MODE,
        prev_end_date, prior_duration,
    )
    prev_ix = pick_cumulative_value(
        prev_facts, BANK_INTEREST_EXPENDED_TAGS, _NAMESPACE_MODE,
        prev_end_date, prior_duration,
    )
    prev_op = pick_cumulative_value(
        prev_facts, BANK_OPERATING_PROFIT_TAGS, _NAMESPACE_MODE,
        prev_end_date, prior_duration,
    )
    prev_pbt = pick_cumulative_value(
        prev_facts, BANK_PBT_TAGS, _NAMESPACE_MODE,
        prev_end_date, prior_duration,
    )
    prev_net = pick_cumulative_value(
        prev_facts, BANK_NET_PROFIT_TAGS, _NAMESPACE_MODE,
        prev_end_date, prior_duration,
    )
    prev_eps = pick_cumulative_value(
        prev_facts, BANK_BASIC_EPS_TAGS, _NAMESPACE_MODE,
        prev_end_date, prior_duration,
    )

    if (
        full_ie is not None
        and full_ix is not None
        and prev_ie is not None
        and prev_ix is not None
    ):
        delta_ie = full_ie - prev_ie
        delta_ix = full_ix - prev_ix
        delta_nii = delta_ie - delta_ix
        if should_apply_q4_delta(delta_nii, nii):
            nii = delta_nii

        delta_oi = (full_oi or 0.0) - (prev_oi or 0.0)
        delta_total_income = delta_ie + delta_oi
        if should_apply_q4_delta(delta_total_income, total_income) and (
            nii is None
            or delta_total_income is None
            or delta_total_income >= nii
        ):
            total_income = delta_total_income
        elif nii is not None and total_income is None:
            # Cross-filing OI restatements can make IE+OI delta < NII; use NII.
            total_income = nii

    if full_op is not None and prev_op is not None:
        delta_ppop = full_op - prev_op
        if should_apply_q4_delta(delta_ppop, ppop):
            ppop = delta_ppop

    if full_pbt is not None and prev_pbt is not None:
        delta_pbt = full_pbt - prev_pbt
        if should_apply_q4_delta(delta_pbt, pbt):
            pbt = delta_pbt

    if full_net is not None and prev_net is not None:
        delta_net = full_net - prev_net
        if should_apply_q4_delta(delta_net, net_income):
            net_income = delta_net

    if full_eps is not None and prev_eps is not None:
        delta_eps = full_eps - prev_eps
        if should_apply_q4_delta(delta_eps, basic_eps):
            basic_eps = delta_eps

    return nii, total_income, ppop, pbt, net_income, basic_eps


def _should_apply_intermediate_quarter_delta(
    target_end_date: dt.date,
    plan: dict | None,
    ppop: float | None,
    pbt: float | None,
    net_income: float | None,
) -> bool:
    """Apply Q2/Q3 YTD delta when a direct quarter context is missing."""
    if _intermediate_quarter_ytd_durations(target_end_date) is None:
        return False

    plan_duration = plan.get("duration_days") if plan else None
    if plan_duration and plan_duration <= 120:
        return (
            ppop is None
            or pbt is None
            or net_income is None
        )
    return True


def _apply_intermediate_quarter_delta(
    facts: dict,
    file_path: str,
    target_end_date: dt.date,
    nii: float | None,
    total_income: float | None,
    ppop: float | None,
    pbt: float | None,
    net_income: float | None,
    basic_eps: float | None,
) -> tuple[
    float | None,
    float | None,
    float | None,
    float | None,
    float | None,
    float | None,
]:
    """Derive Q2 or Q3 from YTD cumulative minus prior quarter's cached filing."""
    durations = _intermediate_quarter_ytd_durations(target_end_date)
    if durations is None:
        return nii, total_income, ppop, pbt, net_income, basic_eps

    current_duration, prior_duration = durations
    prev_facts, prev_end_date = _load_previous_quarter_facts(
        file_path,
        target_end_date,
    )
    if not prev_facts or not prev_end_date:
        return nii, total_income, ppop, pbt, net_income, basic_eps

    return _apply_period_delta_metrics(
        facts,
        prev_facts,
        target_end_date,
        prev_end_date,
        current_duration,
        prior_duration,
        nii,
        total_income,
        ppop,
        pbt,
        net_income,
        basic_eps,
    )


def _apply_q4_delta_normalization(
    facts: dict,
    file_path: str,
    target_end_date: dt.date,
    nii: float | None,
    total_income: float | None,
    ppop: float | None,
    pbt: float | None,
    net_income: float | None,
    basic_eps: float | None,
) -> tuple[
    float | None,
    float | None,
    float | None,
    float | None,
    float | None,
    float | None,
]:
    """Apply Q4 delta normalization using previous quarter's XBRL file.

    For Q4 filings (ending 31-Mar), the filing often contains only FY
    cumulative figures. We isolate Q4 by computing:
        Q4_value = FY_cumulative − Q3_YTD_cumulative

    The Q3 YTD cumulative is obtained from the previous quarter's file
    (which must be present in the same directory).
    """
    if not (target_end_date.month == 3 and target_end_date.day == 31):
        return nii, total_income, ppop, pbt, net_income, basic_eps

    prev_facts, prev_end_date = _load_previous_quarter_facts(
        file_path,
        target_end_date,
    )
    if not prev_facts or not prev_end_date:
        return nii, total_income, ppop, pbt, net_income, basic_eps

    return _apply_period_delta_metrics(
        facts,
        prev_facts,
        target_end_date,
        prev_end_date,
        _FY_DURATION,
        _NINE_MONTH_DURATION,
        nii,
        total_income,
        ppop,
        pbt,
        net_income,
        basic_eps,
    )


def _needs_q4_normalization(
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

    used_intermediate_delta = False
    # ── Q2/Q3 YTD delta (H1−Q1, 9M−H1) when no ~3-month context ───
    if _should_apply_intermediate_quarter_delta(
        target_end_date,
        plan,
        ppop,
        pbt,
        net_income,
    ):
        used_intermediate_delta = True
        (
            nii,
            total_income,
            ppop,
            pbt,
            net_income,
            basic_eps,
        ) = _apply_intermediate_quarter_delta(
            facts,
            file_path,
            target_end_date,
            nii,
            total_income,
            ppop,
            pbt,
            net_income,
            basic_eps,
        )

    # ── Q4 delta normalization ──────────────────────────────────────
    if _needs_q4_normalization(target_end_date, plan, ppop, pbt, net_income):
        (
            nii,
            total_income,
            ppop,
            pbt,
            net_income,
            basic_eps,
        ) = _apply_q4_delta_normalization(
            facts,
            file_path,
            target_end_date,
            nii,
            total_income,
            ppop,
            pbt,
            net_income,
            basic_eps,
        )

    # ── ROA ─────────────────────────────────────────────────────────
    roa = _extract_bank_roa(
        facts,
        plan,
        target_end_date,
        net_income,
        file_path,
    )

    # ── ROE & Cost-to-Income ────────────────────────────────────────
    roe = None
    _assets, equity = _extract_bank_balance_sheet_with_fallback(
        facts, file_path, target_end_date
    )
    if net_income is not None and equity is not None and equity > 0:
        annualization = _computed_roa_annualization(plan)
        roe = (net_income / equity) * annualization * 100

    cost_to_income = None
    operating_expenses = pick_value_for_plan(
        facts, BANK_OPERATING_EXPENSES_TAGS, plan, _NAMESPACE_MODE
    )
    if operating_expenses is not None and nii is not None and other_income is not None:
        net_operating_income = nii + other_income
        if net_operating_income > 0:
            cost_to_income = (operating_expenses / net_operating_income) * 100

    # ── NPA ─────────────────────────────────────────────────────────
    gnpa_pct, nnpa_pct = _extract_bank_npa(
        facts,
        plan,
        target_end_date,
        file_path,
    )

    # Populate warnings
    if used_intermediate_delta:
        warnings.append(
            "P&L derived from YTD delta vs prior-quarter file (e.g. H1−Q1); "
            "requires prior quarter XBRL in the same output folder"
        )
    if nii is None:
        warnings.append("NII could not be computed")
    if ppop is None:
        warnings.append("PPOP not found")
    if pbt is None:
        warnings.append("PBT not found")
    if net_income is None:
        warnings.append("Net Income not found")
    if _assets is None:
        warnings.append("Balance Sheet assets/equity not found (metrics like ROE will be missing)")

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
        roe=roe,
        equity=equity,
        cost_to_income=cost_to_income,
        gnpa_pct=gnpa_pct,
        nnpa_pct=nnpa_pct,
        warnings=warnings,
    )
