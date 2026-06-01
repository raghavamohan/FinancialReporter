"""Command-line interface for the FinancialReporter.

Usage::

    python -m fin_reporter --symbols RELIANCE HDFCBANK --quarter Q4_FY26

Or via the legacy entry point::

    python fin_report_cli.py --symbols RELIANCE HDFCBANK --quarter Q4_FY26
"""

import argparse

from fin_reporter.display import print_metric_table, print_results_table
from fin_reporter.downloader import NSEXBRLDownloader


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Automated NSE XBRL downloader and CLI result reporter."
        ),
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        required=True,
        help="Space-separated NSE symbols (example: RELIANCE ITC HDFCBANK).",
    )
    parser.add_argument(
        "--quarter",
        type=str,
        required=True,
        help="Quarter code like Q4_FY26 or explicit date like 31-Mar-2026.",
    )
    parser.add_argument(
        "--back-quarters",
        type=int,
        default=1,
        help=(
            "Number of quarters to include, counting backward from --quarter "
            "(includes selected quarter)."
        ),
    )
    parser.add_argument(
        "--output",
        type=str,
        default=".\\xbrl_downloads",
        help="Directory where XBRL files are saved.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP timeout per request in seconds.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Delay (seconds) between symbol requests.",
    )
    parser.add_argument(
        "--debug-tags",
        action="store_true",
        help="Print discovered XBRL tags for EBITDA component debugging.",
    )
    parser.add_argument(
        "--ebitda-definition",
        type=str,
        choices=("include-other-income", "exclude-other-income"),
        default="include-other-income",
        help=(
            "EBITDA definition for manufacturing companies: "
            "'include-other-income' (default) or 'exclude-other-income'."
        ),
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point: download XBRL filings and print metric tables."""
    args = parse_args()
    symbols = [
        symbol.upper().strip()
        for symbol in args.symbols
        if symbol.strip()
    ]
    if not symbols:
        raise ValueError("At least one valid symbol is required.")
    if args.back_quarters < 1:
        raise ValueError("--back-quarters must be at least 1.")

    downloader = NSEXBRLDownloader(
        timeout=args.timeout,
        delay_seconds=args.delay,
    )
    display_quarters, download_quarters = (
        downloader.resolve_display_and_download_quarters(
            args.quarter,
            args.back_quarters,
        )
    )
    support_quarters = [
        label
        for label in download_quarters
        if label.strip().upper()
        not in {token.strip().upper() for token in display_quarters}
    ]
    if support_quarters:
        print(
            "[*] Also fetching prior-quarter XBRL for trailing EPS / P/E: "
            + ", ".join(support_quarters)
        )
    if downloader.all_requested_files_cached(
        symbols,
        download_quarters,
        args.output,
    ):
        print("[+] All requested XBRL files found locally; skipping NSE session.")
    else:
        downloader.initialize_session()
    results = []
    display_set = {token.strip().upper() for token in display_quarters}
    for quarter in download_quarters:
        quarter_results = downloader.download_for_symbols(
            symbols,
            quarter,
            args.output,
        )
        for result in quarter_results:
            quarter_label = (result.quarter_label or quarter).strip().upper()
            if quarter_label not in display_set and result.status == "DOWNLOADED":
                result.message = (
                    f"{result.message} (trailing EPS support, not in metrics table)"
                )
        results.extend(quarter_results)
    print_results_table(results, args.quarter)
    print_metric_table(
        results,
        args.quarter,
        debug_tags=args.debug_tags,
        ebitda_definition=args.ebitda_definition,
        market_downloader=downloader,
        display_quarters=display_quarters,
    )


if __name__ == "__main__":
    main()
