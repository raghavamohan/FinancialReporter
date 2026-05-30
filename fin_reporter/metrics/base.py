"""Shared helpers for metric calculation used by both bank and manufacturing."""

import datetime as dt

from fin_reporter.constants import (
    BANK_INTEREST_EARNED_TAGS,
    BANK_INTEREST_EXPENDED_TAGS,
    BANK_OPERATING_PROFIT_TAGS,
)
from fin_reporter.xbrl_parser import select_entries


def should_apply_q4_delta(
    delta_value: float | None,
    direct_quarter_value: float | None,
) -> bool:
    """Decide whether a computed Q4 delta (FY − 9M) should replace the direct value.

    The delta is applied when:
    - It is not None
    - Either no direct value exists, or the delta is within 35% of the direct value

    Note: Negative deltas ARE allowed — a company can legitimately report a
    loss in Q4 even with a full-year profit. Only None is rejected.
    """
    if delta_value is None:
        return False
    if direct_quarter_value is None:
        return True
    baseline = max(abs(direct_quarter_value), 1.0)
    return abs(delta_value - direct_quarter_value) / baseline <= 0.35


def detect_company_type(facts: dict) -> str:
    """Auto-detect whether XBRL facts are from a bank or manufacturing company.

    Detection logic:
    - **Bank**: Has all three of InterestEarned, InterestExpended, and
      OperatingProfitBeforeProvision tags (IN-GAAP namespace).
    - **Manufacturing**: Everything else (Ind-AS namespace).

    Returns:
        "bank" or "manufacturing"
    """
    bank_ie = select_entries(
        facts, BANK_INTEREST_EARNED_TAGS, namespace_mode="in-gaap"
    )
    bank_ix = select_entries(
        facts, BANK_INTEREST_EXPENDED_TAGS, namespace_mode="in-gaap"
    )
    bank_op = select_entries(
        facts, BANK_OPERATING_PROFIT_TAGS, namespace_mode="in-gaap"
    )
    if bank_ie and bank_ix and bank_op:
        return "bank"
    return "manufacturing"
