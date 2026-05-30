"""FinancialReporter — NSE XBRL downloader and financial metrics CLI.

This is a thin wrapper that delegates to the modular ``fin_reporter`` package.
Usage is unchanged::

    python fin_report.py --symbols RELIANCE HDFCBANK --quarter Q4_FY26
"""

from fin_reporter.cli import main

if __name__ == "__main__":
    main()