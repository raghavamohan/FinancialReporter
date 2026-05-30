r"""Prewarm local XBRL and market-data cache for Nifty 50 symbols.

This script downloads the latest quarters of NSE XBRL filings for all
Nifty 50 companies into a local output directory and also fills the
corporate-actions JSON cache used by market data helpers.

Usage (from repository root):

    python .\scripts\prewarm_nifty50_cache.py
    python .\scripts\prewarm_nifty50_cache.py --quarter Q4_FY26 --quarters 13
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fin_reporter.downloader import NSEXBRLDownloader
from fin_reporter.market_data import fetch_corporate_actions_rows
from fin_reporter.models import DownloadResult

# Fallback Nifty 50 constituents as NSE symbols.
FALLBACK_NIFTY50_SYMBOLS: tuple[str, ...] = (
    "ADANIENT",
    "ADANIPORTS",
    "APOLLOHOSP",
    "ASIANPAINT",
    "AXISBANK",
    "BAJAJ-AUTO",
    "BAJAJFINSV",
    "BAJFINANCE",
    "BEL",
    "BHARTIARTL",
    "BRITANNIA",
    "CIPLA",
    "COALINDIA",
    "DRREDDY",
    "EICHERMOT",
    "GRASIM",
    "HCLTECH",
    "HDFCBANK",
    "HDFCLIFE",
    "HEROMOTOCO",
    "HINDALCO",
    "HINDUNILVR",
    "ICICIBANK",
    "INDUSINDBK",
    "INFY",
    "ITC",
    "JIOFIN",
    "JSWSTEEL",
    "KOTAKBANK",
    "LT",
    "M&M",
    "MARUTI",
    "NESTLEIND",
    "NTPC",
    "ONGC",
    "POWERGRID",
    "RELIANCE",
    "SBILIFE",
    "SBIN",
    "SHRIRAMFIN",
    "SUNPHARMA",
    "TATACONSUM",
    "TATAMOTORS",
    "TATASTEEL",
    "TCS",
    "TECHM",
    "TITAN",
    "TRENT",
    "ULTRACEMCO",
    "WIPRO",
)


def resolve_nifty50_symbols(downloader: NSEXBRLDownloader) -> list[str]:
    """Fetch latest Nifty 50 symbols from NSE, with local fallback."""
    url = "https://www.nseindia.com/api/equity-stockIndices"
    params = {"index": "NIFTY 50"}
    try:
        downloader.ensure_api_session()
        response = downloader._api_get(url, params=params)
        if response.status_code == 200:
            payload = response.json()
            rows = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(rows, list):
                symbols: list[str] = []
                seen: set[str] = set()
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    symbol = str(row.get("symbol", "")).strip().upper()
                    if not symbol or symbol in seen or symbol == "NIFTY 50":
                        continue
                    seen.add(symbol)
                    symbols.append(symbol)
                if len(symbols) >= 45:
                    return symbols
    except Exception:
        pass
    return list(FALLBACK_NIFTY50_SYMBOLS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prewarm last N quarters of XBRL + corporate-actions cache "
            "for all Nifty 50 companies."
        )
    )
    parser.add_argument(
        "--quarter",
        type=str,
        default=None,
        help=(
            "Anchor quarter code like Q4_FY26. "
            "If omitted, latest completed quarter is auto-detected."
        ),
    )
    parser.add_argument(
        "--quarters",
        type=int,
        default=13,
        help="Number of trailing quarters to prewarm (default: 13).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=".\\xbrl_downloads",
        help="Cache directory for downloaded XBRL files.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Delay in seconds between symbol requests.",
    )
    parser.add_argument(
        "--no-eps-support",
        action="store_true",
        help=(
            "Only fetch requested quarter range. "
            "By default script also fetches up to 3 older support quarters "
            "for trailing EPS / P-E continuity."
        ),
    )
    return parser.parse_args()


def latest_completed_quarter_code(today: dt.date | None = None) -> str:
    """Return latest completed Indian fiscal quarter code (Qx_FYyy)."""
    as_of = today or dt.date.today()
    checkpoints: list[tuple[dt.date, str]] = [
        (dt.date(as_of.year - 1, 6, 30), f"Q1_FY{(as_of.year) % 100:02d}"),
        (dt.date(as_of.year - 1, 9, 30), f"Q2_FY{(as_of.year) % 100:02d}"),
        (dt.date(as_of.year - 1, 12, 31), f"Q3_FY{(as_of.year) % 100:02d}"),
        (dt.date(as_of.year, 3, 31), f"Q4_FY{(as_of.year) % 100:02d}"),
        (dt.date(as_of.year, 6, 30), f"Q1_FY{(as_of.year + 1) % 100:02d}"),
        (dt.date(as_of.year, 9, 30), f"Q2_FY{(as_of.year + 1) % 100:02d}"),
        (dt.date(as_of.year, 12, 31), f"Q3_FY{(as_of.year + 1) % 100:02d}"),
    ]
    completed = [item for item in checkpoints if item[0] <= as_of]
    if not completed:
        return f"Q4_FY{(as_of.year - 1) % 100:02d}"
    return completed[-1][1]


def write_download_reports(
    rows: list[DownloadResult],
    output_dir: str,
) -> tuple[Path, Path]:
    """Write all-download and failures-only CSV reports."""
    report_dir = Path(output_dir) / ".reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    all_path = report_dir / f"prewarm_download_report_{stamp}.csv"
    failed_path = report_dir / f"prewarm_failures_{stamp}.csv"
    headers = (
        "symbol",
        "quarter_label",
        "period",
        "status",
        "source",
        "filing_basis",
        "file_path",
        "message",
    )

    with all_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(
                [
                    row.symbol,
                    row.quarter_label,
                    row.period,
                    row.status,
                    row.source,
                    row.filing_basis,
                    row.file_path,
                    row.message,
                ]
            )

    with failed_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for row in rows:
            if row.status == "DOWNLOADED":
                continue
            writer.writerow(
                [
                    row.symbol,
                    row.quarter_label,
                    row.period,
                    row.status,
                    row.source,
                    row.filing_basis,
                    row.file_path,
                    row.message,
                ]
            )

    return all_path, failed_path


def build_cached_results_for_quarter(
    downloader: NSEXBRLDownloader,
    symbols: list[str],
    quarter: str,
    output_dir: str,
) -> list[DownloadResult]:
    """Build DownloadResult rows from local cache only."""
    quarter_label = quarter.strip().upper()
    target_period = downloader.resolve_target_period(quarter_label)
    results: list[DownloadResult] = []
    for raw_symbol in symbols:
        requested_symbol = raw_symbol.upper().strip()
        symbol = downloader.normalize_symbol(requested_symbol)
        cached_path = downloader._find_cached_filing_file(
            output_dir,
            symbol,
            quarter_label,
            "XBRL",
        )
        if cached_path:
            results.append(
                downloader._result_from_cached_file(
                    requested_symbol,
                    symbol,
                    target_period,
                    quarter_label,
                    cached_path,
                )
            )
            continue
        results.append(
            DownloadResult(
                symbol=requested_symbol,
                period=target_period,
                status="NOT_FOUND",
                file_path="-",
                message=(
                    f"Expected cached file missing for symbol '{symbol}' "
                    f"({quarter_label})"
                ),
                source="-",
                filing_basis="unknown",
                quarter_label=quarter_label,
            )
        )
    return results


def main() -> None:
    args = parse_args()
    if args.quarters < 1:
        raise ValueError("--quarters must be at least 1.")

    anchor_quarter = (args.quarter or latest_completed_quarter_code()).upper().strip()
    downloader = NSEXBRLDownloader(
        timeout=args.timeout,
        delay_seconds=args.delay,
    )
    symbols = resolve_nifty50_symbols(downloader)
    print(
        f"[*] Nifty 50 prewarm start: symbols={len(symbols)}, "
        f"anchor={anchor_quarter}, quarters={args.quarters}"
    )

    if args.no_eps_support:
        display_quarters = downloader.resolve_quarter_sequence(
            anchor_quarter,
            args.quarters,
        )
        download_quarters = list(display_quarters)
    else:
        display_quarters, download_quarters = (
            downloader.resolve_display_and_download_quarters(
                anchor_quarter,
                args.quarters,
            )
        )

    print("[*] Display quarters: " + ", ".join(display_quarters))
    if download_quarters != display_quarters:
        support = [
            item
            for item in download_quarters
            if item not in set(display_quarters)
        ]
        if support:
            print(
                "[*] Extra support quarters for trailing EPS/P-E: "
                + ", ".join(support)
            )

    run_needs_nse = downloader.needs_nse_access(
        symbols,
        download_quarters,
        args.output,
    )
    if not run_needs_nse:
        print("[+] XBRL already cached for requested range.")

    status_counts: Counter[str] = Counter()
    all_results: list[DownloadResult] = []
    nse_session_initialized = False
    for idx, quarter in enumerate(download_quarters, start=1):
        print(f"\n=== [{idx}/{len(download_quarters)}] Quarter {quarter} ===")
        quarter_needs_nse = downloader.needs_nse_access(
            symbols,
            [quarter],
            args.output,
        )
        if quarter_needs_nse:
            if not nse_session_initialized:
                downloader.initialize_session()
                nse_session_initialized = True
            results = downloader.download_for_symbols(
                symbols,
                quarter,
                args.output,
            )
        else:
            print(
                f"[+] Quarter {quarter} already cached; "
                "skipping network download."
            )
            results = build_cached_results_for_quarter(
                downloader,
                symbols,
                quarter,
                args.output,
            )
        all_results.extend(results)
        status_counts.update(result.status for result in results)
        quarter_counter = Counter(result.status for result in results)
        print(
            "[*] Quarter status counts: "
            + ", ".join(f"{k}={v}" for k, v in sorted(quarter_counter.items()))
        )

    print("\n=== Corporate-actions cache warmup ===")
    for idx, raw_symbol in enumerate(symbols, start=1):
        symbol = downloader.normalize_symbol(raw_symbol)
        rows = fetch_corporate_actions_rows(
            downloader,
            symbol,
            cache_dir=args.output,
        )
        print(
            f"[*] [{idx}/{len(symbols)}] {symbol}: cached {len(rows)} rows"
        )

    print("\n=== Prewarm complete ===")
    print(
        "[+] Download status totals: "
        + ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items()))
    )
    print(f"[+] Output cache directory: {args.output}")
    all_report, failed_report = write_download_reports(all_results, args.output)
    print(f"[+] Download report CSV: {all_report}")
    print(f"[+] Failures report CSV: {failed_report}")


if __name__ == "__main__":
    main()
