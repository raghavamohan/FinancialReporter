"""Data classes for download results, filing metadata, and financial metrics."""

from dataclasses import dataclass, field


@dataclass
class DownloadResult:
    """Result of attempting to download an XBRL filing for a symbol/period."""

    symbol: str
    period: str
    status: str  # "DOWNLOADED", "NOT_FOUND", "FAILED", "ERROR"
    file_path: str
    message: str
    source: str = "-"  # "integrated", "legacy", or "-"
    filing_basis: str = "unknown"  # "consolidated", "standalone", or "unknown"
    quarter_label: str = "-"  # Requested quarter token (e.g. "Q4_FY26")


@dataclass
class FilingMetadata:
    """Metadata extracted from a downloaded XBRL filing's content."""

    nature: str = "Unknown"  # "Consolidated" or "Standalone"
    reporting_period: str = "Unknown"  # "Quarterly" or "Annual"
    company_name: str = "Unknown"


@dataclass
class FinancialMetrics:
    """Computed financial metrics for one company-quarter.

    Banks and manufacturing companies have different applicable metrics.
    Use ``company_type`` to determine which fields are meaningful.

    For banks: nii, total_income, ppop, roa, gnpa_pct, nnpa_pct are relevant.
    For manufacturing: revenue, ebitda, pbit are relevant.
    Common: pbt, net_income, basic_eps, diluted_eps.
    """

    company_type: str = "unknown"  # "bank" or "manufacturing"
    filing_nature: str = "Unknown"  # "Consolidated" or "Standalone"

    # ── Common metrics ──────────────────────────────────────────────
    pbt: float | None = None
    net_income: float | None = None
    basic_eps: float | None = None
    diluted_eps: float | None = None
    trailing_eps: float | None = None  # Sum of basic EPS for current + prior 3 quarters
    share_price: float | None = None  # NSE EQ close on report period end
    pe_ratio: float | None = None  # share_price / trailing_eps
    quarter_dividend: float | None = None  # Sum of per-share dividends (NSE corporate actions)

    # ── Manufacturing-specific (Ind-AS) ─────────────────────────────
    revenue: float | None = None  # Revenue from Operations
    ebitda: float | None = None  # PBT + Finance Cost + D&A
    pbit: float | None = None  # PBT + Finance Cost (= EBIT)

    # ── Banking-specific (IN-GAAP) ──────────────────────────────────
    nii: float | None = None  # Net Interest Income
    total_income: float | None = None  # Interest Earned + Other Income
    ppop: float | None = None  # Pre-Provision Operating Profit
    roa: float | None = None  # Return on Assets (annualized %)
    gnpa_pct: float | None = None  # Gross NPA %
    nnpa_pct: float | None = None  # Net NPA %

    # ── Debug / provenance ──────────────────────────────────────────
    warnings: list[str] = field(default_factory=list)
