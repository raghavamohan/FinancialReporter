"""NSE integrated and legacy filing resolution helpers."""

from __future__ import annotations

import datetime as dt


class FilingResolverMixin:
    """Mixin for ranking and resolving NSE financial-result filings."""

    integrated_api_url = "https://www.nseindia.com/api/integrated-filing-results"
    legacy_api_url = "https://www.nseindia.com/api/corporates-financial-results"

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

    @staticmethod
    def _is_consolidated_entry(filing: dict) -> bool:
        consolidated_flag = str(filing.get("consolidated", "")).strip().lower()
        if "consolidated" in consolidated_flag and "non" not in consolidated_flag:
            return True
        filing_type = " ".join(
            str(filing.get(key, "")).lower()
            for key in ("nature", "type", "reportType", "filingType")
        )
        return "consol" in filing_type and "non-consol" not in filing_type

    @staticmethod
    def _is_standalone_entry(filing: dict) -> bool:
        consolidated_flag = str(filing.get("consolidated", "")).strip().lower()
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
        if self._is_consolidated_entry(filing):
            return "consolidated"
        if self._is_standalone_entry(filing):
            return "standalone"
        return "unknown"

    @staticmethod
    def _target_qe_date(target_period: str) -> str:
        parsed = dt.datetime.strptime(target_period, "%d-%b-%Y")
        return parsed.strftime("%d-%b-%Y").upper()

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
                (0 if self._is_standalone_entry(f) else 1)
                if prefer_standalone
                else (0 if self._is_consolidated_entry(f) else 1),
                (0 if self._is_consolidated_entry(f) else 1)
                if prefer_standalone
                else (0 if self._is_standalone_entry(f) else 1),
                -self._sort_timestamp(
                    f.get("broadcast_Date")
                    or f.get("broadCastDate")
                    or f.get("filingDate")
                ),
            ),
        )

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

    def _resolve_integrated_candidates(
        self,
        symbol: str,
        target_period: str,
        from_date: str,
        to_date: str,
        prefer_standalone: bool = False,
        require_financial: bool = True,
    ) -> tuple[list[dict], str]:
        params = {"symbol": symbol, "from_date": from_date, "to_date": to_date}
        response = self.api_get(self.integrated_api_url, params=params)
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
                (0 if self._is_standalone_entry(f) else 1)
                if prefer_standalone
                else (0 if self._is_consolidated_entry(f) else 1),
                (0 if self._is_consolidated_entry(f) else 1)
                if prefer_standalone
                else (0 if self._is_standalone_entry(f) else 1),
                -self._sort_timestamp(f.get("broadcast_Date")),
            ),
        )
        return ranked, ""

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
        response = self.api_get(self.legacy_api_url, params=params)
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

        def _append_candidates(candidates: list[dict], source: str) -> None:
            for filing in candidates:
                xbrl_url = self._resolve_xbrl_url(filing)
                if not xbrl_url or xbrl_url in seen_urls:
                    continue
                seen_urls.add(xbrl_url)
                resolved.append((filing, source))

        for lookup_symbol in symbol_candidates:
            from_date, to_date = self._date_window_for_target(target_period)
            integrated_candidates, integrated_err = self._resolve_integrated_candidates(
                lookup_symbol,
                target_period,
                from_date,
                to_date,
                prefer_standalone=prefer_standalone,
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
                    f"{lookup_symbol}: {joined_errors}"
                    if joined_errors
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
        """Try integrated API first, then fall back to legacy API."""
        candidates, error = self.resolve_filing_candidates_with_fallback(
            symbol,
            target_period,
            prefer_standalone=prefer_standalone,
        )
        if candidates:
            filing, source = candidates[0]
            return filing, source, ""
        return None, "-", error
