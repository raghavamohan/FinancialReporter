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

from fin_reporter.models import DownloadResult
from fin_reporter.period_resolver import previous_quarter_code, previous_quarter_end
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

    def _pick_matching_filing(
        self,
        filings: list,
        target_period: str,
        prefer_standalone: bool = False,
    ) -> dict | None:
        candidates = []
        for filing in filings:
            period = str(filing.get("period", "")).strip()
            to_date = str(filing.get("toDate", "")).strip()
            xbrl_url = self._resolve_xbrl_url(filing)
            if (to_date == target_period or period == target_period) and xbrl_url:
                candidates.append(filing)

        if not candidates:
            return None

        ranked = sorted(
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
        return ranked[0]

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

    def _resolve_filing_integrated(
        self,
        symbol: str,
        target_period: str,
        from_date: str,
        to_date: str,
        prefer_standalone: bool = False,
    ) -> tuple[dict | None, str]:
        params = {
            "symbol": symbol,
            "from_date": from_date,
            "to_date": to_date,
        }
        response = self._api_get(self.integrated_api_url, params=params)
        if response.status_code != 200:
            return None, f"integrated HTTP {response.status_code}"

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
            return None, "integrated not found"

        financial_candidates = [
            f for f in candidates if self._is_financial_integrated_record(f)
        ]
        if not financial_candidates:
            return None, "integrated financial not found"
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
        return ranked[0], ""

    def _resolve_filing_legacy(
        self,
        symbol: str,
        target_period: str,
        from_date: str,
        to_date: str,
        prefer_standalone: bool = False,
        period: str = "Quarterly",
    ) -> tuple[dict | None, str]:
        params = {
            "index": "equities",
            "symbol": symbol,
            "period": period,
            "from_date": from_date,
            "to_date": to_date,
        }
        response = self._api_get(self.legacy_api_url, params=params)
        if response.status_code != 200:
            return None, f"legacy HTTP {response.status_code}"

        filings = self._extract_filings(response.json())
        filing = self._pick_matching_filing(
            filings,
            target_period,
            prefer_standalone=prefer_standalone,
        )
        if not filing:
            return None, f"legacy {period.lower()} not found"
        return filing, ""

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
        from_date, to_date = self._date_window_for_target(target_period)
        integrated_filing, integrated_err = self._resolve_filing_integrated(
            symbol,
            target_period,
            from_date,
            to_date,
            prefer_standalone=prefer_standalone,
        )
        if integrated_filing:
            return integrated_filing, "integrated", ""

        legacy_filing, legacy_err = self._resolve_filing_legacy(
            symbol,
            target_period,
            from_date,
            to_date,
            prefer_standalone=prefer_standalone,
        )
        if legacy_filing:
            return legacy_filing, "legacy", ""

        errors = [integrated_err, legacy_err]

        if self._is_q4_target(target_period):
            wide_from, wide_to = self._date_window_for_target(
                target_period,
                upper_days=120,
            )
            if wide_to != to_date:
                integrated_filing, wide_err = self._resolve_filing_integrated(
                    symbol,
                    target_period,
                    wide_from,
                    wide_to,
                    prefer_standalone=prefer_standalone,
                )
                if integrated_filing:
                    return integrated_filing, "integrated", ""
                errors.append(wide_err)

            annual_filing, annual_err = self._resolve_filing_legacy(
                symbol,
                target_period,
                from_date,
                to_date,
                prefer_standalone=prefer_standalone,
                period="Annual",
            )
            if annual_filing:
                return annual_filing, "legacy-annual", ""
            errors.append(annual_err)

        return None, "-", "; ".join(errors)

    # ─── Symbol helpers ──────────────────────────────────────────────

    def normalize_symbol(self, requested_symbol: str) -> str:
        """Normalize a symbol, applying known aliases (e.g. HDFC → HDFCBANK)."""
        cleaned = requested_symbol.upper().strip()
        return self.symbol_aliases.get(cleaned, cleaned)

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

    def _download_file(self, xbrl_url: str, output_path: str) -> str:
        """Download a file from a URL. Returns error string or empty on success."""
        with self._api_get(xbrl_url, stream=True) as response:
            if response.status_code != 200:
                return f"HTTP {response.status_code}"
            with open(output_path, "wb") as output_file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        output_file.write(chunk)
        return ""

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

                filing, source, resolve_error = (
                    self.resolve_filing_with_fallback(symbol, target_period)
                )
                filing_basis = (
                    self._infer_filing_basis(filing) if filing else "unknown"
                )
                if not filing:
                    results.append(
                        DownloadResult(
                            requested_symbol,
                            target_period,
                            "NOT_FOUND",
                            "-",
                            (
                                f"No XBRL filing for requested period under "
                                f"symbol '{symbol}' ({resolve_error})"
                            ),
                            source,
                            filing_basis,
                            quarter_label,
                        )
                    )
                    time.sleep(self.delay_seconds)
                    continue

                xbrl_url = self._resolve_xbrl_url(filing)
                if not xbrl_url:
                    results.append(
                        DownloadResult(
                            requested_symbol,
                            target_period,
                            "NOT_FOUND",
                            "-",
                            f"XBRL attachment missing for symbol '{symbol}'",
                            source,
                            filing_basis,
                            quarter_label,
                        )
                    )
                    time.sleep(self.delay_seconds)
                    continue

                if not xbrl_url.startswith("http"):
                    xbrl_url = self.base_url + xbrl_url

                filename = self._build_file_name(
                    symbol, quarter_label, xbrl_url
                )
                file_path = os.path.join(output_dir, filename)
                download_error = self._download_file(xbrl_url, file_path)
                if download_error:
                    results.append(
                        DownloadResult(
                            requested_symbol,
                            target_period,
                            "FAILED",
                            "-",
                            (
                                f"Download {download_error} for symbol "
                                f"'{symbol}' via {source}"
                            ),
                            source,
                            filing_basis,
                            quarter_label,
                        )
                    )
                else:
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
