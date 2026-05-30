"""NSE market data helpers for P/E and dividend metrics.

Trailing EPS is built from cached quarterly XBRL files in the same folder.
Share price and dividend announcements are fetched from NSE APIs using the
downloader's authenticated session.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import TYPE_CHECKING

import requests

from fin_reporter.metrics import build_metrics_from_file
from fin_reporter.period_resolver import (
    find_symbol_quarter_file,
    previous_quarter_code,
    quarter_code_from_file_path,
    quarter_start_date,
)
from fin_reporter.xbrl_parser import format_metric

if TYPE_CHECKING:
    from fin_reporter.downloader import NSEXBRLDownloader

_TRAILING_EPS_QUARTERS = 4
_DIVIDEND_EXDATE_GRACE_DAYS = 90
_DIVIDEND_AMOUNT_RE = re.compile(
    r"dividend[^0-9]*(?:rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*"
    r"(?:per\s*share|per\s*sh|/-)",
    re.IGNORECASE,
)


def _parse_nse_date(raw_value: str | None) -> dt.date | None:
    if not raw_value or str(raw_value).strip() in ("-", ""):
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return dt.datetime.strptime(str(raw_value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _quarter_exdate_window(period_end: dt.date) -> tuple[dt.date, dt.date] | None:
    """Inclusive ex-date window for dividends announced with the quarter's results."""
    start = quarter_start_date(period_end)
    if start is None:
        return None
    end = period_end + dt.timedelta(days=_DIVIDEND_EXDATE_GRACE_DAYS)
    return start, end


def trailing_basic_eps_sum(
    file_path: str,
    target_period: str,
    ebitda_definition: str = "tickertape",
) -> float | None:
    """Sum basic EPS for the report quarter and the prior three quarters."""
    quarter_code = quarter_code_from_file_path(file_path)
    if not quarter_code:
        return None

    total = 0.0
    found = 0
    current_code: str | None = quarter_code
    for _ in range(_TRAILING_EPS_QUARTERS):
        if not current_code:
            break
        quarter_file = find_symbol_quarter_file(file_path, current_code)
        if not quarter_file:
            break
        period = _period_for_quarter_code(current_code)
        if not period:
            break
        metrics = build_metrics_from_file(
            quarter_file,
            period,
            ebitda_definition=ebitda_definition,
        )
        if metrics.basic_eps is None:
            break
        total += metrics.basic_eps
        found += 1
        current_code = previous_quarter_code(current_code)

    if found < _TRAILING_EPS_QUARTERS:
        return None
    return total


def _period_for_quarter_code(quarter_code: str) -> str | None:
    match = re.fullmatch(r"Q([1-4])_FY(\d{2}|\d{4})", quarter_code)
    if not match:
        return None
    quarter_num = int(match.group(1))
    fy_token = match.group(2)
    fy_end_year = 2000 + int(fy_token) if len(fy_token) == 2 else int(fy_token)
    pre_year = fy_end_year - 1
    if quarter_num == 1:
        return dt.date(pre_year, 6, 30).strftime("%d-%b-%Y")
    if quarter_num == 2:
        return dt.date(pre_year, 9, 30).strftime("%d-%b-%Y")
    if quarter_num == 3:
        return dt.date(pre_year, 12, 31).strftime("%d-%b-%Y")
    return dt.date(fy_end_year, 3, 31).strftime("%d-%b-%Y")


def _parse_dividend_per_share(subject: str) -> float | None:
    if not subject or "bonus" in subject.lower():
        return None
    if "dividend" not in subject.lower():
        return None
    match = _DIVIDEND_AMOUNT_RE.search(subject)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def fetch_quarter_dividend_per_share(
    downloader: NSEXBRLDownloader,
    symbol: str,
    period_end: dt.date,
) -> float | None:
    """Sum per-share dividends announced for the reporting quarter (NSE ex-date window)."""
    window = _quarter_exdate_window(period_end)
    if window is None:
        return None
    window_start, window_end = window

    downloader.ensure_api_session()
    url = "https://www.nseindia.com/api/corporates-corporateActions"
    params = {"index": "equities", "symbol": symbol}
    response = downloader._api_get(url, params=params)
    if response.status_code != 200:
        return None

    try:
        rows = response.json()
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None

    total = 0.0
    found = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        ex_date = _parse_nse_date(row.get("exDate"))
        if ex_date is None or ex_date < window_start or ex_date > window_end:
            continue
        amount = _parse_dividend_per_share(str(row.get("subject", "")))
        if amount is None:
            continue
        total += amount
        found = True

    return total if found else None


def fetch_share_price_on_date(
    downloader: NSEXBRLDownloader,
    symbol: str,
    as_of_date: dt.date,
) -> float | None:
    """Return NSE EQ close on ``as_of_date``, or the last trading day on/before it."""
    downloader.ensure_api_session()
    lookback_start = as_of_date - dt.timedelta(days=14)
    from_str = lookback_start.strftime("%d-%m-%Y")
    to_str = as_of_date.strftime("%d-%m-%Y")
    url = (
        "https://www.nseindia.com/api/historical/cm/equity"
        f"?symbol={symbol}&series=[%22EQ%22]&from={from_str}&to={to_str}"
    )
    referer = (
        f"https://www.nseindia.com/get-quotes/equity?symbol={symbol}"
    )
    headers = {**downloader.headers, "Referer": referer}
    downloader.session.get(referer, headers=downloader.page_headers, timeout=downloader.timeout)
    response = downloader.session.get(
        url,
        headers=headers,
        timeout=downloader.timeout,
    )
    if response.status_code != 200:
        return _fetch_share_price_yahoo(symbol, as_of_date)

    try:
        payload = response.json()
    except ValueError:
        return _fetch_share_price_yahoo(symbol, as_of_date)
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not rows:
        return _fetch_share_price_yahoo(symbol, as_of_date)

    best_date: dt.date | None = None
    best_close: float | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        trade_date = _parse_nse_date(row.get("CH_TIMESTAMP") or row.get("TIMESTAMP"))
        if trade_date is None or trade_date > as_of_date:
            continue
        close_raw = row.get("CH_CLOSING_PRICE") or row.get("CLOSE_PRICE")
        if close_raw is None:
            continue
        try:
            close_price = float(str(close_raw).replace(",", ""))
        except ValueError:
            continue
        if best_date is None or trade_date >= best_date:
            best_date = trade_date
            best_close = close_price

    nse_close = best_close
    if nse_close is not None:
        return nse_close
    return _fetch_share_price_yahoo(symbol, as_of_date)


def _fetch_share_price_yahoo(symbol: str, as_of_date: dt.date) -> float | None:
    """Fallback: Yahoo Finance daily close for ``SYMBOL.NS`` on or before ``as_of_date``."""
    yahoo_symbol = f"{symbol}.NS"
    lookback_start = as_of_date - dt.timedelta(days=21)
    period1 = int(
        dt.datetime.combine(lookback_start, dt.time.min).timestamp()
    )
    period2 = int(
        dt.datetime.combine(as_of_date, dt.time(23, 59, 59)).timestamp()
    )
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{yahoo_symbol}?period1={period1}&period2={period2}&interval=1d"
    )
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None

    try:
        payload = response.json()
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError, ValueError):
        return None

    best_date: dt.date | None = None
    best_close: float | None = None
    for raw_ts, close in zip(timestamps, closes):
        if close is None:
            continue
        trade_date = dt.datetime.fromtimestamp(raw_ts).date()
        if trade_date > as_of_date:
            continue
        if best_date is None or trade_date >= best_date:
            best_date = trade_date
            best_close = float(close)

    return best_close


def compute_pe_ratio(
    share_price: float | None,
    trailing_eps: float | None,
) -> float | None:
    """P/E = share price / sum of last four quarters' basic EPS."""
    if share_price is None or trailing_eps is None or trailing_eps <= 0:
        return None
    return share_price / trailing_eps


def format_pe_ratio(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def format_dividend_per_share(value: float | None) -> str:
    return format_metric(value)
