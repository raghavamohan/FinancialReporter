#!/usr/bin/env python
r"""Native REST API and Static Web Server for Financial Reporter Dashboard.

Usage:
    python app.py
    python app.py --port 8080 --cache-dir .\xbrl_downloads
"""

from __future__ import annotations

import argparse
import dataclasses
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
from fin_reporter.period_resolver import AVAILABLE_QUARTER_CODES
from fin_reporter.service import run_analysis
from fin_reporter.symbols import FALLBACK_NIFTY50_SYMBOLS
from fin_reporter.timing import time_block


class FinancialReporterAPIHandler(http.server.SimpleHTTPRequestHandler):
    """Custom request handler subclass serving frontend SPA assets and REST APIs."""

    def translate_path(self, path: str) -> str:
        pure_path = path.split("?")[0]
        if pure_path == "/":
            pure_path = "/index.html"

        if pure_path in ("/index.html", "/style.css", "/app.js"):
            return os.path.abspath(os.path.join(PROJECT_ROOT, "frontend" + pure_path))

        return super().translate_path(path)

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            self.handle_api()
        else:
            super().do_GET()

    def handle_api(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed_url.query)

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
        cache_dir = self.server.cache_dir
        cached = set()

        if os.path.exists(cache_dir):
            for path in glob.glob(os.path.join(cache_dir, "*_XBRL.xml")):
                basename = os.path.basename(path)
                parts = basename.split("_")
                if len(parts) >= 2:
                    cached.add(parts[0].upper().strip())

        all_symbols = sorted(list(cached.union(set(FALLBACK_NIFTY50_SYMBOLS))))
        response = {
            "symbols": all_symbols,
            "cached": sorted(list(cached)),
        }
        self.send_json_response(200, response, headers)

    def get_quarters(self, headers: dict) -> None:
        self.send_json_response(
            200,
            {"quarters": list(AVAILABLE_QUARTER_CODES)},
            headers,
        )

    def get_metrics(self, params: dict, headers: dict) -> None:
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
        ebitda_definition = params.get(
            "ebitda_definition", ["include-other-income"]
        )[0].strip()

        try:
            back_quarters = int(back_quarters_raw)
        except ValueError:
            back_quarters = 10

        cache_dir = self.server.cache_dir
        downloader = self.server.downloader

        with time_block(
            category="API Request",
            detail=(
                f"symbols={symbols_list}, quarter={anchor_quarter}, "
                f"quarters_depth={back_quarters}"
            ),
        ):
            try:
                analysis = run_analysis(
                    symbols_list,
                    anchor_quarter,
                    back_quarters,
                    cache_dir,
                    ebitda_definition=ebitda_definition,
                    downloader=downloader,
                )
            except Exception as exc:
                self.send_error_response(
                    400,
                    f"Error running analysis: {exc}",
                    headers,
                )
                return

            metrics_payload = {
                symbol: {
                    quarter: dataclasses.asdict(metrics)
                    for quarter, metrics in quarter_map.items()
                }
                for symbol, quarter_map in analysis.metrics_by_symbol.items()
            }

            response_data = {
                "display_quarters": analysis.display_quarters,
                "symbols": symbols_list,
                "metrics": metrics_payload,
                "errors": analysis.errors,
                "downloads": [
                    {
                        "symbol": r.symbol,
                        "quarter": r.quarter_label,
                        "status": r.status,
                        "file_path": r.file_path,
                        "message": r.message,
                        "source": r.source,
                        "filing_basis": r.filing_basis,
                    }
                    for r in analysis.download_results
                ],
            }
            self.send_json_response(200, response_data, headers)

    def send_json_response(self, status: int, data: dict, headers: dict) -> None:
        self.send_response(status)
        for key, val in headers.items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(json.dumps(data, separators=(",", ":")).encode("utf-8"))

    def send_error_response(self, status: int, message: str, headers: dict) -> None:
        self.send_json_response(status, {"error": message}, headers)

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


class FinancialReporterWebServer(http.server.ThreadingHTTPServer):
    """Custom Threading HTTP Server supporting persistent variables."""

    def __init__(
        self,
        server_address,
        RequestHandlerClass,
        cache_dir: str,
        downloader: NSEXBRLDownloader,
    ):
        self.cache_dir = cache_dir
        self.downloader = downloader
        super().__init__(server_address, RequestHandlerClass)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Financial Reporter Web Dashboard Server"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to run web server on (default: 8080)",
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default=".\\xbrl_downloads",
        help="Local filings cache path",
    )
    args = parser.parse_args()

    os.makedirs(args.cache_dir, exist_ok=True)
    os.makedirs(os.path.join(PROJECT_ROOT, "frontend"), exist_ok=True)

    downloader = NSEXBRLDownloader(timeout=10, delay_seconds=2.0)
    server_address = ("", args.port)
    server = FinancialReporterWebServer(
        server_address,
        FinancialReporterAPIHandler,
        args.cache_dir,
        downloader,
    )
    print("[+] Financial Reporter visual dashboard server started successfully!")
    print(f"[+] Point your browser to: http://localhost:{args.port}/")
    print("[+] Server logs printing below (Press Ctrl+C to terminate)...")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[-] Server shutting down cleanly...")
        server.server_close()
        sys.exit(0)


if __name__ == "__main__":
    main()
