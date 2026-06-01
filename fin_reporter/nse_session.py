"""NSE HTTP session management and authenticated API requests."""

from __future__ import annotations

import time

import requests  # pyright: ignore[reportMissingModuleSource]

DEFAULT_API_HEADERS = {
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

DEFAULT_PAGE_HEADERS = {
    **DEFAULT_API_HEADERS,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/webp,*/*;q=0.8"
    ),
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


class NSESessionMixin:
    """Mixin providing NSE cookie session warmup and authenticated GET requests."""

    base_url = "https://www.nseindia.com"
    max_session_retries = 3

    def __init__(self, timeout: int = 20, delay_seconds: float = 2):
        self.timeout = timeout
        self.delay_seconds = delay_seconds
        self.session = requests.Session()
        self.headers = dict(DEFAULT_API_HEADERS)
        self.page_headers = dict(DEFAULT_PAGE_HEADERS)

    def ensure_api_session(self) -> None:
        """Initialize the NSE session when API calls are needed but cookies are absent."""
        if self.session.cookies:
            return
        self.initialize_session()

    def initialize_session(self) -> None:
        """Warm up the NSE session by visiting key pages to obtain cookies."""
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

    def api_get(self, url: str, params=None, stream: bool = False):
        """Authenticated GET with automatic session refresh on 401/403."""
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

    # Backward-compatible alias for internal callers.
    _api_get = api_get
