"""Table formatting and console output for download results and financial metrics.

Provides two main display functions:
    - ``print_results_table``: Shows download status for each symbol
    - ``print_metric_table``: Shows computed financial metrics per symbol

Also includes tag discovery debugging helpers for EBITDA component analysis.
"""

import datetime as dt
import os

from fin_reporter.constants import (
    CRORE_DIVISOR,
    MANUFACTURING_DEPRECIATION_TAGS,
    MANUFACTURING_FINANCE_COST_TAGS,
    MANUFACTURING_PBT_TAGS,
    OTHER_INCOME_TAGS,
)
from fin_reporter.market_data import (
    corporate_action_factor_between,
    compute_pe_ratio,
    fetch_other_corporate_actions_summary,
    fetch_quarter_dividend_per_share,
    fetch_share_price_on_date,
    fetch_share_restructuring_events,
    format_dividend_per_share,
    format_pe_ratio,
    trailing_basic_eps_sum,
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
            ("Cost-to-Income Ratio (%)", "cost_to_income"),
            ("PPOP", "ppop"),
            ("PBT", "pbt"),
            ("Net Income", "net_income"),
            ("Basic EPS", "basic_eps"),
            ("P/E Ratio", "pe_ratio"),
            ("P/B Ratio", "pb_ratio"),
            ("Dividend (Rs/sh)", "quarter_dividend"),
            ("ROE (%)", "roe"),
            ("ROA (%)", "roa"),
            ("Gross NPA (%)", "gnpa_pct"),
            ("Net NPA (%)", "nnpa_pct"),
            ("Other Corporate Actions", "other_corporate_actions"),
        ])
    elif company_types == {"manufacturing"}:
        metric_rows.extend([
            ("Revenue from Operations", "revenue"),
            ("Gross Profit", "gross_profit"),
            ("Gross Margin (%)", "gross_margin"),
            ("EBITDA", "ebitda"),
            ("EBITDA Margin (%)", "ebitda_margin"),
            ("PBIT", "pbit"),
            ("PBT", "pbt"),
            ("Net Income", "net_income"),
            ("Net Profit Margin (%)", "net_margin"),
            ("Basic EPS", "basic_eps"),
            ("P/E Ratio", "pe_ratio"),
            ("P/B Ratio", "pb_ratio"),
            ("Dividend (Rs/sh)", "quarter_dividend"),
            ("ROE (%)", "roe"),
            ("ROCE (%)", "roce"),
            ("ROA (%)", "roa"),
            ("Other Corporate Actions", "other_corporate_actions"),
        ])
    else:
        metric_rows.extend([
            ("Revenue / Total Income", "revenue_or_nii"),
            ("EBITDA / PPOP", "ebitda_or_ppop"),
            ("PBIT", "pbit"),
            ("PBT", "pbt"),
            ("Net Income", "net_income"),
            ("Basic EPS", "basic_eps"),
            ("P/E Ratio", "pe_ratio"),
            ("P/B Ratio", "pb_ratio"),
            ("Dividend (Rs/sh)", "quarter_dividend"),
            ("ROE (%)", "roe"),
            ("ROA (%)", "roa"),
            ("Other Corporate Actions", "other_corporate_actions"),
        ])
    return metric_rows


def _latest_period_end_for_results(
    results: list[DownloadResult],
    symbol: str,
) -> dt.date | None:
    """Latest period-end date among downloaded filings for one symbol."""
    latest: dt.date | None = None
    for result in results:
        if result.symbol != symbol or result.status != "DOWNLOADED":
            continue
        try:
            period_end = dt.datetime.strptime(result.period, "%d-%b-%Y").date()
        except ValueError:
            continue
        if latest is None or period_end > latest:
            latest = period_end
    return latest


def _enrich_market_metrics(
    metrics: FinancialMetrics,
    file_path: str,
    target_period: str,
    symbol: str,
    ebitda_definition: str,
    market_downloader,
    pe_anchor_date: dt.date | None = None,
) -> FinancialMetrics:
    """Attach trailing EPS, share price, P/E, and quarterly dividend to metrics."""
    try:
        period_end = dt.datetime.strptime(target_period, "%d-%b-%Y").date()
    except ValueError:
        period_end = None

    restructuring_events = None
    nse_symbol = None
    cache_dir = os.path.dirname(file_path)
    if market_downloader is not None and period_end is not None:
        nse_symbol = market_downloader.normalize_symbol(symbol)
        restructuring_events = fetch_share_restructuring_events(
            market_downloader,
            nse_symbol,
            cache_dir=cache_dir,
        )

    eps_anchor = pe_anchor_date or period_end
    metrics.trailing_eps = trailing_basic_eps_sum(
        file_path,
        target_period,
        ebitda_definition=ebitda_definition,
        restructuring_events=restructuring_events,
        report_date=period_end,
        eps_anchor_date=eps_anchor,
    )

    if market_downloader is not None and period_end is not None and nse_symbol:
        share_price = fetch_share_price_on_date(
            market_downloader,
            nse_symbol,
            period_end,
            restructuring_events=restructuring_events,
            cache_dir=cache_dir,
        )
        if (
            share_price is not None
            and restructuring_events
            and eps_anchor is not None
            and period_end < eps_anchor
        ):
            share_price *= corporate_action_factor_between(
                period_end,
                eps_anchor,
                restructuring_events,
            )
        metrics.share_price = share_price
        metrics.quarter_dividend = fetch_quarter_dividend_per_share(
            market_downloader,
            nse_symbol,
            period_end,
            cache_dir=cache_dir,
        )
        metrics.other_corporate_actions = fetch_other_corporate_actions_summary(
            market_downloader,
            nse_symbol,
            period_end,
            cache_dir=cache_dir,
        )

    metrics.pe_ratio = compute_pe_ratio(metrics.share_price, metrics.trailing_eps)

    # Compute P/B Ratio
    if metrics.share_price is not None and metrics.equity is not None and metrics.equity > 0:
        if metrics.net_income is not None and metrics.basic_eps not in (None, 0):
            implied_shares = abs(metrics.net_income) / abs(metrics.basic_eps)
            if implied_shares > 0:
                bvps = metrics.equity / implied_shares
                if bvps > 0:
                    metrics.pb_ratio = metrics.share_price / bvps

    return metrics


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
    row_values["roa"] = _raw(metrics.roa)
    row_values["gnpa_pct"] = _raw(metrics.gnpa_pct)
    row_values["nnpa_pct"] = _raw(metrics.nnpa_pct)
    row_values["filing_basis"] = metrics.filing_nature or "Unknown"

    row_values["gross_profit"] = _cr(metrics.gross_profit) if not is_bank else "-"
    row_values["gross_margin"] = _raw(metrics.gross_margin) if not is_bank else "-"
    row_values["ebitda_margin"] = _raw(metrics.ebitda_margin) if not is_bank else "-"
    row_values["net_margin"] = _raw(metrics.net_margin) if not is_bank else "-"
    row_values["roe"] = _raw(metrics.roe)
    row_values["roce"] = _raw(metrics.roce) if not is_bank else "-"
    row_values["cost_to_income"] = _raw(metrics.cost_to_income) if is_bank else "-"

    row_values["revenue_or_nii"] = (
        _cr(metrics.total_income) if is_bank else _cr(metrics.revenue)
    )
    row_values["nii_mixed"] = _cr(metrics.nii) if is_bank else "-"
    row_values["ebitda_or_ppop"] = (
        _cr(metrics.ppop) if is_bank else _cr(metrics.ebitda)
    )
    row_values["pe_ratio"] = format_pe_ratio(metrics.pe_ratio)
    row_values["pb_ratio"] = _raw(metrics.pb_ratio)
    row_values["quarter_dividend"] = format_dividend_per_share(
        metrics.quarter_dividend
    )
    row_values["other_corporate_actions"] = (
        metrics.other_corporate_actions or "-"
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
    market_downloader=None,
    display_quarters: list[str] | None = None,
) -> None:
    """Print financial metrics in symbol or multi-quarter grouped view."""
    basis_note = (
        "Filing Basis shows the primary downloaded filing; "
        "NPA/ROA may use standalone fallback when consolidated values are missing. "
        "P/E uses closing price on the report date (NSE, with Yahoo fallback converted for "
        "split-adjusted history) and trailing "
        "four-quarter basic EPS adjusted for bonus/split ex-dates (multi-quarter tables use "
        "the latest column as anchor; prior quarters are downloaded automatically when needed). "
        "Dividend sums per-share amounts from NSE corporate actions with ex-date in the "
        "reporting quarter. Other Corporate Actions shows non-dividend actions by "
        "reporting-quarter ex-date."
    )
    display_labels = (
        {token.strip().upper() for token in display_quarters}
        if display_quarters
        else None
    )
    successful = [
        result
        for result in results
        if result.status == "DOWNLOADED" and result.file_path != "-"
        and (
            display_labels is None
            or (result.quarter_label or quarter).strip().upper() in display_labels
        )
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
            metrics = build_metrics_from_file(
                result.file_path,
                result.period,
                ebitda_definition=ebitda_definition,
            )
            raw_metrics[result.symbol] = _enrich_market_metrics(
                metrics,
                result.file_path,
                result.period,
                result.symbol,
                ebitda_definition,
                market_downloader,
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
        symbol_anchors: dict[str, dt.date | None] = {}
        for result in successful:
            symbol = result.symbol
            if symbol not in symbols:
                symbols.append(symbol)
                symbol_anchors[symbol] = _latest_period_end_for_results(
                    successful,
                    symbol,
                )
        for result in successful:
            symbol = result.symbol
            quarter_label = (result.quarter_label or quarter).upper()
            metrics = build_metrics_from_file(
                result.file_path,
                result.period,
                ebitda_definition=ebitda_definition,
            )
            symbol_metrics.setdefault(symbol, {})[quarter_label] = (
                _enrich_market_metrics(
                    metrics,
                    result.file_path,
                    result.period,
                    symbol,
                    ebitda_definition,
                    market_downloader,
                    pe_anchor_date=symbol_anchors.get(symbol),
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
        debug_results = [
            result
            for result in results
            if result.status == "DOWNLOADED" and result.file_path != "-"
        ]
        _print_ebitda_tag_discovery(debug_results)
