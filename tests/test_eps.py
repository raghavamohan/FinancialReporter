"""Tests for lightweight EPS extraction."""

from pathlib import Path

from fin_reporter.eps import extract_basic_eps_from_file
from fin_reporter.xbrl_parser import clear_parse_cache

from tests.fixtures.sample_xbrl import BANK_XBRL, MANUFACTURING_XBRL


def test_extract_basic_eps_manufacturing(tmp_path: Path):
    clear_parse_cache()
    file_path = tmp_path / "RELIANCE_Q4_FY26_XBRL.xml"
    file_path.write_bytes(MANUFACTURING_XBRL)
    eps = extract_basic_eps_from_file(str(file_path), "31-Mar-2026")
    assert eps == 10.5


def test_extract_basic_eps_bank(tmp_path: Path):
    clear_parse_cache()
    file_path = tmp_path / "HDFCBANK_Q4_FY26_XBRL.xml"
    file_path.write_bytes(BANK_XBRL)
    eps = extract_basic_eps_from_file(str(file_path), "31-Mar-2026")
    assert eps == 8.25
