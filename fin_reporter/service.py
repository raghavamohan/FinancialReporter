"""Shared analysis orchestration for CLI and web API."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field

from fin_reporter.downloader import NSEXBRLDownloader
from fin_reporter.market_data import (
    CorporateActionsContext,
    enrich_market_metrics,
    fetch_share_restructuring_events,
    trailing_basic_eps_for_anchor,
)
from fin_reporter.metrics import build_metrics_from_file
from fin_reporter.models import DownloadResult, FinancialMetrics


@dataclass
class AnalysisResult:
    """Outcome of a multi-symbol, multi-quarter analysis run."""

    display_quarters: list[str]
    download_quarters: list[str]
    download_results: list[DownloadResult]
    metrics_by_symbol: dict[str, dict[str, FinancialMetrics]] = field(
        default_factory=dict
    )
    errors: list[str] = field(default_factory=list)


def latest_period_end_for_results(
    results: list[DownloadResult],
    symbol: str,
) -> dt.date | None:
    """Latest period-end date among downloaded filings for one symbol."""
    latest: dt.date | None = None
    for result in results:
        if result.symbol != symbol or result.status != "DOWNLOADED":
            continue
        try:
            period_end = dt.datetime.strptime(result.period, "%d-%b-%Y").date()
        except ValueError:
            continue
        if latest is None or period_end > latest:
            latest = period_end
    return latest


def _find_anchor_download(
    successful: list[DownloadResult],
    symbol: str,
    anchor_date: dt.date | None,
) -> DownloadResult | None:
    if anchor_date is None:
        return None
    sym_key = symbol.upper().strip()
    for result in successful:
        if result.symbol.upper().strip() != sym_key:
            continue
        try:
            period_end = dt.datetime.strptime(result.period, "%d-%b-%Y").date()
        except ValueError:
            continue
        if period_end == anchor_date:
            return result
    return None


def _corporate_actions_context(
    downloader: NSEXBRLDownloader,
    contexts: dict[str, CorporateActionsContext],
    symbol: str,
    file_path: str,
) -> CorporateActionsContext:
    sym_key = symbol.upper().strip()
    if sym_key not in contexts:
        contexts[sym_key] = CorporateActionsContext(
            downloader,
            os.path.dirname(file_path),
        )
    return contexts[sym_key]


def run_analysis(
    symbols: list[str],
    quarter: str,
    back_quarters: int,
    cache_dir: str,
    *,
    ebitda_definition: str = "include-other-income",
    downloader: NSEXBRLDownloader | None = None,
) -> AnalysisResult:
    """Download filings, compute metrics, and enrich with market data."""
    active_downloader = downloader or NSEXBRLDownloader()
    display_quarters, download_quarters = (
        active_downloader.resolve_display_and_download_quarters(
            quarter,
            back_quarters,
        )
    )
    display_set = {label.strip().upper() for label in display_quarters}

    if active_downloader.all_requested_files_cached(
        symbols,
        download_quarters,
        cache_dir,
    ):
        print("[+] All requested XBRL files found locally; skipping NSE session.")
    else:
        active_downloader.ensure_download_session(
            symbols,
            download_quarters,
            cache_dir,
        )

    download_results: list[DownloadResult] = []
    errors: list[str] = []
    for download_quarter in download_quarters:
        try:
            quarter_results = active_downloader.download_for_symbols(
                symbols,
                download_quarter,
                cache_dir,
            )
            download_results.extend(quarter_results)
        except Exception as exc:
            message = f"Download failed for quarter {download_quarter}: {exc}"
            print(f"[!] {message}")
            errors.append(message)

    successful = [
        result
        for result in download_results
        if result.status == "DOWNLOADED"
        and result.file_path != "-"
        and (result.quarter_label or quarter).strip().upper() in display_set
    ]

    symbol_anchors = {
        symbol.upper().strip(): latest_period_end_for_results(successful, symbol)
        for symbol in symbols
    }

    trailing_eps_by_symbol: dict[str, float | None] = {}
    corporate_actions_by_symbol: dict[str, CorporateActionsContext] = {}

    for symbol in symbols:
        sym_key = symbol.upper().strip()
        anchor_date = symbol_anchors.get(sym_key)
        anchor_result = _find_anchor_download(successful, sym_key, anchor_date)
        if anchor_result is None:
            trailing_eps_by_symbol[sym_key] = None
            continue

        ca_context = _corporate_actions_context(
            active_downloader,
            corporate_actions_by_symbol,
            sym_key,
            anchor_result.file_path,
        )
        nse_symbol = active_downloader.normalize_symbol(sym_key)
        restructuring = fetch_share_restructuring_events(
            active_downloader,
            nse_symbol,
            cache_dir=os.path.dirname(anchor_result.file_path),
            corporate_actions=ca_context,
        )
        trailing_eps_by_symbol[sym_key] = trailing_basic_eps_for_anchor(
            anchor_result.file_path,
            anchor_result.period,
            restructuring_events=restructuring,
            eps_anchor_date=anchor_date,
        )

    metrics_by_symbol: dict[str, dict[str, FinancialMetrics]] = {}
    for result in successful:
        sym_key = result.symbol.upper().strip()
        quarter_label = (result.quarter_label or quarter).upper().strip()
        try:
            metrics = build_metrics_from_file(
                result.file_path,
                result.period,
                ebitda_definition=ebitda_definition,
            )
            ca_context = _corporate_actions_context(
                active_downloader,
                corporate_actions_by_symbol,
                sym_key,
                result.file_path,
            )
            enriched = enrich_market_metrics(
                metrics,
                result.file_path,
                result.period,
                sym_key,
                active_downloader,
                pe_anchor_date=symbol_anchors.get(sym_key),
                trailing_eps=trailing_eps_by_symbol.get(sym_key),
                corporate_actions=ca_context,
            )
            metrics_by_symbol.setdefault(sym_key, {})[quarter_label] = enriched
        except Exception as exc:
            message = f"Metrics failed for {sym_key} {quarter_label}: {exc}"
            print(f"[!] {message}")
            errors.append(message)

    return AnalysisResult(
        display_quarters=display_quarters,
        download_quarters=download_quarters,
        download_results=download_results,
        metrics_by_symbol=metrics_by_symbol,
        errors=errors,
    )
