"""NSE XBRL filing downloader.

Downloads XBRL financial result filings from NSE (National Stock Exchange
of India) for specified company symbols and quarters. Supports two NSE
API endpoints with automatic fallback:

1. **Integrated Filing API** — newer endpoint for SEBI integrated filings
2. **Legacy Financial Results API** — older endpoint, broader coverage

The downloader manages HTTP sessions with cookie-based authentication
required by NSE's anti-bot protections.
"""

import datetime as dt
import os
import re
import time

import requests  # pyright: ignore[reportMissingModuleSource]

from fin_reporter.bse_fallback import bse_pdf_only_note
from fin_reporter.models import DownloadResult
from fin_reporter.period_resolver import (
    previous_quarter_code,
    previous_quarter_end,
    trailing_eps_support_quarters,
)
from fin_reporter.xbrl_parser import extract_filing_metadata


class NSEXBRLDownloader:
    """Downloads XBRL filings from NSE for given symbols and quarters.

    Usage::

        downloader = NSEXBRLDownloader()
        downloader.initialize_session()
        results = downloader.download_for_symbols(
            ["RELIANCE", "HDFCBANK"],
            "Q4_FY26",
            "./xbrl_downloads",
        )
    """

    def __init__(self, timeout: int = 20, delay_seconds: float = 2):
        self.base_url = "https://www.nseindia.com"
        self.legacy_api_url = (
            "https://www.nseindia.com/api/corporates-financial-results"
        )
        self.integrated_api_url = (
            "https://www.nseindia.com/api/integrated-filing-results"
        )
        self.timeout = timeout
        self.delay_seconds = delay_seconds
        self.session = requests.Session()
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Referer": (
                "https://www.nseindia.com/companies-listing/"
                "corporate-integrated-filing"
            ),
        }
        self.page_headers = {
            **self.headers,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/webp,*/*;q=0.8"
            ),
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        }
        self.max_session_retries = 3
        # Common legacy-to-live NSE symbol aliases.
        self.symbol_aliases = {
            "HDFC": "HDFCBANK",
        }
        # Additional lookup-only fallback symbols for historical filing feeds.
        # Primary symbol naming (cache filenames, user-facing output) stays unchanged.
        self.lookup_symbol_fallbacks = {
            "TATAMOTORS": ("TATAMTRDVR",),
        }

    # ─── Session management ──────────────────────────────────────────

    def ensure_api_session(self) -> None:
        """Initialize the NSE session when API calls are needed but cookies are absent."""
        if self.session.cookies:
            return
        self.initialize_session()

    def initialize_session(self) -> None:
        """Warm up the NSE session by visiting key pages to obtain cookies.

        Raises:
            RuntimeError: If the session cannot be initialized after retries.
        """
        print("[*] Initializing NSE session and cookies...")
        warmup_urls = (
            "https://www.nseindia.com/companies-listing/corporate-integrated-filing",
            "https://www.nseindia.com/companies-listing/corporate-filings-financial-results",
            "https://www.nseindia.com/",
            "https://www.nseindia.com/market-data/live-equity-market",
        )
        last_status = "unknown"
        for attempt in range(1, self.max_session_retries + 1):
            self.session.cookies.clear()
            ok_count = 0
            for url in warmup_urls:
                try:
                    response = self.session.get(
                        url,
                        headers=self.page_headers,
                        timeout=self.timeout,
                    )
                    last_status = str(response.status_code)
                    if response.status_code == 200:
                        ok_count += 1
                    time.sleep(0.75)
                except requests.RequestException:
                    time.sleep(0.75)

            if ok_count >= 1 and self.session.cookies:
                print("[+] NSE session initialized successfully.")
                return

            sleep_time = min(2 * attempt, 6)
            print(
                f"[!] Session warm-up attempt {attempt} blocked. "
                f"Retrying in {sleep_time}s..."
            )
            time.sleep(sleep_time)

        raise RuntimeError(
            f"Unable to initialize NSE session after retries "
            f"(last HTTP status: {last_status}). "
            "Try again after some time, on a different network, "
            "or with VPN disabled."
        )

    # ─── Period resolution ───────────────────────────────────────────

    @staticmethod
    def _format_period_date(day: int, month: int, year: int) -> str:
        return dt.date(year, month, day).strftime("%d-%b-%Y")

    def resolve_target_period(self, quarter: str) -> str:
        """Convert a quarter code (e.g. ``Q4_FY26``) to a period date string.

        Also accepts a direct date string like ``31-Mar-2026``.
        """
        quarter_token = quarter.strip().upper()
        direct_period_pattern = re.compile(r"^\d{2}-[A-Za-z]{3}-\d{4}$")
        if direct_period_pattern.match(quarter_token):
            parsed = dt.datetime.strptime(quarter_token, "%d-%b-%Y")
            return parsed.strftime("%d-%b-%Y")

        match = re.fullmatch(r"Q([1-4])_FY(\d{2}|\d{4})", quarter_token)
        if not match:
            raise ValueError(
                "Quarter must be like Q1_FY26 or a date like 30-Jun-2025."
            )

        quarter_num = int(match.group(1))
        fy_token = match.group(2)
        fy_end_year = (
            2000 + int(fy_token) if len(fy_token) == 2 else int(fy_token)
        )
        pre_year = fy_end_year - 1

        if quarter_num == 1:
            return self._format_period_date(30, 6, pre_year)
        if quarter_num == 2:
            return self._format_period_date(30, 9, pre_year)
        if quarter_num == 3:
            return self._format_period_date(31, 12, pre_year)
        return self._format_period_date(31, 3, fy_end_year)

    def resolve_quarter_sequence(
        self,
        quarter: str,
        back_quarters: int,
    ) -> list[str]:
        """Build a quarter list from selected quarter backwards.

        The returned list is ordered from newest to oldest and always includes
        the selected quarter as the first element.
        """
        if back_quarters < 1:
            raise ValueError("back_quarters must be at least 1.")

        selected = quarter.strip().upper()
        quarter_pattern = re.compile(r"^Q[1-4]_FY(\d{2}|\d{4})$")
        is_quarter_code = quarter_pattern.fullmatch(selected) is not None

        if is_quarter_code:
            sequence: list[str] = []
            current = selected
            for _ in range(back_quarters):
                sequence.append(current)
                previous = previous_quarter_code(current)
                if previous is None:
                    break
                current = previous
            return sequence

        period = self.resolve_target_period(selected)
        current_date = dt.datetime.strptime(period, "%d-%b-%Y").date()
        sequence = []
        for _ in range(back_quarters):
            sequence.append(current_date.strftime("%d-%b-%Y"))
            previous_date = previous_quarter_end(current_date)
            if previous_date is None:
                break
            current_date = previous_date
        return sequence

    def resolve_display_and_download_quarters(
        self,
        quarter: str,
        back_quarters: int,
    ) -> tuple[list[str], list[str]]:
        """Return (display_quarters, download_quarters) for CLI runs.

        ``download_quarters`` includes ``display_quarters`` plus up to three
        earlier quarters required for trailing EPS / P/E on the oldest column.
        """
        display_quarters = self.resolve_quarter_sequence(quarter, back_quarters)
        support_quarters = trailing_eps_support_quarters(display_quarters)
        download_quarters: list[str] = []
        seen: set[str] = set()
        for label in display_quarters + support_quarters:
            key = label.strip().upper()
            if key in seen:
                continue
            seen.add(key)
            download_quarters.append(label)
        return display_quarters, download_quarters

    # ─── Filing extraction helpers ───────────────────────────────────

    def _extract_filings(self, response_json) -> list:
        if isinstance(response_json, list):
            return response_json
        if isinstance(response_json, dict):
            if isinstance(response_json.get("data"), list):
                return response_json["data"]
            if isinstance(response_json.get("records"), list):
                return response_json["records"]
        return []

    @staticmethod
    def _resolve_xbrl_url(filing: dict) -> str | None:
        xbrl_url = (
            filing.get("xbrlAttachment")
            or filing.get("attachment")
            or filing.get("xbrl")
            or filing.get("ixbrl")
        )
        if xbrl_url in ("", "-", None):
            return None
        return str(xbrl_url).strip()

    @staticmethod
    def _parse_nse_datetime(raw_datetime) -> dt.datetime | None:
        if not raw_datetime:
            return None
        for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
            try:
                return dt.datetime.strptime(str(raw_datetime).strip(), fmt)
            except ValueError:
                continue
        return None

    def _sort_timestamp(self, raw_datetime) -> float:
        parsed = self._parse_nse_datetime(raw_datetime)
        if not parsed:
            return 0.0
        return parsed.timestamp()

    # ─── Filing type classification ──────────────────────────────────

    @staticmethod
    def _is_consolidated_entry(filing: dict) -> bool:
        consolidated_flag = (
            str(filing.get("consolidated", "")).strip().lower()
        )
        if (
            "consolidated" in consolidated_flag
            and "non" not in consolidated_flag
        ):
            return True
        filing_type = " ".join(
            str(filing.get(key, "")).lower()
            for key in ("nature", "type", "reportType", "filingType")
        )
        return "consol" in filing_type and "non-consol" not in filing_type

    @staticmethod
    def _is_standalone_entry(filing: dict) -> bool:
        consolidated_flag = (
            str(filing.get("consolidated", "")).strip().lower()
        )
        if "standalone" in consolidated_flag:
            return True
        filing_type = " ".join(
            str(filing.get(key, "")).lower()
            for key in ("nature", "type", "reportType", "filingType")
        )
        return "standalone" in filing_type

    @staticmethod
    def _is_financial_integrated_record(filing: dict) -> bool:
        filing_type = str(filing.get("type", "")).lower()
        return "integrated filing" in filing_type and "financial" in filing_type

    def _infer_filing_basis(self, filing: dict) -> str:
        """Infer filing basis from NSE API metadata."""
        if self._is_consolidated_entry(filing):
            return "consolidated"
        if self._is_standalone_entry(filing):
            return "standalone"
        return "unknown"

    @staticmethod
    def _target_qe_date(target_period: str) -> str:
        parsed = dt.datetime.strptime(target_period, "%d-%b-%Y")
        return parsed.strftime("%d-%b-%Y").upper()

    # ─── Filing matching ─────────────────────────────────────────────

    def _rank_matching_filings(
        self,
        filings: list,
        target_period: str,
        prefer_standalone: bool = False,
    ) -> list[dict]:
        candidates = []
        for filing in filings:
            period = str(filing.get("period", "")).strip()
            to_date = str(filing.get("toDate", "")).strip()
            xbrl_url = self._resolve_xbrl_url(filing)
            if (to_date == target_period or period == target_period) and xbrl_url:
                candidates.append(filing)

        if not candidates:
            return []

        return sorted(
            candidates,
            key=lambda f: (
                (
                    0 if self._is_standalone_entry(f) else 1
                ) if prefer_standalone else (
                    0 if self._is_consolidated_entry(f) else 1
                ),
                (
                    0 if self._is_consolidated_entry(f) else 1
                ) if prefer_standalone else (
                    0 if self._is_standalone_entry(f) else 1
                ),
                -self._sort_timestamp(
                    f.get("broadcast_Date")
                    or f.get("broadCastDate")
                    or f.get("filingDate")
                ),
            ),
        )

    def _pick_matching_filing(
        self,
        filings: list,
        target_period: str,
        prefer_standalone: bool = False,
    ) -> dict | None:
        ranked = self._rank_matching_filings(
            filings,
            target_period,
            prefer_standalone=prefer_standalone,
        )
        return ranked[0] if ranked else None

    @staticmethod
    def _is_q4_target(target_period: str) -> bool:
        target_date = dt.datetime.strptime(target_period, "%d-%b-%Y").date()
        return target_date.month == 3 and target_date.day == 31

    @staticmethod
    def _date_window_for_target(
        target_period: str,
        upper_days: int | None = None,
    ) -> tuple[str, str]:
        target_date = dt.datetime.strptime(target_period, "%d-%b-%Y").date()
        from_date = (target_date - dt.timedelta(days=400)).strftime("%d-%m-%Y")
        if upper_days is None:
            upper_days = 90 if (
                target_date.month == 3 and target_date.day == 31
            ) else 45
        to_date = (target_date + dt.timedelta(days=upper_days)).strftime("%d-%m-%Y")
        return from_date, to_date

    def _build_file_name(
        self,
        symbol: str,
        quarter_label: str,
        xbrl_url: str,
    ) -> str:
        extension = ".zip"
        lower_url = xbrl_url.lower()
        if lower_url.endswith(".xml"):
            extension = ".xml"
        elif lower_url.endswith(".xbrl"):
            extension = ".xbrl"
        return f"{symbol}_{quarter_label}_XBRL{extension}"

    def _build_standalone_file_name(
        self,
        symbol: str,
        quarter_label: str,
        xbrl_url: str,
    ) -> str:
        """Build a standalone variant filename."""
        xbrl_file_name = self._build_file_name(symbol, quarter_label, xbrl_url)
        return xbrl_file_name.replace("_XBRL", "_STANDALONE")

    # ─── HTTP helpers ────────────────────────────────────────────────

    def _api_get(self, url: str, params=None, stream: bool = False):
        response = self.session.get(
            url,
            headers=self.headers,
            params=params,
            timeout=self.timeout,
            stream=stream,
        )
        if response.status_code in (401, 403):
            self.initialize_session()
            response = self.session.get(
                url,
                headers=self.headers,
                params=params,
                timeout=self.timeout,
                stream=stream,
            )
        return response

    # ─── Filing resolution (integrated + legacy) ─────────────────────

    def _resolve_integrated_candidates(
        self,
        symbol: str,
        target_period: str,
        from_date: str,
        to_date: str,
        prefer_standalone: bool = False,
        require_financial: bool = True,
    ) -> tuple[list[dict], str]:
        params = {
            "symbol": symbol,
            "from_date": from_date,
            "to_date": to_date,
        }
        response = self._api_get(self.integrated_api_url, params=params)
        if response.status_code != 200:
            return [], f"integrated HTTP {response.status_code}"

        filings = self._extract_filings(response.json())
        target_qe_date = self._target_qe_date(target_period)
        candidates = []
        for filing in filings:
            qe_date = str(filing.get("qe_Date", "")).strip().upper()
            if qe_date != target_qe_date:
                continue
            xbrl_url = self._resolve_xbrl_url(filing)
            if not xbrl_url:
                continue
            candidates.append(filing)

        if not candidates:
            return [], "integrated not found"

        if require_financial:
            financial_candidates = [
                f for f in candidates if self._is_financial_integrated_record(f)
            ]
            if not financial_candidates:
                return [], "integrated financial not found"
            candidates = financial_candidates

        ranked = sorted(
            candidates,
            key=lambda f: (
                0 if self._is_financial_integrated_record(f) else 1,
                (
                    0 if self._is_standalone_entry(f) else 1
                ) if prefer_standalone else (
                    0 if self._is_consolidated_entry(f) else 1
                ),
                (
                    0 if self._is_consolidated_entry(f) else 1
                ) if prefer_standalone else (
                    0 if self._is_standalone_entry(f) else 1
                ),
                -self._sort_timestamp(f.get("broadcast_Date")),
            ),
        )
        return ranked, ""

    def _resolve_filing_integrated(
        self,
        symbol: str,
        target_period: str,
        from_date: str,
        to_date: str,
        prefer_standalone: bool = False,
    ) -> tuple[dict | None, str]:
        ranked, error = self._resolve_integrated_candidates(
            symbol,
            target_period,
            from_date,
            to_date,
            prefer_standalone=prefer_standalone,
        )
        if ranked:
            return ranked[0], ""
        return None, error

    def _resolve_legacy_candidates(
        self,
        symbol: str,
        target_period: str,
        from_date: str,
        to_date: str,
        prefer_standalone: bool = False,
        period: str = "Quarterly",
    ) -> tuple[list[dict], str]:
        params = {
            "index": "equities",
            "symbol": symbol,
            "period": period,
            "from_date": from_date,
            "to_date": to_date,
        }
        response = self._api_get(self.legacy_api_url, params=params)
        if response.status_code != 200:
            return [], f"legacy HTTP {response.status_code}"

        filings = self._extract_filings(response.json())
        ranked = self._rank_matching_filings(
            filings,
            target_period,
            prefer_standalone=prefer_standalone,
        )
        if not ranked:
            return [], f"legacy {period.lower()} not found"
        return ranked, ""

    def _resolve_filing_legacy(
        self,
        symbol: str,
        target_period: str,
        from_date: str,
        to_date: str,
        prefer_standalone: bool = False,
        period: str = "Quarterly",
    ) -> tuple[dict | None, str]:
        ranked, error = self._resolve_legacy_candidates(
            symbol,
            target_period,
            from_date,
            to_date,
            prefer_standalone=prefer_standalone,
            period=period,
        )
        if ranked:
            return ranked[0], ""
        return None, error

    def resolve_filing_candidates_with_fallback(
        self,
        symbol: str,
        target_period: str,
        prefer_standalone: bool = False,
    ) -> tuple[list[tuple[dict, str]], str]:
        """Return ranked (filing, source) candidates across lookup symbols."""
        symbol_candidates = self.candidate_lookup_symbols(symbol)
        resolved: list[tuple[dict, str]] = []
        seen_urls: set[str] = set()
        all_errors: list[str] = []

        def _append_candidates(
            candidates: list[dict],
            source: str,
        ) -> None:
            for filing in candidates:
                xbrl_url = self._resolve_xbrl_url(filing)
                if not xbrl_url or xbrl_url in seen_urls:
                    continue
                seen_urls.add(xbrl_url)
                resolved.append((filing, source))

        for lookup_symbol in symbol_candidates:
            from_date, to_date = self._date_window_for_target(target_period)
            integrated_candidates, integrated_err = (
                self._resolve_integrated_candidates(
                    lookup_symbol,
                    target_period,
                    from_date,
                    to_date,
                    prefer_standalone=prefer_standalone,
                )
            )
            _append_candidates(integrated_candidates, "integrated")

            legacy_candidates, legacy_err = self._resolve_legacy_candidates(
                lookup_symbol,
                target_period,
                from_date,
                to_date,
                prefer_standalone=prefer_standalone,
            )
            _append_candidates(legacy_candidates, "legacy")

            broad_from, broad_to = self._date_window_for_target(
                target_period,
                upper_days=180,
            )
            if broad_to != to_date:
                broad_integrated, broad_integrated_err = (
                    self._resolve_integrated_candidates(
                        lookup_symbol,
                        target_period,
                        broad_from,
                        broad_to,
                        prefer_standalone=prefer_standalone,
                    )
                )
                _append_candidates(broad_integrated, "integrated")

                broad_legacy, broad_legacy_err = self._resolve_legacy_candidates(
                    lookup_symbol,
                    target_period,
                    broad_from,
                    broad_to,
                    prefer_standalone=prefer_standalone,
                )
                _append_candidates(broad_legacy, "legacy")
            else:
                broad_integrated_err = ""
                broad_legacy_err = ""

            errors = [integrated_err, legacy_err]
            if broad_integrated_err:
                errors.append(broad_integrated_err)
            if broad_legacy_err:
                errors.append(broad_legacy_err)

            if self._is_q4_target(target_period):
                annual_candidates, annual_err = self._resolve_legacy_candidates(
                    lookup_symbol,
                    target_period,
                    broad_from,
                    broad_to,
                    prefer_standalone=prefer_standalone,
                    period="Annual",
                )
                _append_candidates(annual_candidates, "legacy-annual")
                errors.append(annual_err)

            joined_errors = "; ".join([token for token in errors if token])
            if lookup_symbol != symbol:
                joined_errors = (
                    f"{lookup_symbol}: {joined_errors}" if joined_errors
                    else f"{lookup_symbol}: not found"
                )
            all_errors.append(joined_errors)

        if resolved:
            return resolved, ""
        return [], "; ".join([item for item in all_errors if item])

    def resolve_filing_with_fallback(
        self,
        symbol: str,
        target_period: str,
        prefer_standalone: bool = False,
    ) -> tuple[dict | None, str, str]:
        """Try integrated API first, then fall back to legacy API.

        For Q4 (31-Mar) targets, uses a wider broadcast-date window and may
        retry with an extended window or legacy Annual filings.

        Returns:
            (filing_dict, source, error_message)
        """
        candidates, error = self.resolve_filing_candidates_with_fallback(
            symbol,
            target_period,
            prefer_standalone=prefer_standalone,
        )
        if candidates:
            filing, source = candidates[0]
            return filing, source, ""
        return None, "-", error

    # ─── Symbol helpers ──────────────────────────────────────────────

    def normalize_symbol(self, requested_symbol: str) -> str:
        """Normalize a symbol, applying known aliases (e.g. HDFC → HDFCBANK)."""
        cleaned = requested_symbol.upper().strip()
        return self.symbol_aliases.get(cleaned, cleaned)

    def candidate_lookup_symbols(self, requested_symbol: str) -> list[str]:
        """Return prioritized symbols to query NSE filing APIs."""
        primary = self.normalize_symbol(requested_symbol)
        candidates = [primary]
        for fallback in self.lookup_symbol_fallbacks.get(primary, ()):
            normalized_fallback = self.normalize_symbol(fallback)
            if normalized_fallback not in candidates:
                candidates.append(normalized_fallback)
        return candidates

    # ─── Local cache ─────────────────────────────────────────────────

    @staticmethod
    def _find_cached_filing_file(
        output_dir: str,
        symbol: str,
        quarter_label: str,
        variant: str,
    ) -> str | None:
        """Return a non-empty local XBRL path if already downloaded.

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

    def all_requested_files_cached(
        self,
        symbols: list[str],
        quarter_labels: list[str],
        output_dir: str,
    ) -> bool:
        """True when every symbol/quarter primary XBRL file exists locally."""
        for raw_symbol in symbols:
            symbol = self.normalize_symbol(raw_symbol.upper().strip())
            for quarter_label in quarter_labels:
                label = quarter_label.strip().upper()
                if not self._find_cached_filing_file(
                    output_dir, symbol, label, "XBRL"
                ):
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
                cached_path = self._find_cached_filing_file(
                    output_dir, symbol, label, "XBRL"
                )
                if not cached_path:
                    return True
                metadata = extract_filing_metadata(cached_path)
                nature = metadata.nature.strip().lower()
                if "consolidated" in nature and "standalone" not in nature:
                    if not self._find_cached_filing_file(
                        output_dir, symbol, label, "STANDALONE"
                    ):
                        return True
        return False

    def _result_from_cached_file(
        self,
        requested_symbol: str,
        symbol: str,
        target_period: str,
        quarter_label: str,
        file_path: str,
    ) -> DownloadResult:
        """Build a DownloadResult for a locally cached XBRL file."""
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
        """Download standalone XBRL when consolidated is cached but companion is not."""
        if file_basis != "consolidated":
            return
        standalone_path = self._find_cached_filing_file(
            output_dir, symbol, quarter_label, "STANDALONE"
        )
        if standalone_path:
            return
        standalone_filing, _src, _err = self.resolve_filing_with_fallback(
            symbol,
            target_period,
            prefer_standalone=True,
        )
        if not standalone_filing:
            return
        standalone_basis = self._infer_filing_basis(standalone_filing)
        standalone_url = self._resolve_xbrl_url(standalone_filing)
        if standalone_basis != "standalone" or not standalone_url:
            return
        if not standalone_url.startswith("http"):
            standalone_url = self.base_url + standalone_url
        standalone_path = os.path.join(
            output_dir,
            self._build_standalone_file_name(
                symbol,
                quarter_label,
                standalone_url,
            ),
        )
        if not os.path.exists(standalone_path):
            self._download_file(standalone_url, standalone_path)

    # ─── Download ────────────────────────────────────────────────────

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
        """Append BSE PDF-only disclosure note for failed NSE downloads."""
        note = bse_pdf_only_note(symbol, target_period)
        if not note:
            return message
        return f"{message} | {note}"

    def _download_file_once(self, xbrl_url: str, output_path: str) -> str:
        """Download one URL. Returns error string or empty on success."""
        with self._api_get(xbrl_url, stream=True) as response:
            if response.status_code != 200:
                return f"HTTP {response.status_code}"
            with open(output_path, "wb") as output_file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        output_file.write(chunk)
        return ""

    def _download_file(self, xbrl_url: str, output_path: str) -> str:
        """Download a file from a URL. Returns error string or empty on success."""
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
        """Download XBRL filings for multiple symbols.

        Args:
            symbols: List of NSE symbol strings.
            quarter: Quarter code (e.g. "Q4_FY26") or date string.
            output_dir: Directory to save downloaded files.

        Returns:
            List of DownloadResult for each symbol.
        """
        quarter_label = quarter.strip().upper()
        target_period = self.resolve_target_period(quarter_label)
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
                cached_path = self._find_cached_filing_file(
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
                filing: dict | None = None
                source = "-"
                file_path = "-"
                filing_basis = "unknown"

                for candidate_filing, candidate_source in candidates:
                    last_source = candidate_source
                    xbrl_url = self._resolve_xbrl_url(candidate_filing)
                    if not xbrl_url:
                        continue
                    if not xbrl_url.startswith("http"):
                        xbrl_url = self.base_url + xbrl_url

                    filename = self._build_file_name(
                        symbol,
                        quarter_label,
                        xbrl_url,
                    )
                    candidate_path = os.path.join(output_dir, filename)
                    download_error = self._download_file(
                        xbrl_url,
                        candidate_path,
                    )
                    if download_error:
                        last_download_error = download_error
                        if os.path.exists(candidate_path):
                            os.remove(candidate_path)
                        continue

                    filing = candidate_filing
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
                    message = self._message_with_bse_pdf_note(
                        symbol,
                        target_period,
                        message,
                    )
                    results.append(
                        DownloadResult(
                            requested_symbol,
                            target_period,
                            status,
                            "-",
                            message,
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
                        (
                            f"OK (source symbol: {symbol}, "
                            f"endpoint: {source})"
                        ),
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
