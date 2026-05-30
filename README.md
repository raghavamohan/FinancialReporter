# FinancialReporter

Command-line tool that downloads quarterly financial result filings in XBRL format from the [National Stock Exchange of India (NSE)](https://www.nseindia.com/) and prints download status and financial metrics in terminal tables.

Symbols are NSE tickers (for example `RELIANCE`, `HDFCBANK`, `ITC`). The tool fetches filings when needed, caches files under a local folder, parses XBRL facts, and computes quarter-level metrics. **Banks** (IN-GAAP) and **manufacturing** companies (Ind-AS) are detected automatically from filing tags.

## Features

- Downloads XBRL from NSE **integrated** and **legacy** financial-results APIs, with automatic fallback
- Reuses cached files when present; skips NSE session warmup when nothing new is required
- Fetches a **standalone** companion filing when consolidated is cached but standalone is needed (for example NPA / ROA)
- Resolves Indian fiscal quarters (`Q1_FY26` … `Q4_FY26`, two- or four-digit FY) or explicit period-end dates (`31-Mar-2026`)
- Multiple symbols and multiple quarters per run (`--back-quarters`)
- **Q4 handling:** derives quarter figures from cumulative contexts when direct quarter tags are missing; banks can use FY − prior YTD when earlier-quarter files exist in the same output folder
- Configurable manufacturing **EBITDA** formula and optional XBRL tag discovery for debugging

## Requirements

- Python 3.10+ (3.12 tested)
- [requests](https://pypi.org/project/requests/) (see `requirements.txt`)
- Network access to NSE for downloads (no API key)

## Installation

Clone the repository and install dependencies:

```powershell
git clone https://github.com/raghavamohan/FinancialReporter.git
cd FinancialReporter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Usage

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
| `--ebitda-definition` | No | `tickertape` | Manufacturing EBITDA only (see below) |
| `--debug-tags` | No | off | Print candidate XBRL tags for EBITDA-related fields (manufacturing) |

#### `--ebitda-definition` (manufacturing only)

| Value | Formula (simplified) |
|-------|----------------------|
| `tickertape` | Prefer segment PBT + segment finance cost + depreciation; otherwise PBT (pre-exceptional where available) + finance cost + depreciation |
| `subtract-other-income` | Same base components, then subtract **Other Income** |

Banks do not use EBITDA; this flag applies only when manufacturing filers are in the run.

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
python fin_report.py --symbols RELIANCE --quarter Q3_FY26 --ebitda-definition subtract-other-income --debug-tags
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

## Output

### 1. Download results table

Per symbol and quarter: period, filing basis (`consolidated` / `standalone` / `unknown`), status, API source, file path, and message.

| Status | Meaning |
|--------|---------|
| `DOWNLOADED` | File available (fresh download or cache) |
| `NOT_FOUND` | No matching XBRL on NSE for that symbol/period |
| `FAILED` | Filing found but download failed |
| `ERROR` | Unexpected error during processing |

Source is typically `integrated`, `legacy`, or `cached`.

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
| PBT | Profit before tax |
| Net Income | |
| Basic EPS | Not scaled to crore |
| P/E Ratio | Share price on period end ÷ trailing four-quarter basic EPS, with older EPS adjusted for bonus/split ex-dates after each quarter (needs prior-quarter XBRL in `--output`) |
| Dividend (Rs/sh) | Sum of per-share dividends from NSE corporate actions with ex-date in the reporting quarter |
| Other Corporate Actions | Non-dividend corporate actions (bonus, split, demerger, rights, etc.) with ex-date in the reporting quarter |
| ROA (%) | Annualized when reported as a decimal on a quarter context |
| Gross NPA (%) | May use standalone fallback if consolidated missing |
| Net NPA (%) | Same fallback behavior |

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

If a single run mixes banks and non-banks, a combined row layout is used (for example Revenue / NII and EBITDA / PPOP columns).

After manufacturing tables, the CLI prints which EBITDA definition was used. With `--debug-tags`, tag discovery runs after the metrics tables.

## Project layout

```
FinancialReporter/
├── fin_report.py              # CLI wrapper → fin_reporter.cli
├── fin_reporter/
│   ├── __main__.py            # python -m fin_reporter
│   ├── cli.py                 # Arguments and orchestration
│   ├── constants.py           # XBRL tag mappings
│   ├── models.py              # DownloadResult, FinancialMetrics, …
│   ├── downloader.py          # NSE session and downloads
│   ├── xbrl_parser.py         # XBRL parsing and metadata
│   ├── period_resolver.py     # Quarter contexts and Q4 deltas
│   ├── display.py             # Console tables
│   └── metrics/
│       ├── base.py            # Bank vs manufacturing detection
│       ├── banking.py         # IN-GAAP metrics
│       └── manufacturing.py   # Ind-AS metrics and EBITDA
├── requirements.txt
├── LICENSE                    # Apache License 2.0
└── xbrl_downloads/            # Default cache (gitignored; created at runtime)
```

## Programmatic use

The package can be imported for parsing and metrics without running the CLI. `NSEXBRLDownloader` requires `requests`.

```python
from fin_reporter.metrics import build_metrics_from_file

metrics = build_metrics_from_file(
    "xbrl_downloads/RELIANCE_Q4_FY26_XBRL.xml",
    "31-Mar-2026",
    ebitda_definition="tickertape",
)
print(metrics.company_type, metrics.revenue, metrics.ebitda)
```

## Notes and limitations

- NSE may block or throttle automated access; increase `--delay` and retry if downloads fail.
- Not every symbol/quarter has XBRL on NSE; missing filings show as `NOT_FOUND`.
- Metrics depend on tags in each filing; missing values appear as `-` in the table.
- Delete cached files under `--output` to force a fresh download.
- This tool is for research and education; verify figures against official filings before any investment or compliance use.

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
