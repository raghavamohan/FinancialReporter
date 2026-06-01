"""NSE XBRL filing downloader.

Downloads XBRL financial result filings from NSE for specified company symbols
and quarters, with integrated/legacy API fallback and local cache support.
"""

from __future__ import annotations

import datetime as dt
import os
import time

from fin_reporter.bse_fallback import bse_pdf_only_note
from fin_reporter.cache_paths import (
    build_standalone_file_name,
    build_xbrl_file_name,
    find_cached_filing_file,
)
from fin_reporter.filing_resolver import FilingResolverMixin
from fin_reporter.models import DownloadResult
from fin_reporter.nse_session import NSESessionMixin
from fin_reporter.period_resolver import (
    resolve_display_and_download_quarters,
    resolve_quarter_sequence,
    resolve_target_period,
)
from fin_reporter.timing import time_block
from fin_reporter.xbrl_parser import extract_filing_metadata


class NSEXBRLDownloader(NSESessionMixin, FilingResolverMixin):
    """Downloads XBRL filings from NSE for given symbols and quarters."""

    def __init__(self, timeout: int = 20, delay_seconds: float = 2):
        super().__init__(timeout=timeout, delay_seconds=delay_seconds)
        self.symbol_aliases = {"HDFC": "HDFCBANK"}
        self.lookup_symbol_fallbacks = {"TATAMOTORS": ("TATAMTRDVR",)}
        self.skip_standalone_companion = False

    def resolve_target_period(self, quarter: str) -> str:
        return resolve_target_period(quarter)

    def resolve_quarter_sequence(self, quarter: str, back_quarters: int) -> list[str]:
        return resolve_quarter_sequence(quarter, back_quarters)

    def resolve_display_and_download_quarters(
        self,
        quarter: str,
        back_quarters: int,
    ) -> tuple[list[str], list[str]]:
        return resolve_display_and_download_quarters(quarter, back_quarters)

    def normalize_symbol(self, requested_symbol: str) -> str:
        cleaned = requested_symbol.upper().strip()
        return self.symbol_aliases.get(cleaned, cleaned)

    def candidate_lookup_symbols(self, requested_symbol: str) -> list[str]:
        primary = self.normalize_symbol(requested_symbol)
        candidates = [primary]
        for fallback in self.lookup_symbol_fallbacks.get(primary, ()):
            normalized_fallback = self.normalize_symbol(fallback)
            if normalized_fallback not in candidates:
                candidates.append(normalized_fallback)
        return candidates

    def _find_cached_filing_file(
        self,
        output_dir: str,
        symbol: str,
        quarter_label: str,
        variant: str,
    ) -> str | None:
        return find_cached_filing_file(output_dir, symbol, quarter_label, variant)

    def all_requested_files_cached(
        self,
        symbols: list[str],
        quarter_labels: list[str],
        output_dir: str,
    ) -> bool:
        for raw_symbol in symbols:
            symbol = self.normalize_symbol(raw_symbol.upper().strip())
            for quarter_label in quarter_labels:
                label = quarter_label.strip().upper()
                if not find_cached_filing_file(output_dir, symbol, label, "XBRL"):
                    return False
        return True

    def needs_nse_access(
        self,
        symbols: list[str],
        quarter_labels: list[str],
        output_dir: str,
    ) -> bool:
        """True if any primary or standalone companion download may hit NSE."""
        for raw_symbol in symbols:
            symbol = self.normalize_symbol(raw_symbol.upper().strip())
            for quarter_label in quarter_labels:
                label = quarter_label.strip().upper()
                cached_path = find_cached_filing_file(
                    output_dir, symbol, label, "XBRL"
                )
                if not cached_path:
                    return True
                if self.skip_standalone_companion:
                    continue
                metadata = extract_filing_metadata(cached_path)
                nature = metadata.nature.strip().lower()
                if "consolidated" in nature and "standalone" not in nature:
                    if not find_cached_filing_file(
                        output_dir, symbol, label, "STANDALONE"
                    ):
                        notfound_path = os.path.join(
                            output_dir,
                            f"{symbol}_{label}_STANDALONE.notfound",
                        )
                        if not os.path.exists(notfound_path):
                            return True
        return False

    def ensure_download_session(
        self,
        symbols: list[str],
        quarter_labels: list[str],
        output_dir: str,
    ) -> None:
        """Initialize NSE session only when network access may be required."""
        if self.needs_nse_access(symbols, quarter_labels, output_dir):
            self.initialize_session()

    def _result_from_cached_file(
        self,
        requested_symbol: str,
        symbol: str,
        target_period: str,
        quarter_label: str,
        file_path: str,
    ) -> DownloadResult:
        file_basis = "unknown"
        metadata = extract_filing_metadata(file_path)
        nature = metadata.nature.strip().lower()
        if "consolidated" in nature and "standalone" not in nature:
            file_basis = "consolidated"
        elif "standalone" in nature:
            file_basis = "standalone"
        return DownloadResult(
            requested_symbol,
            target_period,
            "DOWNLOADED",
            file_path,
            f"OK (cached locally, symbol: {symbol})",
            "cached",
            file_basis,
            quarter_label,
        )

    def _ensure_standalone_companion(
        self,
        symbol: str,
        target_period: str,
        quarter_label: str,
        output_dir: str,
        file_basis: str,
    ) -> None:
        with time_block(
            category="Companion Standalone Check",
            detail=f"symbol={symbol}, quarter={quarter_label}",
        ):
            if self.skip_standalone_companion or file_basis != "consolidated":
                return
            standalone_path = find_cached_filing_file(
                output_dir, symbol, quarter_label, "STANDALONE"
            )
            if standalone_path:
                return

            notfound_path = os.path.join(
                output_dir,
                f"{symbol}_{quarter_label}_STANDALONE.notfound",
            )
            if os.path.exists(notfound_path):
                return

            standalone_filing, _src, _err = self.resolve_filing_with_fallback(
                symbol,
                target_period,
                prefer_standalone=True,
            )
            if not standalone_filing:
                with open(notfound_path, "w", encoding="utf-8") as target:
                    target.write(
                        f"Not found on NSE at {dt.datetime.now().isoformat()}"
                    )
                print(
                    f"[+] Marked standalone companion for {symbol} "
                    f"({quarter_label}) as NOT_FOUND locally."
                )
                return

            standalone_basis = self._infer_filing_basis(standalone_filing)
            standalone_url = self._resolve_xbrl_url(standalone_filing)
            if standalone_basis != "standalone" or not standalone_url:
                with open(notfound_path, "w", encoding="utf-8") as target:
                    target.write(
                        "Not standalone basis or URL missing at "
                        f"{dt.datetime.now().isoformat()}"
                    )
                print(
                    f"[+] Marked standalone companion for {symbol} "
                    f"({quarter_label}) as NOT_FOUND locally."
                )
                return

            if not standalone_url.startswith("http"):
                standalone_url = self.base_url + standalone_url
            standalone_path = os.path.join(
                output_dir,
                build_standalone_file_name(symbol, quarter_label, standalone_url),
            )
            if not os.path.exists(standalone_path):
                download_err = self._download_file(standalone_url, standalone_path)
                if download_err:
                    print(
                        f"[!] Failed to download standalone companion: {download_err}"
                    )
                    if "404" in download_err:
                        with open(notfound_path, "w", encoding="utf-8") as target:
                            target.write(
                                f"Download returned 404 at "
                                f"{dt.datetime.now().isoformat()}"
                            )
                else:
                    print(
                        f"[+] Standalone companion downloaded and cached: "
                        f"{standalone_path}"
                    )

    @staticmethod
    def _xbrl_url_variants(xbrl_url: str) -> list[str]:
        variants = [xbrl_url.strip()]
        if "_WEB.xml" in xbrl_url:
            variants.append(xbrl_url.replace("_WEB.xml", ".xml"))
        deduped: list[str] = []
        seen: set[str] = set()
        for variant in variants:
            if variant and variant not in seen:
                seen.add(variant)
                deduped.append(variant)
        return deduped

    def _message_with_bse_pdf_note(
        self,
        symbol: str,
        target_period: str,
        message: str,
    ) -> str:
        note = bse_pdf_only_note(symbol, target_period)
        if not note:
            return message
        return f"{message} | {note}"

    def _download_file_once(self, xbrl_url: str, output_path: str) -> str:
        with self.api_get(xbrl_url, stream=True) as response:
            if response.status_code != 200:
                return f"HTTP {response.status_code}"
            with open(output_path, "wb") as output_file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        output_file.write(chunk)
        return ""

    def _download_file(self, xbrl_url: str, output_path: str) -> str:
        last_error = "HTTP unknown"
        for candidate_url in self._xbrl_url_variants(xbrl_url):
            download_error = self._download_file_once(candidate_url, output_path)
            if not download_error:
                return ""
            last_error = download_error
            if download_error != "HTTP 404":
                return download_error
        return last_error

    def download_for_symbols(
        self,
        symbols: list[str],
        quarter: str,
        output_dir: str,
    ) -> list[DownloadResult]:
        quarter_label = quarter.strip().upper()
        target_period = resolve_target_period(quarter_label)
        os.makedirs(output_dir, exist_ok=True)
        print(f"[*] Target filing period resolved to: {target_period}")

        results: list[DownloadResult] = []
        total = len(symbols)
        for index, raw_symbol in enumerate(symbols, start=1):
            requested_symbol = raw_symbol.upper().strip()
            symbol = self.normalize_symbol(requested_symbol)
            if symbol != requested_symbol:
                print(
                    f"[*] [{index}/{total}] Processing {requested_symbol} "
                    f"(resolved to {symbol})..."
                )
            else:
                print(f"[*] [{index}/{total}] Processing {symbol}...")

            try:
                cached_path = find_cached_filing_file(
                    output_dir, symbol, quarter_label, "XBRL"
                )
                if cached_path:
                    print(
                        f"[+] Using cached XBRL for {symbol} "
                        f"({quarter_label}): {cached_path}"
                    )
                    cached_result = self._result_from_cached_file(
                        requested_symbol,
                        symbol,
                        target_period,
                        quarter_label,
                        cached_path,
                    )
                    results.append(cached_result)
                    self._ensure_standalone_companion(
                        symbol,
                        target_period,
                        quarter_label,
                        output_dir,
                        cached_result.filing_basis,
                    )
                    continue

                candidates, resolve_error = (
                    self.resolve_filing_candidates_with_fallback(
                        symbol,
                        target_period,
                    )
                )
                if not candidates:
                    results.append(
                        DownloadResult(
                            requested_symbol,
                            target_period,
                            "NOT_FOUND",
                            "-",
                            self._message_with_bse_pdf_note(
                                symbol,
                                target_period,
                                (
                                    f"No XBRL filing for requested period under "
                                    f"symbol '{symbol}' ({resolve_error})"
                                ),
                            ),
                            "-",
                            "unknown",
                            quarter_label,
                        )
                    )
                    time.sleep(self.delay_seconds)
                    continue

                downloaded = False
                last_download_error = ""
                last_source = "-"
                filing_basis = "unknown"
                source = "-"
                file_path = "-"

                for candidate_filing, candidate_source in candidates:
                    last_source = candidate_source
                    xbrl_url = self._resolve_xbrl_url(candidate_filing)
                    if not xbrl_url:
                        continue
                    if not xbrl_url.startswith("http"):
                        xbrl_url = self.base_url + xbrl_url

                    filename = build_xbrl_file_name(
                        symbol,
                        quarter_label,
                        xbrl_url,
                    )
                    candidate_path = os.path.join(output_dir, filename)
                    download_error = self._download_file(xbrl_url, candidate_path)
                    if download_error:
                        last_download_error = download_error
                        if os.path.exists(candidate_path):
                            os.remove(candidate_path)
                        continue

                    source = candidate_source
                    file_path = candidate_path
                    filing_basis = self._infer_filing_basis(candidate_filing)
                    downloaded = True
                    break

                if not downloaded:
                    status = "FAILED" if last_download_error else "NOT_FOUND"
                    if last_download_error == "HTTP 404" and len(candidates) > 1:
                        message = (
                            f"All {len(candidates)} NSE archive URLs returned "
                            f"HTTP 404 for symbol '{symbol}'"
                        )
                    elif last_download_error:
                        message = (
                            f"Download {last_download_error} for symbol "
                            f"'{symbol}' via {last_source}"
                        )
                    else:
                        message = f"XBRL attachment missing for symbol '{symbol}'"
                    results.append(
                        DownloadResult(
                            requested_symbol,
                            target_period,
                            status,
                            "-",
                            self._message_with_bse_pdf_note(
                                symbol, target_period, message
                            ),
                            last_source,
                            filing_basis,
                            quarter_label,
                        )
                    )
                    time.sleep(self.delay_seconds)
                    continue

                file_basis = filing_basis
                metadata = extract_filing_metadata(file_path)
                nature = metadata.nature.strip().lower()
                if "consolidated" in nature and "standalone" not in nature:
                    file_basis = "consolidated"
                elif "standalone" in nature:
                    file_basis = "standalone"
                results.append(
                    DownloadResult(
                        requested_symbol,
                        target_period,
                        "DOWNLOADED",
                        file_path,
                        f"OK (source symbol: {symbol}, endpoint: {source})",
                        source,
                        file_basis,
                        quarter_label,
                    )
                )
                if file_basis == "consolidated":
                    self._ensure_standalone_companion(
                        symbol,
                        target_period,
                        quarter_label,
                        output_dir,
                        file_basis,
                    )
                time.sleep(self.delay_seconds)
                continue

            except Exception as exc:
                results.append(
                    DownloadResult(
                        requested_symbol,
                        target_period,
                        "ERROR",
                        "-",
                        str(exc),
                        "-",
                        "unknown",
                        quarter_label,
                    )
                )

            time.sleep(self.delay_seconds)

        return results
