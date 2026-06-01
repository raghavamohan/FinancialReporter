"""Tests for unified XBRL parsing and cache."""

from pathlib import Path

from fin_reporter.metrics.base import detect_company_type, should_apply_q4_delta
from fin_reporter.xbrl_parser import clear_parse_cache, extract_facts, parse_xbrl

from tests.fixtures.sample_xbrl import BANK_XBRL, MANUFACTURING_XBRL


def test_parse_xbrl_single_read(tmp_path: Path):
    clear_parse_cache()
    file_path = tmp_path / "RELIANCE_Q4_FY26_XBRL.xml"
    file_path.write_bytes(MANUFACTURING_XBRL)

    first = parse_xbrl(str(file_path))
    second = parse_xbrl(str(file_path))

    assert first is second
    assert first.metadata.company_name == "Test Manufacturing Co"
    assert first.metadata.nature == "Consolidated"
    assert detect_company_type(first.facts) == "manufacturing"

    facts, contexts = extract_facts(str(file_path))
    assert facts is first.facts
    assert contexts is first.contexts


def test_detect_company_type_bank(tmp_path: Path):
    clear_parse_cache()
    file_path = tmp_path / "HDFCBANK_Q4_FY26_XBRL.xml"
    file_path.write_bytes(BANK_XBRL)
    parsed = parse_xbrl(str(file_path))
    assert detect_company_type(parsed.facts) == "bank"


def test_should_apply_q4_delta():
    assert should_apply_q4_delta(100.0, 105.0) is True
    assert should_apply_q4_delta(None, 10.0) is False
    assert should_apply_q4_delta(50.0, None) is True
