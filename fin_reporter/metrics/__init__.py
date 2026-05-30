"""Financial metric dispatcher — routes to bank or manufacturing calculator."""

from fin_reporter.metrics.base import detect_company_type
from fin_reporter.metrics.banking import build_bank_metrics
from fin_reporter.metrics.manufacturing import build_manufacturing_metrics
from fin_reporter.models import FinancialMetrics
from fin_reporter.xbrl_parser import extract_facts, extract_filing_metadata

__all__ = [
    "build_metrics_from_file",
    "detect_company_type",
]


def build_metrics_from_file(
    file_path: str,
    target_period: str,
    ebitda_definition: str = "tickertape",
) -> FinancialMetrics:
    """Build financial metrics from an XBRL file.

    Auto-detects whether the filing is from a bank or manufacturing company
    and dispatches to the appropriate calculator.

    Args:
        file_path: Path to the XBRL file (.xml, .xbrl, or .zip).
        target_period: Target period date string (e.g. "31-Mar-2026").
        ebitda_definition: EBITDA formula mode for manufacturing companies.

    Returns:
        FinancialMetrics with populated fields appropriate to the company type.
    """
    facts, _contexts = extract_facts(file_path)
    if not facts:
        return FinancialMetrics(
            warnings=["No parseable XBRL facts found in file"],
        )

    filing_meta = extract_filing_metadata(file_path)
    company_type = detect_company_type(facts)

    if company_type == "bank":
        metrics = build_bank_metrics(facts, target_period, file_path)
    else:
        metrics = build_manufacturing_metrics(
            facts,
            target_period,
            ebitda_definition=ebitda_definition,
        )

    metrics.company_type = company_type
    metrics.filing_nature = filing_meta.nature
    return metrics
