"""Local XBRL cache path helpers."""

from __future__ import annotations

import os


def build_xbrl_file_name(symbol: str, quarter_label: str, xbrl_url: str) -> str:
    """Build a consolidated primary cache filename from an NSE XBRL URL."""
    extension = ".zip"
    lower_url = xbrl_url.lower()
    if lower_url.endswith(".xml"):
        extension = ".xml"
    elif lower_url.endswith(".xbrl"):
        extension = ".xbrl"
    return f"{symbol}_{quarter_label}_XBRL{extension}"


def build_standalone_file_name(symbol: str, quarter_label: str, xbrl_url: str) -> str:
    """Build a standalone companion cache filename."""
    return build_xbrl_file_name(symbol, quarter_label, xbrl_url).replace(
        "_XBRL", "_STANDALONE"
    )


def find_cached_filing_file(
    output_dir: str,
    symbol: str,
    quarter_label: str,
    variant: str,
) -> str | None:
    """Return a non-empty local XBRL path when already downloaded.

    Args:
        variant: ``"XBRL"`` for consolidated primary filing, or
            ``"STANDALONE"`` for the standalone companion file.
    """
    for ext in (".xml", ".xbrl", ".zip"):
        candidate = os.path.join(
            output_dir,
            f"{symbol}_{quarter_label}_{variant}{ext}",
        )
        if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
            return candidate
    return None


def all_primary_files_cached(
    symbols: list[str],
    quarter_labels: list[str],
    output_dir: str,
    *,
    normalize_symbol,
) -> bool:
    """True when every symbol/quarter primary XBRL file exists locally."""
    for raw_symbol in symbols:
        symbol = normalize_symbol(raw_symbol.upper().strip())
        for quarter_label in quarter_labels:
            label = quarter_label.strip().upper()
            if not find_cached_filing_file(output_dir, symbol, label, "XBRL"):
                return False
    return True
