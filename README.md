# FinancialReporter

Command-line tool and **interactive web dashboard** that downloads quarterly financial result filings in XBRL format from the [National Stock Exchange of India (NSE)](https://www.nseindia.com/) and presents download status and financial metrics in terminal tables or in the browser.

Symbols are NSE tickers (for example `RELIANCE`, `HDFCBANK`, `ITC`). The tool fetches filings when needed, caches files under a local folder, parses XBRL facts, and computes quarter-level metrics. **Banks** (IN-GAAP) and **manufacturing** companies (Ind-AS) are detected automatically from filing tags.

## Features

### CLI

- Downloads XBRL from NSE integrated and financial-results APIs, with automatic fallback
- Reuses cached files when present; skips NSE session warmup when nothing new is required
- Fetches a **standalone** companion filing when consolidated is cached but standalone is needed (for example NPA / ROA)
- Resolves Indian fiscal quarters (`Q1_FY26` … `Q4_FY26`, two- or four-digit FY) or explicit period-end dates (`31-Mar-2026`)
- Multiple symbols and multiple quarters per run (`--back-quarters`)
- **Q4 handling:** derives quarter figures from cumulative contexts when direct quarter tags are missing; banks can use FY − prior YTD when earlier-quarter files exist in the same output folder
- Manufacturing **EBITDA** with `include-other-income` or `exclude-other-income`, plus optional XBRL tag discovery (`--debug-tags`)

### Web dashboard

- Browser UI served by [`app.py`](app.py) with a REST API for symbols, quarters, and metrics
- Multi-company comparison with **Performance Trends** chart (Chart.js time series)
- **Chart metric checkboxes** — one checkbox per metric toggles that series on/off for all companies on the chart
- **Metrics picker** in the sidebar — filter which parameters appear in tables and charts; leave empty to show all sector-applicable metrics
- **Company tabs** — per-symbol metrics table and corporate-actions timeline
- **EBITDA calculation** checkbox (manufacturing only): exclude other income from the EBITDA formula when checked
- CSV export of computed metrics
- Offline badge when all requested filings load from local cache

## Requirements

- Python 3.10+ (3.12 tested)
- [requests](https://pypi.org/project/requests/) (see `requirements.txt`)
- Network access to NSE for downloads (no API key)
- Modern browser for the web dashboard (Chart.js loaded from CDN)

## Installation

Clone the repository and install dependencies:

```powershell
git clone https://github.com/raghavamohan/FinancialReporter.git
cd FinancialReporter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Optional — run tests:

```powershell
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

## Architecture

Both the CLI and web dashboard call [`fin_reporter.service.run_analysis`](fin_reporter/service.py), which:

1. Resolves display and download quarter ranges (including extra quarters for trailing EPS / P/E)
2. Downloads or reuses cached XBRL via [`NSEXBRLDownloader`](fin_reporter/downloader.py)
3. Parses each filing once ([`parse_xbrl`](fin_reporter/xbrl_parser.py)) and builds sector metrics
4. Enriches with market data (share price, trailing EPS, P/E, dividends, corporate actions)

The downloader composes [`nse_session`](fin_reporter/nse_session.py) (HTTP session), [`filing_resolver`](fin_reporter/filing_resolver.py) (NSE filing lookup), and [`cache_paths`](fin_reporter/cache_paths.py) (local cache filenames). Trailing EPS uses lightweight EPS extraction ([`fin_reporter.eps`](fin_reporter/eps.py)) rather than rebuilding full metrics for each prior quarter.

## Web dashboard

Start the local server (default port **8080**, cache under `.\xbrl_downloads`):

```powershell
python app.py
python app.py --port 8080 --cache-dir .\xbrl_downloads
```

Open [http://localhost:8080/](http://localhost:8080/) in your browser.

The server reuses one `NSEXBRLDownloader` instance per process and only opens an NSE session when cached files or market data require network access.

### Control panel

| Control | Description |
|---------|-------------|
| **Select Companies** | Multi-symbol picker with Nifty 50 suggestions; cached symbols are marked |
| **Anchor Quarter** | Fiscal quarter code (for example `Q4_FY26`) |
| **Trailing Quarters** | Slider (1–12): how many quarters to display, counting back from the anchor |
| **EBITDA Calculation** | Checkbox: *Exclude other income* (manufacturing only). Unchecked = include other income in EBITDA |
| **Metrics to Display** | Searchable multi-select; limits table rows and chart series. Empty = all sector-applicable metrics |
| **Analyze Performance** | Fetches/cache XBRL, computes metrics, renders chart and tables |

### Results area

1. **Performance Trends** — multi-company chart over trailing quarters. Checkboxes above the chart control which metrics are drawn for every company; unchecked labels dim.
2. **Company tabs** — metrics table (quarters as columns) and corporate-actions timeline per symbol.
3. **Export metrics to CSV** — full metric dump for the current run.

Mixed bank + manufacturing runs show sector-appropriate rows (for example Revenue vs Total Income, EBITDA vs PPOP).

### REST API

All endpoints are `GET` with compact JSON responses.

| Endpoint | Parameters | Description |
|----------|------------|-------------|
| `/api/symbols` | — | Nifty 50 + symbols found in cache (`symbols`, `cached` arrays) |
| `/api/quarters` | — | Supported quarter codes (`Q4_FY23` … `Q4_FY26`) |
| `/api/metrics` | `symbols` (comma-separated), `quarter`, `back_quarters`, `ebitda_definition` | Download/cache filings, compute metrics, return JSON |

`/api/metrics` response fields:

| Field | Description |
|-------|-------------|
| `display_quarters` | Quarter codes shown in the UI |
| `symbols` | Requested symbols |
| `metrics` | Nested map: symbol → quarter → metric object |
| `downloads` | Per symbol/quarter download status |
| `errors` | Non-fatal failures during download or metric computation |

Example:

```powershell
curl "http://localhost:8080/api/metrics?symbols=HDFCBANK,RELIANCE&quarter=Q4_FY26&back_quarters=8&ebitda_definition=include-other-income"
```

`ebitda_definition` accepts `include-other-income` (default) or `exclude-other-income`.

## CLI usage

Run from the project root using either entry point:

```powershell
python fin_report.py --symbols RELIANCE HDFCBANK --quarter Q4_FY26
```

```powershell
python -m fin_reporter --symbols RELIANCE ITC SBIN --quarter Q3_FY26
```

Symbols are uppercased automatically. The alias `HDFC` is normalized to `HDFCBANK` for NSE lookups.

### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--symbols` | Yes | — | One or more NSE symbols (space-separated) |
| `--quarter` | Yes | — | Quarter code (`Q4_FY26`, `Q4_FY2026`) or period end (`31-Mar-2026`) |
| `--back-quarters` | No | `1` | Quarters to process, counting back from `--quarter` (includes that quarter). Newest first. |
| `--output` | No | `.\xbrl_downloads` | Directory for cached XBRL files |
| `--timeout` | No | `20` | HTTP timeout per request (seconds) |
| `--delay` | No | `2.0` | Pause between symbol requests (seconds) |
| `--ebitda-definition` | No | `include-other-income` | Manufacturing EBITDA only: `include-other-income` or `exclude-other-income` |
| `--debug-tags` | No | off | Print candidate XBRL tags for EBITDA-related fields (manufacturing) |

#### `--ebitda-definition` (manufacturing only)

| Value | Formula (simplified) |
|-------|----------------------|
| `include-other-income` | Prefer segment PBT + segment finance cost + depreciation; otherwise PBT (pre-exceptional where available) + finance cost + depreciation |
| `exclude-other-income` | Same base components, then subtract **Other Income** |

Banks do not use EBITDA. For banks, **PPOP** (Pre-Provision Operating Profit) is the operating-profit metric shown instead — interest is core business income, not a financing cost to add back.

### Quarter codes

Indian FY convention (examples for `FY26` = year ending 31-Mar-2026):

| Code | Period end |
|------|------------|
| `Q1_FY26` | 30-Jun-2025 |
| `Q2_FY26` | 30-Sep-2025 |
| `Q3_FY26` | 31-Dec-2025 |
| `Q4_FY26` | 31-Mar-2026 |

With `--back-quarters 4` and `--quarter Q4_FY26`, the tool processes `Q4_FY26`, `Q3_FY26`, `Q2_FY26`, and `Q1_FY26` in that order.

For trailing EPS and P/E, the tool also downloads up to three quarters **before** the oldest displayed quarter (not shown in the metrics table). Those appear in the download results table with `(trailing EPS support, not in metrics table)` in the message.

Corporate actions used for dividend and bonus/split handling are cached locally under `<output>/.market_cache/` to avoid repeated NSE calls across runs.

If `--quarter` is a date (for example `30-Jun-2025`), `--back-quarters` steps backward by calendar quarter ends instead of FY codes.

### Examples

Single quarter, two companies:

```powershell
python fin_report.py --symbols RELIANCE HDFCBANK --quarter Q4_FY26
```

Last four quarters for one symbol (one metrics table per symbol, columns = quarters):

```powershell
python -m fin_reporter --symbols ITC --quarter Q4_FY26 --back-quarters 4
```

Custom download directory and slower requests (helps avoid NSE rate limits):

```powershell
python fin_report.py --symbols SBIN ICICIBANK --quarter Q2_FY26 --output D:\data\xbrl --delay 3
```

Manufacturing EBITDA and tag debugging:

```powershell
python fin_report.py --symbols RELIANCE --quarter Q3_FY26 --ebitda-definition exclude-other-income --debug-tags
```

Use cache only (no NSE session if every required file is already present):

```powershell
python fin_report.py --symbols RELIANCE --quarter Q4_FY26 --output .\xbrl_downloads
```

When all primary (and any required standalone) files exist locally:

```text
[+] All requested XBRL files found locally; skipping NSE session.
```

Cached hits still appear as `DOWNLOADED` with source `cached` in the results table.

### Cache prewarm (optional)

Pre-download Nifty 50 XBRL and market-data cache for faster dashboard use:

```powershell
python .\scripts\prewarm_nifty50_cache.py
python .\scripts\prewarm_nifty50_cache.py --quarter Q4_FY26 --quarters 13
```

## Output

### 1. Download results table

Per symbol and quarter: period, filing basis (`consolidated` / `standalone` / `unknown`), status, API source, file path, and message.

| Status | Meaning |
|--------|---------|
| `DOWNLOADED` | File available (fresh download or cache) |
| `NOT_FOUND` | No matching XBRL on NSE for that symbol/period |
| `FAILED` | Filing found but download failed |
| `ERROR` | Unexpected error during processing |

Source is typically `integrated`, `legacy`, or `cached` (NSE API endpoint used for the download).

### 2. Financial metrics tables

Monetary amounts are labeled **Rs Cr** (₹ crore). Only rows with `DOWNLOADED` files are included.

**Single quarter** (`--back-quarters 1`): one table; **columns = symbols**.

**Multiple quarters** (`--back-quarters` > 1 with quarter codes): **one table per symbol**; **columns = quarters**.

Row sets depend on company type detected in each filing:

**Banking (IN-GAAP)**

| Parameter | Notes |
|-----------|--------|
| Net Interest Income | Interest earned − interest expended |
| Total Income | Interest earned + other income |
| PPOP | Pre-provision operating profit |
| PBT | Profit before tax (after provisions) |
| Net Income | |
| Basic EPS | Not scaled to crore |
| P/E Ratio | Share price on period end ÷ trailing four-quarter basic EPS, with older EPS adjusted for bonus/split ex-dates (needs prior-quarter XBRL in `--output`) |
| Dividend (Rs/sh) | Sum of per-share dividends from NSE corporate actions with ex-date in the reporting quarter |
| Other Corporate Actions | Non-dividend corporate actions (bonus, split, demerger, rights, etc.) with ex-date in the reporting quarter |
| ROA (%) | Annualized when reported as a decimal on a quarter context |
| Gross NPA (%) | May use standalone fallback if consolidated missing |
| Net NPA (%) | Same fallback behavior |
| Cost-to-Income (%) | Operating efficiency ratio (dashboard / extended metrics) |

**Manufacturing (Ind-AS)**

| Parameter | Notes |
|-----------|--------|
| Revenue from Operations | |
| EBITDA | See `--ebitda-definition` |
| PBIT | PBT + finance cost (EBIT) |
| PBT | |
| Net Income | |
| Basic EPS | |
| P/E Ratio | Same as banking row |
| Dividend (Rs/sh) | Same as banking row |
| Other Corporate Actions | Same as banking row |
| Gross Profit / Gross Margin | Dashboard / extended metrics |
| EBITDA Margin / Net Margin | Dashboard / extended metrics |
| ROCE (%) | Return on capital employed |

If a single run mixes banks and non-banks, a combined row layout is used (for example Revenue / NII and EBITDA / PPOP columns).

After manufacturing tables, the CLI prints which EBITDA mode was used (for example `[Info] EBITDA: include other income`). With `--debug-tags`, tag discovery runs after the metrics tables.

## Project layout

```
FinancialReporter/
├── app.py                     # Web dashboard + REST API server
├── fin_report.py              # CLI wrapper → fin_reporter.cli
├── fin_reporter/
│   ├── __main__.py            # python -m fin_reporter
│   ├── cli.py                 # CLI arguments; delegates to service.run_analysis
│   ├── service.py             # Shared download + metrics pipeline
│   ├── constants.py           # XBRL tag mappings
│   ├── models.py              # DownloadResult, FinancialMetrics, …
│   ├── downloader.py          # NSEXBRLDownloader orchestrator
│   ├── nse_session.py         # NSE cookie session and api_get
│   ├── filing_resolver.py     # Integrated / financial-results filing lookup
│   ├── cache_paths.py         # Local XBRL cache path helpers
│   ├── market_data.py         # Share prices, dividends, enrichment
│   ├── eps.py                 # Lightweight basic-EPS extraction
│   ├── xbrl_parser.py         # XBRL parse cache, facts, metadata
│   ├── period_resolver.py     # Quarter arithmetic and context plans
│   ├── display.py             # Console tables
│   ├── symbols.py             # Nifty 50 fallback symbol list
│   ├── bse_fallback.py        # BSE PDF note when NSE XBRL is missing
│   ├── timing.py              # Optional performance logging
│   └── metrics/
│       ├── base.py            # Bank vs manufacturing detection
│       ├── banking.py         # IN-GAAP metrics (NII, PPOP, NPA, …)
│       └── manufacturing.py   # Ind-AS metrics and EBITDA
├── frontend/
│   ├── index.html             # Dashboard SPA shell
│   ├── app.js                 # Metrics registry, chart, tables, API client
│   └── style.css              # Dashboard UI styles
├── scripts/
│   └── prewarm_nifty50_cache.py
├── tests/                     # pytest suite (see requirements-dev.txt)
├── requirements.txt
├── requirements-dev.txt
├── LICENSE                    # Apache License 2.0
└── xbrl_downloads/            # Default cache (gitignored; created at runtime)
```

## Programmatic use

### Metrics from a cached XBRL file

```python
from fin_reporter.metrics import build_metrics_from_file

metrics = build_metrics_from_file(
    "xbrl_downloads/RELIANCE_Q4_FY26_XBRL.xml",
    "31-Mar-2026",
    ebitda_definition="include-other-income",
)
print(metrics.company_type, metrics.revenue, metrics.ebitda)
```

### Full analysis (download + metrics + market enrichment)

```python
from fin_reporter.downloader import NSEXBRLDownloader
from fin_reporter.service import run_analysis

downloader = NSEXBRLDownloader(timeout=20, delay_seconds=2.0)
result = run_analysis(
    ["RELIANCE", "HDFCBANK"],
    quarter="Q4_FY26",
    back_quarters=4,
    cache_dir="xbrl_downloads",
    ebitda_definition="include-other-income",
    downloader=downloader,
)

for symbol, quarters in result.metrics_by_symbol.items():
    for quarter_label, metrics in quarters.items():
        print(symbol, quarter_label, metrics.net_income, metrics.pe_ratio)
```

## Notes and limitations

- NSE may block or throttle automated access; increase `--delay` and retry if downloads fail.
- Not every symbol/quarter has XBRL on NSE; missing filings show as `NOT_FOUND`.
- Metrics depend on tags in each filing; missing values appear as `-` in the table.
- Delete cached files under `--output` to force a fresh download.
- The web dashboard requires network on first fetch per symbol/quarter; subsequent loads can be fully offline when cache is warm.
- This tool is for research and education; verify figures against official filings before any investment or compliance use.

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
