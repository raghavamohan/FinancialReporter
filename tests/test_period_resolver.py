"""Tests for quarter/date helpers."""

from fin_reporter.period_resolver import (
    previous_quarter_code,
    quarter_code_to_period_end,
    resolve_quarter_sequence,
    resolve_target_period,
)


def test_quarter_code_to_period_end():
    assert quarter_code_to_period_end("Q4_FY26") == "31-Mar-2026"
    assert quarter_code_to_period_end("Q1_FY26") == "30-Jun-2025"
    assert quarter_code_to_period_end("Q3_FY25") == "31-Dec-2024"
    assert quarter_code_to_period_end("INVALID") is None


def test_resolve_target_period_accepts_date():
    assert resolve_target_period("31-Mar-2026") == "31-Mar-2026"
    assert resolve_target_period("Q4_FY26") == "31-Mar-2026"


def test_resolve_quarter_sequence():
    sequence = resolve_quarter_sequence("Q4_FY26", 3)
    assert sequence == ["Q4_FY26", "Q3_FY26", "Q2_FY26"]
    assert previous_quarter_code("Q1_FY26") == "Q4_FY25"
