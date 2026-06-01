#!/usr/bin/env python
r"""Native REST API and Static Web Server for Financial Reporter Dashboard.

Exposes endpoints for cached stocks, fiscal quarters, and metrics computation,
and serves a high-fidelity glassmorphic HTML/JS/CSS Single Page Application (SPA).

Usage:
    python app.py
    python app.py --port 8080 --cache-dir .\xbrl_downloads
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import glob
import http.server
import json
import os
import sys
import urllib.parse

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fin_reporter.downloader import NSEXBRLDownloader
from fin_reporter.metrics import build_metrics_from_file
from fin_reporter.display import _enrich_market_metrics, _latest_period_end_for_results
from fin_reporter.timing import time_block

FALLBACK_NIFTY50_SYMBOLS = (
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL",
    "BRITANNIA", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK", "INFY",
    "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "NESTLEIND", "NTPC", "ONGC", "POWERGRID", "RELIANCE",
    "SBILIFE", "SBIN", "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM",
    "TATAMOTORS", "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT",
    "ULTRACEMCO", "WIPRO"
)


class FinancialReporterAPIHandler(http.server.SimpleHTTPRequestHandler):
    """Custom request handler subclass serving frontend SPA assets and REST APIs."""

    def translate_path(self, path: str) -> str:
        # Resolve query parameters
        pure_path = path.split('?')[0]
        if pure_path == "/":
            pure_path = "/index.html"

        # Serve static SPA assets from the frontend directory
        if pure_path in ("/index.html", "/style.css", "/app.js"):
            return os.path.abspath(os.path.join(PROJECT_ROOT, "frontend" + pure_path))

        return super().translate_path(path)

    def do_GET(self) -> None:
        # Route API requests
        if self.path.startswith("/api/"):
            self.handle_api()
        else:
            super().do_GET()

    def handle_api(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed_url.query)

        # CORS Headers to enable smooth browser integrations
        headers = {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }

        if parsed_url.path == "/api/symbols":
            self.get_symbols(headers)
        elif parsed_url.path == "/api/quarters":
            self.get_quarters(headers)
        elif parsed_url.path == "/api/metrics":
            self.get_metrics(params, headers)
        else:
            self.send_error_response(404, "Endpoint not found", headers)

    def get_symbols(self, headers: dict) -> None:
        """Scan cache dir for previously searched stocks and supplement with Nifty 50."""
        cache_dir = self.server.cache_dir
        cached = set()

        if os.path.exists(cache_dir):
            for path in glob.glob(os.path.join(cache_dir, "*_XBRL.xml")):
                basename = os.path.basename(path)
                parts = basename.split("_")
                if len(parts) >= 2:
                    cached.add(parts[0].upper().strip())

        # Merge with Nifty 50 and sort
        all_symbols = sorted(list(cached.union(set(FALLBACK_NIFTY50_SYMBOLS))))
        response = {
            "symbols": all_symbols,
            "cached": sorted(list(cached))
        }
        self.send_json_response(200, response, headers)

    def get_quarters(self, headers: dict) -> None:
        """Return a sorted sequence of available quarters from FY23 to FY26."""
        # Static standard quarter sequences for simple indexing
        quarters = [
            "Q4_FY26", "Q3_FY26", "Q2_FY26", "Q1_FY26",
            "Q4_FY25", "Q3_FY25", "Q2_FY25", "Q1_FY25",
            "Q4_FY24", "Q3_FY24", "Q2_FY24", "Q1_FY24",
            "Q4_FY23", "Q3_FY23", "Q2_FY23", "Q1_FY23"
        ]
        self.send_json_response(200, {"quarters": quarters}, headers)

    def get_metrics(self, params: dict, headers: dict) -> None:
        """Compute and serialize financial parameters for multiple stocks/periods."""
        symbols_raw = params.get("symbols", [""])
        symbols_list = [
            sym.strip().upper()
            for token in symbols_raw
            for sym in token.split(",")
            if sym.strip()
        ]

        if not symbols_list:
            self.send_error_response(400, "Missing 'symbols' query parameter", headers)
            return

        anchor_quarter = params.get("quarter", ["Q4_FY26"])[0].strip().upper()
        back_quarters_raw = params.get("back_quarters", ["10"])[0].strip()
        ebitda_definition = params.get("ebitda_definition", ["include-other-income"])[0].strip()

        try:
            back_quarters = int(back_quarters_raw)
        except ValueError:
            back_quarters = 10

        cache_dir = self.server.cache_dir
        downloader = NSEXBRLDownloader(timeout=10, delay_seconds=2.0)

        with time_block(category="API Request", detail=f"symbols={symbols_list}, quarter={anchor_quarter}, quarters_depth={back_quarters}"):
            # Resolve quarter scopes
            try:
                display_quarters = downloader.resolve_quarter_sequence(anchor_quarter, back_quarters)
                display_quarters_set = {q.strip().upper() for q in display_quarters}
                display_qs, download_quarters = downloader.resolve_display_and_download_quarters(
                    anchor_quarter, back_quarters
                )
            except Exception as e:
                self.send_error_response(400, f"Error resolving quarter sequence: {e}", headers)
                return

            # Fetch filings lazily
            results = []
            with time_block(category="Filing Download Block", detail=f"symbols={symbols_list}, quarters={download_quarters}"):
                for quarter in download_quarters:
                    try:
                        quarter_results = downloader.download_for_symbols(symbols_list, quarter, cache_dir)
                        results.extend(quarter_results)
                    except Exception as e:
                        # Log fetch error and proceed
                        pass

            # Filter successfully resolved display results
            successful = [
                r for r in results
                if r.status == "DOWNLOADED" and r.file_path != "-"
                and (r.quarter_label or anchor_quarter).strip().upper() in display_quarters_set
            ]

            # Resolve trailing anchors
            symbol_anchors = {}
            for symbol in symbols_list:
                symbol_anchors[symbol] = _latest_period_end_for_results(successful, symbol)

            # Build and enrich dataclass records
            metrics_payload = {}
            for r in successful:
                symbol = r.symbol.upper().strip()
                quarter_label = (r.quarter_label or anchor_quarter).upper().strip()

                try:
                    with time_block(category="Metrics Enrichment", detail=f"symbol={symbol}, quarter={quarter_label}"):
                        metrics = build_metrics_from_file(
                            r.file_path,
                            r.period,
                            ebitda_definition=ebitda_definition,
                        )
                        enriched = _enrich_market_metrics(
                            metrics,
                            r.file_path,
                            r.period,
                            symbol,
                            ebitda_definition,
                            downloader,
                            pe_anchor_date=symbol_anchors.get(symbol),
                        )
                        serialized = dataclasses.asdict(enriched)
                        metrics_payload.setdefault(symbol, {})[quarter_label] = serialized
                except Exception as e:
                    # Skip errors for single quarters gracefully
                    pass

            response_data = {
                "display_quarters": display_quarters,
                "symbols": symbols_list,
                "metrics": metrics_payload,
                "downloads": [
                    {
                        "symbol": r.symbol,
                        "quarter": r.quarter_label,
                        "status": r.status,
                        "file_path": r.file_path,
                        "message": r.message,
                        "source": r.source,
                        "filing_basis": r.filing_basis
                    }
                    for r in results
                ]
            }
            self.send_json_response(200, response_data, headers)

    def send_json_response(self, status: int, data: dict, headers: dict) -> None:
        self.send_response(status)
        for key, val in headers.items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))

    def send_error_response(self, status: int, message: str, headers: dict) -> None:
        self.send_json_response(status, {"error": message}, headers)

    def do_OPTIONS(self) -> None:
        # CORS preflight options
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


class FinancialReporterWebServer(http.server.ThreadingHTTPServer):
    """Custom Threading HTTP Server supporting persistent variables."""
    def __init__(self, server_address, RequestHandlerClass, cache_dir: str):
        self.cache_dir = cache_dir
        super().__init__(server_address, RequestHandlerClass)


def main() -> None:
    parser = argparse.ArgumentParser(description="Financial Reporter Web Dashboard Server")
    parser.add_argument("--port", type=int, default=8080, help="Port to run web server on (default: 8080)")
    parser.add_argument("--cache-dir", type=str, default=".\\xbrl_downloads", help="Local filings cache path")
    args = parser.parse_args()

    # Pre-create folders if absent
    os.makedirs(args.cache_dir, exist_ok=True)
    os.makedirs(os.path.join(PROJECT_ROOT, "frontend"), exist_ok=True)

    server_address = ("", args.port)
    server = FinancialReporterWebServer(server_address, FinancialReporterAPIHandler, args.cache_dir)
    print(f"[+] Financial Reporter visual dashboard server started successfully!")
    print(f"[+] Point your browser to: http://localhost:{args.port}/")
    print(f"[+] Server logs printing below (Press Ctrl+C to terminate)...")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[-] Server shutting down cleanly...")
        server.server_close()
        sys.exit(0)


if __name__ == "__main__":
    main()
