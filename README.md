# FinancialReporter

Command-line tool that downloads quarterly financial result filings in XBRL format from the [National Stock Exchange of India (NSE)](https://www.nseindia.com/) and prints key metrics in terminal tables.

It is aimed at Indian listed companies: symbols are NSE tickers (for example `RELIANCE`, `HDFCBANK`, `ITC`). The tool fetches filings when needed, caches XML under a local folder, parses XBRL facts, and computes quarter-level metrics. Banks and non-bank (manufacturing / Ind-AS) filers are detected automatically from the filing content.

## Features

- Downloads XBRL from NSE integrated and legacy financial-results APIs, with automatic fallback
- Skips NSE session setup when all requested files are already on disk
- Resolves Indian fiscal quarters (`Q1_FY26` … `Q4_FY26`) or explicit period-end dates (`31-Mar-2026`)
- Supports multiple symbols and multiple quarters in one run (`--back-quarters`)
- **Banks:** Net interest income (as Revenue), PPOP (as EBITDA/Op. profit), PBT, net income, EPS, ROA
- **Manufacturing:** Revenue, EBITDA, PBIT, PBT, net income, EPS (ROA shown as N/A)
- Optional EBITDA definition and XBRL tag debugging for non-bank filers

## Requirements

- Python 3.10+ (3.12 tested)
- Network access to NSE (downloads only; no API key)

## Installation

Clone the repository and install dependencies:

```powershell
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

### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--symbols` | Yes | — | One or more NSE symbols (space-separated) |
| `--quarter` | Yes | — | Quarter code (`Q4_FY26`) or period end (`31-Mar-2026`) |
| `--back-quarters` | No | `1` | How many quarters to process, counting back from `--quarter` (includes that quarter) |
| `--output` | No | `.\xbrl_downloads` | Folder where XBRL files are saved |
| `--timeout` | No | `20` | HTTP timeout per request (seconds) |
| `--delay` | No | `2.0` | Pause between symbol requests (seconds) |
| `--ebitda-definition` | No | `tickertape` | Manufacturing EBITDA: `tickertape` or `subtract-other-income` |
| `--debug-tags` | No | off | Print candidate XBRL tags used for EBITDA-related fields |

### Examples

Single quarter, two companies:

```powershell
python fin_report.py --symbols RELIANCE HDFCBANK --quarter Q4_FY26
```

Last four quarters for one symbol:

```powershell
python -m fin_reporter --symbols ITC --quarter Q4_FY26 --back-quarters 4
```

Custom download directory and slower requests (helps avoid NSE rate limits):

```powershell
python fin_report.py --symbols SBIN ICICIBANK --quarter Q2_FY26 --output D:\data\xbrl --delay 3
```

Manufacturing EBITDA excluding other income, with tag discovery:

```powershell
python fin_report.py --symbols RELIANCE --quarter Q3_FY26 --ebitda-definition subtract-other-income --debug-tags
```

Re-run without hitting NSE (files already in `--output`):

```powershell
python fin_report.py --symbols RELIANCE --quarter Q4_FY26 --output .\xbrl_downloads
```

If every symbol/quarter file exists locally, you will see: `All requested XBRL files found locally; skipping NSE session.`

## Output

1. **Download results table** — per symbol/quarter: period, consolidated vs standalone basis, status (`DOWNLOADED`, `NOT_FOUND`, `FAILED`, `ERROR`), API source, file path, and message.
2. **Financial metrics table** — amounts in ₹ crore where applicable; banks use NII/PPOP labels, manufacturing uses revenue/EBITDA/PBIT.

Quarter codes follow Indian FY convention: `Q4_FY26` is the quarter ending 31-Mar-2026; `Q1_FY26` ends 30-Jun-2025, and so on.

## Project layout

```
FinancialReporter/
├── fin_report.py          # Thin CLI wrapper
├── fin_reporter/
│   ├── cli.py             # Argument parsing and main flow
│   ├── downloader.py      # NSE session and XBRL download
│   ├── xbrl_parser.py     # XBRL fact extraction
│   ├── period_resolver.py # Quarter contexts and Q4 deltas
│   ├── display.py         # Console tables
│   └── metrics/           # Bank vs manufacturing calculators
├── requirements.txt
└── xbrl_downloads/        # Default cache (gitignored; created at runtime)
```

## Notes and limitations

- NSE may block or throttle automated access; use `--delay` and retry if downloads fail.
- Not every symbol/quarter has an XBRL filing on NSE; missing filings appear as `NOT_FOUND` in the results table.
- Metrics depend on tags present in each filing; warnings may appear when expected tags are missing.
- Cached XBRL under `xbrl_downloads/` is not committed to the repository; delete files to force a fresh download.

## License

No license file is included yet. Add one if you plan to distribute or open-source the project further.
