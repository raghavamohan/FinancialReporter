"""NSE market data helpers for P/E and dividend metrics.

Trailing EPS is built from cached quarterly XBRL files in the same folder.
Share price and dividend announcements are fetched from NSE APIs using the
downloader's authenticated session.
"""

from __future__ import annotations

import datetime as dt
import json
import os
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
from fin_reporter.timing import time_block

if TYPE_CHECKING:
    from fin_reporter.downloader import NSEXBRLDownloader

_TRAILING_EPS_QUARTERS = 4
_DIVIDEND_AMOUNT_RE = re.compile(
    r"dividend[^0-9]*(?:rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*"
    r"(?:per\s*share|per\s*sh|/-)",
    re.IGNORECASE,
)
_BONUS_RATIO_RE = re.compile(
    r"bonus\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_FACE_VALUE_SPLIT_RE = re.compile(
    r"from\s*rs\.?\s*([\d,]+(?:\.\d+)?)\s*.*?\bto\s*rs\.?\s*([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
_CORPORATE_ACTIONS_CACHE_DIR = ".market_cache"


def _parse_nse_date(raw_value: str | None) -> dt.date | None:
    if not raw_value or str(raw_value).strip() in ("-", ""):
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return dt.datetime.strptime(str(raw_value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _corporate_actions_cache_path(cache_dir: str, symbol: str) -> str:
    safe_symbol = symbol.upper().strip()
    cache_root = os.path.join(cache_dir, _CORPORATE_ACTIONS_CACHE_DIR)
    os.makedirs(cache_root, exist_ok=True)
    return os.path.join(cache_root, f"{safe_symbol}_corporate_actions.json")


def _load_cached_corporate_actions(
    cache_dir: str,
    symbol: str,
) -> list[dict] | None:
    path = _corporate_actions_cache_path(cache_dir, symbol)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as source:
            payload = json.load(source)
    except (OSError, ValueError):
        return None
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    return [row for row in rows if isinstance(row, dict)]


def corporate_actions_cache_exists(cache_dir: str, symbol: str) -> bool:
    """Return True when a non-empty corporate-actions cache file exists."""
    rows = _load_cached_corporate_actions(cache_dir, symbol)
    return bool(rows)


def _save_corporate_actions_cache(
    cache_dir: str,
    symbol: str,
    rows: list[dict],
) -> None:
    path = _corporate_actions_cache_path(cache_dir, symbol)
    payload = {
        "symbol": symbol.upper().strip(),
        "saved_at_utc": dt.datetime.utcnow().isoformat(timespec="seconds"),
        "rows": rows,
    }
    try:
        with open(path, "w", encoding="utf-8") as target:
            json.dump(payload, target, ensure_ascii=True)
    except OSError:
        # Cache failure should not block metric computation.
        return


def fetch_corporate_actions_rows(
    downloader: NSEXBRLDownloader,
    symbol: str,
    cache_dir: str | None = None,
    *,
    force_refresh: bool = False,
) -> list[dict]:
    """Fetch dividend and other corporate actions from cache first, then NSE."""
    with time_block(category="Corporate Actions Fetch", detail=f"symbol={symbol}"):
        cached_rows: list[dict] | None = None
        if cache_dir and not force_refresh:
            cached_rows = _load_cached_corporate_actions(cache_dir, symbol)
            if cached_rows is not None:
                # Log hit
                return cached_rows

        downloader.ensure_api_session()
        url = "https://www.nseindia.com/api/corporates-corporateActions"
        params = {"index": "equities", "symbol": symbol}
        response = downloader._api_get(url, params=params)
        if response.status_code != 200:
            return cached_rows or []

        try:
            rows = response.json()
        except ValueError:
            return cached_rows or []
        if not isinstance(rows, list):
            return cached_rows or []

        normalized_rows = [row for row in rows if isinstance(row, dict)]
        if cache_dir:
            # Save even if it is an empty list [] to record that there are no corporate actions!
            _save_corporate_actions_cache(cache_dir, symbol, normalized_rows)
        return normalized_rows if normalized_rows else (cached_rows or [])


def _quarter_exdate_window(period_end: dt.date) -> tuple[dt.date, dt.date] | None:
    """Inclusive ex-date window constrained to the reporting quarter only."""
    start = quarter_start_date(period_end)
    if start is None:
        return None
    end = period_end
    return start, end


def _parse_bonus_eps_factor(subject: str) -> float | None:
    """Return EPS multiplier after a bonus issue (e.g. 1:1 → 0.5)."""
    match = _BONUS_RATIO_RE.search(subject)
    if not match:
        return None
    try:
        bonus_shares = float(match.group(1).replace(",", ""))
        held_shares = float(match.group(2).replace(",", ""))
    except ValueError:
        return None
    if bonus_shares <= 0 or held_shares <= 0:
        return None
    return held_shares / (held_shares + bonus_shares)


def _parse_face_value_split_eps_factor(subject: str) -> float | None:
    """Return EPS multiplier after a sub-division / face-value split."""
    if "split" not in subject.lower() and "sub-division" not in subject.lower():
        return None
    match = _FACE_VALUE_SPLIT_RE.search(subject)
    if not match:
        return None
    try:
        from_fv = float(match.group(1).replace(",", ""))
        to_fv = float(match.group(2).replace(",", ""))
    except ValueError:
        return None
    if from_fv <= 0 or to_fv <= 0 or from_fv == to_fv:
        return None
    return to_fv / from_fv


def _parse_eps_adjustment_factor(subject: str) -> float | None:
    """Parse one corporate-action subject into an EPS multiplier (<1 for bonus/split)."""
    if "dividend" in subject.lower() and "bonus" not in subject.lower():
        return None
    bonus_factor = _parse_bonus_eps_factor(subject)
    if bonus_factor is not None:
        return bonus_factor
    return _parse_face_value_split_eps_factor(subject)


def fetch_share_restructuring_events(
    downloader: NSEXBRLDownloader,
    symbol: str,
    cache_dir: str | None = None,
) -> list[tuple[dt.date, float]]:
    """Return (ex-date, EPS factor) for bonus and face-value split actions, oldest first."""
    rows = fetch_corporate_actions_rows(
        downloader,
        symbol,
        cache_dir=cache_dir,
    )
    if not rows:
        return []

    events: list[tuple[dt.date, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ex_date = _parse_nse_date(row.get("exDate"))
        factor = _parse_eps_adjustment_factor(str(row.get("subject", "")))
        if ex_date is None or factor is None:
            continue
        events.append((ex_date, factor))

    events.sort(key=lambda item: item[0])
    return events


def corporate_action_factor_between(
    from_date: dt.date,
    to_date: dt.date,
    events: list[tuple[dt.date, float]],
) -> float:
    """Cumulative EPS/price multiplier for bonus/split ex-dates in (from_date, to_date]."""
    factor = 1.0
    for ex_date, event_factor in events:
        if from_date < ex_date <= to_date:
            factor *= event_factor
    return factor


def eps_adjustment_factor_to_report_date(
    quarter_end: dt.date,
    report_date: dt.date,
    events: list[tuple[dt.date, float]],
) -> float:
    """Scale a quarter's EPS to the share count basis as of ``report_date``."""
    return corporate_action_factor_between(quarter_end, report_date, events)


def trailing_basic_eps_sum(
    file_path: str,
    target_period: str,
    ebitda_definition: str = "include-other-income",
    restructuring_events: list[tuple[dt.date, float]] | None = None,
    report_date: dt.date | None = None,
    eps_anchor_date: dt.date | None = None,
) -> float | None:
    """Sum basic EPS for the report quarter and the prior three quarters.

    When ``restructuring_events`` is supplied, older-quarter EPS is adjusted for
    bonus/split ex-dates after that quarter ended through ``eps_anchor_date`` (or
    ``report_date`` when no anchor is set).
    """
    quarter_code = quarter_code_from_file_path(file_path)
    if not quarter_code:
        return None

    if report_date is None:
        try:
            report_date = dt.datetime.strptime(target_period, "%d-%b-%Y").date()
        except ValueError:
            report_date = None

    eps_as_of = eps_anchor_date or report_date

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
        quarter_eps = metrics.basic_eps
        if restructuring_events and eps_as_of is not None:
            try:
                quarter_end = dt.datetime.strptime(period, "%d-%b-%Y").date()
            except ValueError:
                quarter_end = None
            if quarter_end is not None:
                adjust = eps_adjustment_factor_to_report_date(
                    quarter_end,
                    eps_as_of,
                    restructuring_events,
                )
                quarter_eps *= adjust
        total += quarter_eps
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


def _is_dividend_subject(subject: str) -> bool:
    return "dividend" in subject.lower()


def summarize_corporate_actions(rows: list[dict]) -> dict[str, int]:
    """Count dividend vs other corporate-action rows in a cache payload."""
    dividend_rows = 0
    other_rows = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        subject = str(row.get("subject", "")).strip()
        if not subject:
            continue
        if _is_dividend_subject(subject):
            dividend_rows += 1
        else:
            other_rows += 1
    return {
        "total": len(rows),
        "dividend": dividend_rows,
        "other": other_rows,
    }


def _normalize_action_subject(subject: str) -> str:
    return " ".join(subject.split())


def fetch_other_corporate_actions_summary(
    downloader: NSEXBRLDownloader,
    symbol: str,
    period_end: dt.date,
    cache_dir: str | None = None,
) -> str | None:
    """Summarize non-dividend corporate actions for the reporting quarter window."""
    window = _quarter_exdate_window(period_end)
    if window is None:
        return None
    window_start, window_end = window

    rows = fetch_corporate_actions_rows(
        downloader,
        symbol,
        cache_dir=cache_dir,
    )
    if not rows:
        return None

    actions: list[tuple[dt.date, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        ex_date = _parse_nse_date(row.get("exDate"))
        if ex_date is None or ex_date < window_start or ex_date > window_end:
            continue
        subject = _normalize_action_subject(str(row.get("subject", "")))
        if not subject or _is_dividend_subject(subject):
            continue
        dedupe_key = (ex_date.isoformat(), subject.lower())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        actions.append((ex_date, subject))

    if not actions:
        return None
    actions.sort(key=lambda item: item[0])
    return "; ".join(
        f"{item_date.strftime('%d-%b-%Y')}: {item_subject}"
        for item_date, item_subject in actions
    )


def fetch_quarter_dividend_per_share(
    downloader: NSEXBRLDownloader,
    symbol: str,
    period_end: dt.date,
    cache_dir: str | None = None,
) -> float | None:
    """Sum per-share dividends announced for the reporting quarter (NSE ex-date window)."""
    window = _quarter_exdate_window(period_end)
    if window is None:
        return None
    window_start, window_end = window

    rows = fetch_corporate_actions_rows(
        downloader,
        symbol,
        cache_dir=cache_dir,
    )
    if not rows:
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


def _share_prices_cache_path(cache_dir: str, symbol: str) -> str:
    safe_symbol = symbol.upper().strip()
    cache_root = os.path.join(cache_dir, _CORPORATE_ACTIONS_CACHE_DIR)
    os.makedirs(cache_root, exist_ok=True)
    return os.path.join(cache_root, f"{safe_symbol}_share_prices.json")


def _load_cached_share_prices(cache_dir: str, symbol: str) -> dict[str, float] | None:
    path = _share_prices_cache_path(cache_dir, symbol)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as source:
            payload = json.load(source)
        return payload.get("prices", {})
    except (OSError, ValueError):
        return None


def _save_share_prices_cache(cache_dir: str, symbol: str, prices: dict[str, float]) -> None:
    path = _share_prices_cache_path(cache_dir, symbol)
    payload = {
        "symbol": symbol.upper().strip(),
        "saved_at_utc": dt.datetime.utcnow().isoformat(timespec="seconds"),
        "prices": prices,
    }
    try:
        with open(path, "w", encoding="utf-8") as target:
            json.dump(payload, target, ensure_ascii=True)
    except OSError:
        return


def fetch_share_price_on_date(
    downloader: NSEXBRLDownloader,
    symbol: str,
    as_of_date: dt.date,
    restructuring_events: list[tuple[dt.date, float]] | None = None,
    cache_dir: str | None = None,
) -> float | None:
    """Return NSE EQ close on ``as_of_date``, or the last trading day on/before it, using local cache first."""
    date_str = as_of_date.isoformat()
    with time_block(category="Share Price Fetch", detail=f"symbol={symbol}, date={date_str}"):
        cached_prices = None
        if cache_dir:
            cached_prices = _load_cached_share_prices(cache_dir, symbol)
            if cached_prices and date_str in cached_prices:
                val = cached_prices[date_str]
                return val if val != -1.0 else None

        price = _fetch_share_price_raw(downloader, symbol, as_of_date, restructuring_events)
        if cache_dir:
            if cached_prices is None:
                cached_prices = {}
            # Negative caching: store -1.0 if share price could not be fetched
            cached_prices[date_str] = price if price is not None else -1.0
            _save_share_prices_cache(cache_dir, symbol, cached_prices)
        return price


def _fetch_share_price_raw(
    downloader: NSEXBRLDownloader,
    symbol: str,
    as_of_date: dt.date,
    restructuring_events: list[tuple[dt.date, float]] | None = None,
) -> float | None:
    """Return NSE EQ close on ``as_of_date``, or the last trading day on/before it (raw request logic)."""
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
        return _fetch_share_price_yahoo(
            symbol,
            as_of_date,
            restructuring_events=restructuring_events,
        )

    try:
        payload = response.json()
    except ValueError:
        return _fetch_share_price_yahoo(
            symbol,
            as_of_date,
            restructuring_events=restructuring_events,
        )
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not rows:
        return _fetch_share_price_yahoo(
            symbol,
            as_of_date,
            restructuring_events=restructuring_events,
        )

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


def _fetch_share_price_yahoo(
    symbol: str,
    as_of_date: dt.date,
    restructuring_events: list[tuple[dt.date, float]] | None = None,
) -> float | None:
    """Fallback: Yahoo Finance close, converted back to as-of-date share basis.

    Yahoo historical closes are split-adjusted in many cases. When bonus/split
    corporate-action events are available, convert the Yahoo value back to the
    share-count basis prevailing on ``as_of_date``.
    """
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

    if best_close is None:
        return None
    if not restructuring_events:
        return best_close

    latest_event_date = max(ex_date for ex_date, _factor in restructuring_events)
    if latest_event_date <= as_of_date:
        return best_close

    forward_factor = corporate_action_factor_between(
        as_of_date,
        latest_event_date,
        restructuring_events,
    )
    if forward_factor <= 0:
        return best_close
    return best_close / forward_factor


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
