"""
fin_reporter — NSE XBRL financial metrics extraction and reporting.

Modules:
    constants        XBRL tag definitions and namespace mappings
    models           Data classes for results, metadata, metrics
    xbrl_parser      XBRL XML parsing and fact extraction
    period_resolver  Quarter/period context resolution
    downloader       NSE XBRL file download
    metrics          Financial metric calculators (bank / manufacturing)
    display          Table formatting and output
    cli              Command-line interface
"""

from fin_reporter.models import DownloadResult, FilingMetadata, FinancialMetrics
from fin_reporter.metrics import build_metrics_from_file

# Downloader requires the `requests` library — guard the import so that
# users who only need parsing/metrics don't need it installed.
try:
    from fin_reporter.downloader import NSEXBRLDownloader
except ImportError:
    NSEXBRLDownloader = None  # type: ignore[assignment,misc]

__all__ = [
    "DownloadResult",
    "FilingMetadata",
    "FinancialMetrics",
    "NSEXBRLDownloader",
    "build_metrics_from_file",
]
