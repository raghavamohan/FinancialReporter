"""BSE announcement lookup for failed NSE XBRL downloads.

When NSE financial-results XBRL is missing or broken, BSE often still has
the corresponding result announcement. BSE's XBRL here is announcement
metadata only; the financial attachment is typically a PDF. This module
records that fact for CSV reports without attempting PDF parsing.
"""

from __future__ import annotations

import datetime as dt
import re

import requests

_BSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
}
_BSE_ANNOUNCEMENTS_URL = (
    "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
)
_BSE_SUGGEST_URL = (
    "https://api.bseindia.com/BseIndiaAPI/api/Suggest/getSuggestData/w"
)
_BSE_PDF_ROOT = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"
# Nifty 50 symbol -> BSE scrip code. BSE suggest/lookup APIs often return
# retired HTML pages; ComHeader works when the scrip code is already known.
_NIFTY50_BSE_SCRIPS: dict[str, str] = {
    "ADANIENT": "512599",
    "ADANIPORTS": "532921",
    "APOLLOHOSP": "508869",
    "ASIANPAINT": "500820",
    "AXISBANK": "532215",
    "BAJAJ-AUTO": "532977",
    "BAJAJFINSV": "532978",
    "BAJFINANCE": "500034",
    "BEL": "500049",
    "BHARTIARTL": "532454",
    "BRITANNIA": "500825",
    "CIPLA": "500087",
    "COALINDIA": "533278",
    "DRREDDY": "500124",
    "EICHERMOT": "505200",
    "GRASIM": "500300",
    "HCLTECH": "532281",
    "HDFCBANK": "500180",
    "HDFCLIFE": "540777",
    "HEROMOTOCO": "500182",
    "HINDALCO": "500440",
    "HINDUNILVR": "500696",
    "ICICIBANK": "532174",
    "INDUSINDBK": "532187",
    "INFY": "500209",
    "ITC": "500875",
    "JIOFIN": "543940",
    "JSWSTEEL": "500228",
    "KOTAKBANK": "500247",
    "LT": "500510",
    "M&M": "500520",
    "MARUTI": "532500",
    "NESTLEIND": "500790",
    "NTPC": "532555",
    "ONGC": "500312",
    "POWERGRID": "532898",
    "RELIANCE": "500325",
    "SBILIFE": "540719",
    "SBIN": "500112",
    "SHRIRAMFIN": "511218",
    "SUNPHARMA": "524715",
    "TATACONSUM": "500800",
    "TATAMOTORS": "500570",
    "TATASTEEL": "500470",
    "TCS": "532540",
    "TECHM": "532755",
    "TITAN": "500114",
    "TRENT": "500251",
    "ULTRACEMCO": "532538",
    "WIPRO": "507685",
}
# Historical NSE symbol aliases that share BSE scrip with another ticker.
_BSE_SYMBOL_ALIASES: dict[str, str] = {
    "TATAMTRDVR": "TATAMOTORS",
}
_SCRIP_CACHE: dict[str, str | None] = {}


def _parse_period_end(target_period: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(target_period, "%d-%b-%Y").date()
    except ValueError:
        return None


def resolve_bse_scrip_code(symbol: str) -> str | None:
    """Resolve a NSE/BSE symbol to BSE scrip code."""
    token = symbol.upper().strip()
    if token in _SCRIP_CACHE:
        return _SCRIP_CACHE[token]

    lookup_symbol = _BSE_SYMBOL_ALIASES.get(token, token)
    scrip_code = _NIFTY50_BSE_SCRIPS.get(lookup_symbol)
    if scrip_code:
        _SCRIP_CACHE[token] = scrip_code
        return scrip_code

    scrip_code = _resolve_scrip_via_com_header(lookup_symbol)
    _SCRIP_CACHE[token] = scrip_code
    return scrip_code


def _resolve_scrip_via_com_header(symbol: str) -> str | None:
    """Best-effort scrip lookup via BSE suggest API (often unavailable)."""
    try:
        response = requests.get(
            _BSE_SUGGEST_URL,
            headers=_BSE_HEADERS,
            params={"flag": "0", "query": symbol},
            timeout=20,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
    except (requests.RequestException, ValueError, TypeError):
        return None

    rows = payload if isinstance(payload, list) else payload.get("Table", [])
    if not isinstance(rows, list):
        return None

    for row in rows:
        if not isinstance(row, dict):
            continue
        row_symbol = str(
            row.get("symbol")
            or row.get("Symbol")
            or row.get("scrip_cd")
            or ""
        ).upper().strip()
        if row_symbol != symbol:
            continue
        raw_scrip = row.get("scrip_cd") or row.get("SCRIP_CD")
        if raw_scrip not in (None, ""):
            return str(raw_scrip).strip()
    return None


def _period_end_in_subject(subject: str, period_end: dt.date) -> bool:
    text = subject.lower()
    month_names = (
        period_end.strftime("%B").lower(),
        period_end.strftime("%b").lower(),
    )
    day = period_end.day
    year = period_end.year
    patterns = (
        rf"{month_names[0]}\s+{day},?\s+{year}",
        rf"{month_names[1]}\s+{day},?\s+{year}",
        rf"{day}\s+{month_names[0]},?\s+{year}",
        rf"{day}\s+{month_names[1]},?\s+{year}",
        rf"{day}(?:st|nd|rd|th)?\s+{month_names[0]},?\s+{year}",
        rf"{day}(?:st|nd|rd|th)?\s+{month_names[1]},?\s+{year}",
        rf"{day:02d}-{period_end.strftime('%b').upper()}-{year}",
        rf"{day:02d}-{period_end.strftime('%B').upper()}-{year}",
    )
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _announcement_subject_rank(subject: str) -> int:
    """Prefer published result outcomes over meeting intimations."""
    text = subject.lower()
    if "board meeting intimation" in text:
        return 2
    if "outcome" in text or text.startswith("unaudited") or text.startswith("audited"):
        return 0
    return 1


def _fetch_bse_announcement_rows(
    scrip_code: str,
    search_start: dt.date,
    search_end: dt.date,
    *,
    max_pages: int = 5,
) -> list[dict]:
    """Fetch BSE corporate announcements across pages."""
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        params = {
            "pageno": str(page),
            "strCat": "-1",
            "strPrevDate": search_start.strftime("%Y%m%d"),
            "strScrip": scrip_code,
            "strSearch": "P",
            "strToDate": search_end.strftime("%Y%m%d"),
            "strType": "C",
        }
        try:
            response = requests.get(
                _BSE_ANNOUNCEMENTS_URL,
                headers=_BSE_HEADERS,
                params=params,
                timeout=20,
            )
            if response.status_code != 200:
                break
            payload = response.json()
        except (requests.RequestException, ValueError):
            break

        page_rows = payload.get("Table", []) if isinstance(payload, dict) else payload
        if not isinstance(page_rows, list) or not page_rows:
            break
        rows.extend(row for row in page_rows if isinstance(row, dict))
        if len(page_rows) < 50:
            break
    return rows


def _is_financial_result_subject(subject: str) -> bool:
    text = subject.lower()
    return "financial result" in text or "financial results" in text


def _parse_bse_news_date(raw_value: str | None) -> dt.date | None:
    if not raw_value:
        return None
    token = str(raw_value).strip()
    try:
        return dt.datetime.fromisoformat(token.replace("Z", "")).date()
    except ValueError:
        pass
    for fmt in ("%d-%b-%Y", "%d-%b-%Y %H:%M:%S"):
        try:
            return dt.datetime.strptime(token, fmt).date()
        except ValueError:
            continue
    return None


def _pdf_url_from_row(row: dict) -> str | None:
    attachment = str(row.get("ATTACHMENTNAME", "")).strip()
    if attachment and attachment.lower().endswith(".pdf"):
        return f"{_BSE_PDF_ROOT}{attachment}"
    return None


def find_bse_financial_result_pdf(
    symbol: str,
    target_period: str,
) -> dict | None:
    """Return BSE result announcement metadata when only PDF is available."""
    period_end = _parse_period_end(target_period)
    if period_end is None:
        return None

    scrip_code = resolve_bse_scrip_code(symbol)
    if not scrip_code:
        return None

    # BSE filters by announcement date; results are usually published soon
    # after the quarter/year end, not on the period-end date itself.
    search_start = period_end + dt.timedelta(days=7)
    search_end = period_end + dt.timedelta(days=120)
    rows = _fetch_bse_announcement_rows(scrip_code, search_start, search_end)

    matches: list[tuple[int, dt.date, dict]] = []
    for row in rows:
        subject = str(row.get("NEWSSUB", "")).strip()
        if not _is_financial_result_subject(subject):
            continue
        if not _period_end_in_subject(subject, period_end):
            continue
        pdf_url = _pdf_url_from_row(row)
        if not pdf_url:
            continue
        news_date = _parse_bse_news_date(
            row.get("NEWS_DT") or row.get("DT_TM")
        )
        if news_date is None:
            news_date = period_end
        matches.append((_announcement_subject_rank(subject), news_date, row))

    if not matches:
        return None

    matches.sort(key=lambda item: (item[0], item[1]))
    _, news_date, row = matches[0]
    return {
        "subject": str(row.get("NEWSSUB", "")).strip(),
        "news_date": news_date.strftime("%d-%b-%Y"),
        "pdf_url": _pdf_url_from_row(row),
        "scrip_code": scrip_code,
    }


def bse_pdf_only_note(symbol: str, target_period: str) -> str | None:
    """Build a CSV comment when BSE has PDF-only result disclosure."""
    hit = find_bse_financial_result_pdf(symbol, target_period)
    if not hit or not hit.get("pdf_url"):
        return None
    return (
        "BSE fallback: financial result announcement found (PDF only, "
        f"not parsed) on {hit['news_date']}: {hit['pdf_url']}"
    )
