"""Financial metric dispatcher — routes to bank or manufacturing calculator."""

from fin_reporter.metrics.base import detect_company_type
from fin_reporter.metrics.banking import build_bank_metrics
from fin_reporter.metrics.manufacturing import build_manufacturing_metrics, normalize_ebitda_definition
from fin_reporter.models import FinancialMetrics
from fin_reporter.xbrl_parser import extract_facts, parse_xbrl

__all__ = [
    "build_metrics_from_file",
    "detect_company_type",
]


_METRICS_CACHE = {}

def build_metrics_from_file(
    file_path: str,
    target_period: str,
    ebitda_definition: str = "include-other-income",
) -> FinancialMetrics:
    """Build financial metrics from an XBRL file (with in-memory caching).

    Auto-detects whether the filing is from a bank or manufacturing company
    and dispatches to the appropriate calculator.

    Args:
        file_path: Path to the XBRL file (.xml, .xbrl, or .zip).
        target_period: Target period date string (e.g. "31-Mar-2026").
        ebitda_definition: EBITDA formula mode for manufacturing companies
            (``include-other-income`` or ``exclude-other-income``).

    Returns:
        FinancialMetrics with populated fields appropriate to the company type.
    """
    ebitda_definition = normalize_ebitda_definition(ebitda_definition)
    key = (file_path, target_period, ebitda_definition)
    if key in _METRICS_CACHE:
        return _METRICS_CACHE[key]

    parsed = parse_xbrl(file_path)
    facts = parsed.facts
    if not facts:
        metrics = FinancialMetrics(
            warnings=["No parseable XBRL facts found in file"],
        )
        _METRICS_CACHE[key] = metrics
        return metrics

    filing_meta = parsed.metadata
    company_type = detect_company_type(facts)

    if company_type == "bank":
        metrics = build_bank_metrics(facts, target_period, file_path)
    else:
        metrics = build_manufacturing_metrics(
            facts,
            target_period,
            file_path=file_path,
            ebitda_definition=ebitda_definition,
        )

    metrics.company_type = company_type
    metrics.filing_nature = filing_meta.nature
    _METRICS_CACHE[key] = metrics
    return metrics
