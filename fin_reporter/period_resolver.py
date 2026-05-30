"""Quarter/period context resolution and quarter arithmetic.

This module resolves which XBRL context(s) to use for extracting quarterly
financial data. Indian quarterly filings can present data in several ways:

1. **Direct quarter context** (80-100 days duration ending on target date)
   — Most common for Q1, Q2, Q3.

2. **Cumulative delta** (FY cumulative − 9-month cumulative)
   — Common for Q4 filings where only FY and prior YTD are available.

3. **Fallback** — Use the best available context ending on the target date.

Banking Q2/Q3 cross-file deltas (H1−Q1, 9M−H1) are implemented in ``metrics.banking``
when strategy 1 finds no ~3-month context.
"""

import datetime as dt
import os
import re

from fin_reporter.xbrl_parser import (
    extract_facts,
    pick_numeric,
    select_entries,
)


# ─── Quarter date arithmetic ────────────────────────────────────────────────


def quarter_start_date(target_end_date: dt.date) -> dt.date | None:
    """Return the first calendar day of the quarter ending on ``target_end_date``.

    Indian fiscal quarter ends: Mar 31, Jun 30, Sep 30, Dec 31.
    """
    if target_end_date.month == 3:
        return dt.date(target_end_date.year, 1, 1)
    if target_end_date.month == 6:
        return dt.date(target_end_date.year, 4, 1)
    if target_end_date.month == 9:
        return dt.date(target_end_date.year, 7, 1)
    if target_end_date.month == 12:
        return dt.date(target_end_date.year, 10, 1)
    return None


def previous_quarter_end(target_end_date: dt.date) -> dt.date | None:
    """Return the end date of the quarter preceding the given quarter end.

    Examples:
        31-Mar-2026 → 31-Dec-2025
        30-Jun-2025 → 31-Mar-2025
        30-Sep-2025 → 30-Jun-2025
        31-Dec-2025 → 30-Sep-2025
    """
    if target_end_date.month == 3:
        return dt.date(target_end_date.year - 1, 12, 31)
    if target_end_date.month == 6:
        return dt.date(target_end_date.year, 3, 31)
    if target_end_date.month == 9:
        return dt.date(target_end_date.year, 6, 30)
    if target_end_date.month == 12:
        return dt.date(target_end_date.year, 9, 30)
    return None


def quarter_code_from_file_path(file_path: str) -> str | None:
    """Extract quarter code (e.g. 'Q3_FY26') from an XBRL filename."""
    match = re.search(
        r"_(Q[1-4]_FY\d{2,4})_XBRL",
        os.path.basename(file_path),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).upper()


def previous_quarter_code(quarter_code: str) -> str | None:
    """Return the quarter code for the preceding quarter.

    Examples:
        Q3_FY26 → Q2_FY26
        Q1_FY26 → Q4_FY25
    """
    match = re.fullmatch(r"Q([1-4])_FY(\d{2}|\d{4})", quarter_code)
    if not match:
        return None
    qn = int(match.group(1))
    fy = match.group(2)
    fy_end_year = 2000 + int(fy) if len(fy) == 2 else int(fy)
    if qn > 1:
        prev_q = qn - 1
        prev_fy_end = fy_end_year
    else:
        prev_q = 4
        prev_fy_end = fy_end_year - 1
    fy_token = f"{prev_fy_end % 100:02d}" if len(fy) == 2 else str(prev_fy_end)
    return f"Q{prev_q}_FY{fy_token}"


def find_symbol_quarter_file(file_path: str, quarter_code: str) -> str | None:
    """Find the cached XBRL file for a given symbol and quarter code."""
    basename = os.path.basename(file_path)
    parts = basename.split("_", 1)
    if len(parts) < 2:
        return None
    symbol = parts[0]
    directory = os.path.dirname(file_path)
    for ext in (".xml", ".xbrl", ".zip"):
        candidate = os.path.join(directory, f"{symbol}_{quarter_code}_XBRL{ext}")
        if os.path.exists(candidate):
            return candidate
    return None


# ─── Period context plan resolution ──────────────────────────────────────────


def resolve_period_context_plan(
    facts: dict,
    primary_tags: tuple[str, ...],
    namespace_mode: str,
    target_end_date: dt.date,
    allow_end_date_fallback: bool = True,
) -> dict | None:
    """Determine the best strategy for extracting quarterly values.

    Returns a "plan" dict describing how to extract the quarterly value:
    - ``{"mode": "single", "context_ref": ...}`` — use one context directly
    - ``{"mode": "delta", "fy_context_ref": ..., "nine_month_context_ref": ...}``
      — compute FY − 9M to isolate Q4

    The plan is determined by probing the primary_tags to find which
    period durations are available in the filing.
    """
    # Strategy 1: Direct 3-month quarter context ending on target date
    quarter_entries = select_entries(
        facts,
        primary_tags,
        namespace_mode=namespace_mode,
        end_date=target_end_date,
        duration_between=(80, 100),
    )
    _quarter_value, quarter_entry = pick_numeric(quarter_entries, target_end_date)
    if quarter_entry:
        return {
            "mode": "single",
            "context_ref": quarter_entry["context_ref"],
            "duration_days": quarter_entry.get("duration_days"),
        }

    # Strategy 2: FY cumulative − 9M cumulative (for Q4 isolation)
    fy_entries = select_entries(
        facts,
        primary_tags,
        namespace_mode=namespace_mode,
        end_date=target_end_date,
        duration_between=(330, 380),
    )
    _fy_value, fy_entry = pick_numeric(fy_entries, target_end_date)
    prev_end = previous_quarter_end(target_end_date)
    if fy_entry and prev_end:
        nine_month_entries = select_entries(
            facts,
            primary_tags,
            namespace_mode=namespace_mode,
            end_date=prev_end,
            duration_between=(240, 300),
        )
        _nine_value, nine_entry = pick_numeric(nine_month_entries, prev_end)
        if nine_entry:
            return {
                "mode": "delta",
                "fy_context_ref": fy_entry["context_ref"],
                "nine_month_context_ref": nine_entry["context_ref"],
                "duration_days": fy_entry.get("duration_days"),
            }

    # Strategy 3: Fallback — best available context ending on target date
    if allow_end_date_fallback:
        fallback_entries = select_entries(
            facts,
            primary_tags,
            namespace_mode=namespace_mode,
            end_date=target_end_date,
        )
        _fallback_value, fallback_entry = pick_numeric(
            fallback_entries, target_end_date
        )
        if fallback_entry:
            return {
                "mode": "single",
                "context_ref": fallback_entry["context_ref"],
                "duration_days": fallback_entry.get("duration_days"),
            }

    return None


def pick_value_for_plan(
    facts: dict,
    tag_candidates: tuple[str, ...],
    plan: dict | None,
    namespace_mode: str,
) -> float | None:
    """Extract a numeric value using the resolved period context plan.

    For "single" mode: looks up the value in the plan's context_ref.
    For "delta" mode: computes FY_value − 9M_value.
    """
    if not plan:
        return None

    if plan["mode"] == "single":
        entries = select_entries(
            facts,
            tag_candidates,
            namespace_mode=namespace_mode,
            context_ref=plan["context_ref"],
        )
        value, _entry = pick_numeric(entries)
        return value

    if plan["mode"] == "delta":
        fy_entries = select_entries(
            facts,
            tag_candidates,
            namespace_mode=namespace_mode,
            context_ref=plan["fy_context_ref"],
        )
        nine_entries = select_entries(
            facts,
            tag_candidates,
            namespace_mode=namespace_mode,
            context_ref=plan["nine_month_context_ref"],
        )
        fy_value, _fy = pick_numeric(fy_entries)
        nine_value, _nine = pick_numeric(nine_entries)
        if fy_value is None or nine_value is None:
            return None
        return fy_value - nine_value

    return None


def pick_cumulative_value(
    facts: dict,
    tags: tuple[str, ...],
    namespace_mode: str,
    end_date: dt.date,
    duration_between: tuple[int, int],
) -> float | None:
    """Pick a cumulative (YTD or FY) value for the given duration range."""
    entries = select_entries(
        facts,
        tags,
        namespace_mode=namespace_mode,
        end_date=end_date,
        duration_between=duration_between,
    )
    value, _entry = pick_numeric(entries, end_date)
    return value
