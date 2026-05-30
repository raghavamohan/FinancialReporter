"""Table formatting and console output for download results and financial metrics.

Provides two main display functions:
    - ``print_results_table``: Shows download status for each symbol
    - ``print_metric_table``: Shows computed financial metrics per symbol

Also includes tag discovery debugging helpers for EBITDA component analysis.
"""

from fin_reporter.constants import (
    CRORE_DIVISOR,
    MANUFACTURING_DEPRECIATION_TAGS,
    MANUFACTURING_FINANCE_COST_TAGS,
    MANUFACTURING_PBT_TAGS,
    OTHER_INCOME_TAGS,
)
from fin_reporter.metrics import build_metrics_from_file
from fin_reporter.models import DownloadResult, FinancialMetrics
from fin_reporter.xbrl_parser import (
    extract_facts,
    format_metric,
    pick_numeric,
    select_entries,
)


# ─── Download results table ──────────────────────────────────────────────────


def print_results_table(
    results: list[DownloadResult],
    quarter: str,
) -> None:
    """Print a formatted table of download results."""
    if not results:
        print("[Info] No results to display.")
        return

    columns = (
        "Symbol",
        "Quarter",
        "Period",
        "Basis",
        "Status",
        "Source",
        "File",
        "Message",
    )
    rows = [
        (
            result.symbol,
            (result.quarter_label or quarter).upper(),
            result.period,
            result.filing_basis,
            result.status,
            result.source,
            result.file_path,
            result.message,
        )
        for result in results
    ]
    widths = []
    for col_idx, col_name in enumerate(columns):
        max_value = max(len(str(row[col_idx])) for row in rows)
        widths.append(max(len(col_name), max_value))

    divider = "+-" + "-+-".join("-" * width for width in widths) + "-+"
    header = "| " + " | ".join(
        columns[i].ljust(widths[i]) for i in range(len(columns))
    ) + " |"

    print("")
    print(divider)
    print(header)
    print(divider)
    for row in rows:
        line = "| " + " | ".join(
            str(row[i]).ljust(widths[i]) for i in range(len(columns))
        ) + " |"
        print(line)
    print(divider)

    downloaded = sum(1 for r in results if r.status == "DOWNLOADED")
    print(
        f"\n[Summary] Downloaded {downloaded}/{len(results)} "
        f"files for {quarter.upper()}."
    )


# ─── Financial metrics table ─────────────────────────────────────────────────


def _format_metrics_row(
    metrics: FinancialMetrics,
) -> dict[str, str]:
    """Convert a FinancialMetrics object to a display dict keyed by metric ID."""
    cr = CRORE_DIVISOR
    if metrics.company_type == "bank":
        return {
            "Revenue": format_metric(metrics.nii, cr),
            "EBITDA_OpProfit": format_metric(metrics.ppop, cr),
            "PBIT": "-",
            "PBT": format_metric(metrics.pbt, cr),
            "Net_Income": format_metric(metrics.net_income, cr),
            "EPS": format_metric(metrics.basic_eps),
            "ROA": format_metric(metrics.roa),
        }
    # Manufacturing
    return {
        "Revenue": format_metric(metrics.revenue, cr),
        "EBITDA_OpProfit": format_metric(metrics.ebitda, cr),
        "PBIT": format_metric(metrics.pbit, cr),
        "PBT": format_metric(metrics.pbt, cr),
        "Net_Income": format_metric(metrics.net_income, cr),
        "EPS": format_metric(metrics.basic_eps),
        "ROA": "-",
    }


# ─── Tag discovery (debug) ───────────────────────────────────────────────────


def _discover_candidate_tags(
    facts: dict,
    exact_candidates: tuple[str, ...],
    fuzzy_predicate,
    limit: int = 8,
) -> list[tuple[str, float, str, str]]:
    """Discover XBRL tags matching exact candidates and fuzzy predicates.

    Returns list of (tag_name, value, context_ref, match_type) tuples.
    """
    discovered: list[tuple[str, float, str, str]] = []
    seen: set[str] = set()

    for tag_name in exact_candidates:
        entries = select_entries(
            facts,
            (tag_name,),
            namespace_mode="either",
        )
        value, entry = pick_numeric(entries)
        if entry is not None:
            discovered.append(
                (tag_name, value, entry["context_ref"], "exact")
            )
            seen.add(tag_name)

    for tag_name in sorted(facts.keys()):
        if tag_name in seen:
            continue
        if not fuzzy_predicate(tag_name):
            continue
        entries = select_entries(
            facts,
            (tag_name,),
            namespace_mode="either",
        )
        value, entry = pick_numeric(entries)
        if entry is None:
            continue
        discovered.append(
            (tag_name, value, entry["context_ref"], "fuzzy")
        )
        seen.add(tag_name)
        if len(discovered) >= limit:
            break

    return discovered


def _print_ebitda_tag_discovery(
    successful_results: list[DownloadResult],
) -> None:
    """Print discovered XBRL tags relevant to EBITDA computation."""
    print("\n[Debug] EBITDA tag discovery")
    for result in successful_results:
        facts, _contexts = extract_facts(result.file_path)
        print(f"\n  Symbol: {result.symbol}")

        if not facts:
            print("    No parseable XBRL facts found.")
            continue

        sections = (
            (
                "PBT candidates",
                _discover_candidate_tags(
                    facts,
                    MANUFACTURING_PBT_TAGS,
                    lambda tag: "profitbeforetax" in tag.lower(),
                ),
            ),
            (
                "Finance cost candidates",
                _discover_candidate_tags(
                    facts,
                    MANUFACTURING_FINANCE_COST_TAGS,
                    lambda tag: (
                        "finance" in tag.lower()
                        and (
                            "cost" in tag.lower()
                            or "expense" in tag.lower()
                        )
                    ),
                ),
            ),
            (
                "Depreciation/Amortisation candidates",
                _discover_candidate_tags(
                    facts,
                    MANUFACTURING_DEPRECIATION_TAGS,
                    lambda tag: (
                        "depreci" in tag.lower()
                        or "amortis" in tag.lower()
                    ),
                ),
            ),
            (
                "Other income candidates",
                _discover_candidate_tags(
                    facts,
                    OTHER_INCOME_TAGS,
                    lambda tag: (
                        "other" in tag.lower()
                        and "income" in tag.lower()
                    ),
                ),
            ),
        )

        for heading, matches in sections:
            print(f"    {heading}:")
            if not matches:
                print("      - none")
                continue
            for tag_name, value, context_ref, match_type in matches:
                print(
                    f"      - {tag_name} = {value:.2f} "
                    f"({match_type}, context={context_ref})"
                )


def _metric_rows_for_company_types(
    company_types: set[str],
) -> list[tuple[str, str]]:
    """Return display rows based on the participating company types."""
    metric_rows: list[tuple[str, str]] = []
    if company_types == {"bank"}:
        metric_rows.extend([
            ("Net Interest Income", "nii"),
            ("Total Income", "total_income"),
            ("PPOP", "ppop"),
            ("PBT", "pbt"),
            ("Net Income", "net_income"),
            ("Basic EPS", "basic_eps"),
            ("ROA (%)", "roa"),
            ("Gross NPA (%)", "gnpa_pct"),
            ("Net NPA (%)", "nnpa_pct"),
        ])
    elif company_types == {"manufacturing"}:
        metric_rows.extend([
            ("Revenue from Operations", "revenue"),
            ("EBITDA", "ebitda"),
            ("PBIT", "pbit"),
            ("PBT", "pbt"),
            ("Net Income", "net_income"),
            ("Basic EPS", "basic_eps"),
        ])
    else:
        metric_rows.extend([
            ("Revenue", "revenue_or_nii"),
            ("NII", "nii_mixed"),
            ("EBITDA / PPOP", "ebitda_or_ppop"),
            ("PBIT", "pbit"),
            ("PBT", "pbt"),
            ("Net Income", "net_income"),
            ("Basic EPS", "basic_eps"),
            ("ROA (%)", "roa"),
        ])
    return metric_rows


def _format_metric_values(metrics: FinancialMetrics) -> dict[str, str]:
    """Format a FinancialMetrics object into table-ready string values."""
    cr = CRORE_DIVISOR
    is_bank = metrics.company_type == "bank"

    def _cr(val: float | None) -> str:
        return format_metric(val, cr)

    def _raw(val: float | None) -> str:
        return format_metric(val)

    row_values: dict[str, str] = {}
    row_values["revenue"] = _cr(metrics.revenue)
    row_values["ebitda"] = _cr(metrics.ebitda)
    row_values["pbit"] = _cr(metrics.pbit) if not is_bank else "-"
    row_values["pbt"] = _cr(metrics.pbt)
    row_values["net_income"] = _cr(metrics.net_income)
    row_values["basic_eps"] = _raw(metrics.basic_eps)
    row_values["nii"] = _cr(metrics.nii)
    row_values["total_income"] = _cr(metrics.total_income)
    row_values["ppop"] = _cr(metrics.ppop)
    row_values["roa"] = _raw(metrics.roa) if is_bank else "-"
    row_values["gnpa_pct"] = _raw(metrics.gnpa_pct)
    row_values["nnpa_pct"] = _raw(metrics.nnpa_pct)
    row_values["filing_basis"] = metrics.filing_nature or "Unknown"
    row_values["revenue_or_nii"] = (
        _cr(metrics.total_income) if is_bank else _cr(metrics.revenue)
    )
    row_values["nii_mixed"] = _cr(metrics.nii) if is_bank else "-"
    row_values["ebitda_or_ppop"] = (
        _cr(metrics.ppop) if is_bank else _cr(metrics.ebitda)
    )
    return row_values


def _render_metric_grid(
    title: str,
    metric_rows: list[tuple[str, str]],
    columns: list[str],
    formatted: dict[str, dict[str, str]],
    basis_note: str | None = None,
) -> None:
    """Render a metric table where columns can be symbols or quarters."""
    unit_note = "All monetary figures are in Rs Cr."
    label_width = max(
        len("Financial Parameter"),
        *(len(label) for label, _ in metric_rows),
    )
    column_widths: dict[str, int] = {}
    for column in columns:
        max_val_len = max(
            len(str(formatted.get(column, {}).get(key, "-")))
            for _, key in metric_rows
        )
        column_widths[column] = max(len(column), max_val_len)

    table_width = (
        label_width + 3 + sum(column_widths[col] + 3 for col in columns)
    )
    border_width = max(table_width, len(title) + 2, 60)

    print("\n" + "=" * border_width)
    print(f" {title} ")
    print(f" {unit_note} ")
    if basis_note:
        print(f" {basis_note} ")
    print("=" * border_width)
    header = (
        f"{'Financial Parameter':<{label_width}} | "
        + " | ".join(
            f"{column:>{column_widths[column]}}" for column in columns
        )
    )
    print(header)
    print("-" * border_width)
    for label, key in metric_rows:
        row = (
            f"{label:<{label_width}} | "
            + " | ".join(
                f"{str(formatted.get(column, {}).get(key, '-')):>{column_widths[column]}}"
                for column in columns
            )
        )
        print(row)
    print("=" * border_width)


def print_metric_table(
    results: list[DownloadResult],
    quarter: str,
    debug_tags: bool = False,
    ebitda_definition: str = "tickertape",
) -> None:
    """Print financial metrics in symbol or multi-quarter grouped view."""
    basis_note = (
        "Filing Basis shows the primary downloaded filing; "
        "NPA/ROA may use standalone fallback when consolidated values are missing."
    )
    successful = [
        result
        for result in results
        if result.status == "DOWNLOADED" and result.file_path != "-"
    ]
    if not successful:
        print(
            "\n[Info] No downloaded files available for financial metric table."
        )
        return

    quarter_order: list[str] = []
    for result in successful:
        quarter_label = (result.quarter_label or quarter).upper()
        if quarter_label not in quarter_order:
            quarter_order.append(quarter_label)

    multi_quarter = len(quarter_order) > 1
    if not multi_quarter:
        symbols: list[str] = []
        raw_metrics: dict[str, FinancialMetrics] = {}
        for result in successful:
            if result.symbol not in symbols:
                symbols.append(result.symbol)
            raw_metrics[result.symbol] = build_metrics_from_file(
                result.file_path,
                result.period,
                ebitda_definition=ebitda_definition,
            )

        company_types = {metrics.company_type for metrics in raw_metrics.values()}
        metric_rows = _metric_rows_for_company_types(company_types)
        formatted = {
            symbol: _format_metric_values(metrics)
            for symbol, metrics in raw_metrics.items()
        }
        title = f"FINANCIAL METRIC TABLE | PERIOD: {quarter.upper()}"
        _render_metric_grid(
            title,
            metric_rows,
            symbols,
            formatted,
            basis_note=basis_note,
        )
        if "manufacturing" in company_types:
            print(f"[Info] EBITDA definition: {ebitda_definition}")
    else:
        symbols: list[str] = []
        symbol_metrics: dict[str, dict[str, FinancialMetrics]] = {}
        for result in successful:
            symbol = result.symbol
            quarter_label = (result.quarter_label or quarter).upper()
            if symbol not in symbols:
                symbols.append(symbol)
            symbol_metrics.setdefault(symbol, {})[quarter_label] = (
                build_metrics_from_file(
                    result.file_path,
                    result.period,
                    ebitda_definition=ebitda_definition,
                )
            )

        any_manufacturing = False
        for symbol in symbols:
            quarter_metrics = symbol_metrics.get(symbol, {})
            company_types = {
                metrics.company_type for metrics in quarter_metrics.values()
            }
            any_manufacturing = any_manufacturing or (
                "manufacturing" in company_types
            )
            metric_rows = _metric_rows_for_company_types(company_types)
            formatted: dict[str, dict[str, str]] = {}
            for quarter_label in quarter_order:
                metrics = quarter_metrics.get(quarter_label)
                formatted[quarter_label] = (
                    _format_metric_values(metrics) if metrics else {}
                )

            title = f"FINANCIAL METRIC TABLE | SYMBOL: {symbol}"
            _render_metric_grid(
                title,
                metric_rows,
                quarter_order,
                formatted,
                basis_note=basis_note,
            )

        if any_manufacturing:
            print(f"[Info] EBITDA definition: {ebitda_definition}")

    if debug_tags:
        _print_ebitda_tag_discovery(successful)
