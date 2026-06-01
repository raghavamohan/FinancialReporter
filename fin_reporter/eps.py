"""Lightweight basic-EPS extraction from cached XBRL files."""

from __future__ import annotations

import datetime as dt

from fin_reporter.constants import (
    BANK_BASIC_EPS_TAGS,
    BANK_INTEREST_EARNED_TAGS,
    MANUFACTURING_BASIC_EPS_TAGS,
    MANUFACTURING_REVENUE_TAGS,
)
from fin_reporter.metrics.base import detect_company_type
from fin_reporter.period_resolver import pick_value_for_plan, resolve_period_context_plan
from fin_reporter.xbrl_parser import parse_xbrl


def extract_basic_eps_from_file(file_path: str, target_period: str) -> float | None:
    """Return basic EPS for one quarter without building full FinancialMetrics."""
    parsed = parse_xbrl(file_path)
    if not parsed.facts:
        return None

    try:
        target_end_date = dt.datetime.strptime(target_period, "%d-%b-%Y").date()
    except ValueError:
        return None

    company_type = detect_company_type(parsed.facts)
    if company_type == "bank":
        probe_tags = BANK_INTEREST_EARNED_TAGS
        eps_tags = BANK_BASIC_EPS_TAGS
        namespace_mode = "in-gaap"
        allow_fallback = False
    else:
        probe_tags = MANUFACTURING_REVENUE_TAGS
        eps_tags = MANUFACTURING_BASIC_EPS_TAGS
        namespace_mode = "ind-as"
        allow_fallback = True

    plan = resolve_period_context_plan(
        parsed.facts,
        probe_tags,
        namespace_mode,
        target_end_date,
        allow_end_date_fallback=allow_fallback,
    )
    return pick_value_for_plan(parsed.facts, eps_tags, plan, namespace_mode)
