"""XBRL XML parsing, context extraction, and fact extraction.

This module handles the low-level parsing of XBRL (eXtensible Business
Reporting Language) files as filed by Indian companies on NSE/BSE.

Key concepts:
    - **Context**: An XBRL context defines the time period and dimensional
      qualifiers for a reported fact. Contexts can be "duration" (start/end
      date) or "instant" (single point in time).
    - **Fact**: A single reported value (e.g., Revenue = 24386500000000)
      tied to a specific context.
    - **Consolidated vs Standalone**: Filings may contain both consolidated
      and standalone data, distinguished by dimensional members on contexts.
"""

import datetime as dt
import zipfile
from xml.etree import ElementTree as ET

from fin_reporter.constants import (
    CANONICAL_NAMESPACE_TOKENS,
    COMPANY_NAME_TAGS,
    CONSOLIDATED_AXES,
    CONSOLIDATED_MEMBERS,
    FILING_NATURE_TAG,
)
from fin_reporter.models import FilingMetadata


# ─── Low-level XML helpers ───────────────────────────────────────────────────


def local_name(tag_name: str) -> str:
    """Extract the local name from a Clark-notation tag like ``{uri}local``."""
    if "}" in tag_name:
        return tag_name.split("}", maxsplit=1)[1]
    return tag_name


def namespace_uri(tag_name: str) -> str:
    """Extract the namespace URI from a Clark-notation tag."""
    if "}" in tag_name and tag_name.startswith("{"):
        return tag_name[1:].split("}", maxsplit=1)[0]
    return ""


def to_number(raw_value) -> float | None:
    """Parse a string value into a float, handling commas and parenthetical negatives."""
    if raw_value is None:
        return None
    cleaned = str(raw_value).strip()
    if not cleaned:
        return None
    cleaned = cleaned.replace(",", "")
    # Accounting convention: (1234) means -1234
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"
    try:
        return float(cleaned)
    except ValueError:
        return None


def format_metric(raw_number: float | None, scale: float = 1.0) -> str:
    """Format a numeric metric for display, dividing by ``scale``."""
    if raw_number is None:
        return "-"
    return f"{(raw_number / scale):.2f}".rstrip("0").rstrip(".")


# ─── File I/O ────────────────────────────────────────────────────────────────


def read_xbrl_text(file_path: str) -> bytes | None:
    """Read XBRL content from an XML, XBRL, or ZIP file."""
    lower_path = file_path.lower()
    if lower_path.endswith((".xml", ".xbrl")):
        with open(file_path, "rb") as source:
            return source.read()
    if lower_path.endswith(".zip"):
        with zipfile.ZipFile(file_path, "r") as archive:
            members = archive.namelist()
            xml_members = [
                name
                for name in members
                if name.lower().endswith((".xml", ".xbrl"))
            ]
            if not xml_members:
                return None
            with archive.open(xml_members[0]) as source:
                return source.read()
    return None


# ─── Namespace matching ──────────────────────────────────────────────────────


def matches_namespace(ns_uri: str, namespace_mode: str | None) -> bool:
    """Check if a namespace URI matches the expected reporting taxonomy.

    Args:
        ns_uri: The namespace URI from an XBRL element.
        namespace_mode: One of "ind-as", "in-gaap", "either", or None (match all).
    """
    if not namespace_mode:
        return True
    tokens = CANONICAL_NAMESPACE_TOKENS.get(namespace_mode, ())
    ns_lower = ns_uri.lower()
    return any(token in ns_lower for token in tokens)


# ─── QName helpers ───────────────────────────────────────────────────────────


def qname_local_token(raw_qname: str) -> str:
    """Extract the local part from a prefixed QName like ``prefix:local``."""
    if not raw_qname:
        return ""
    return str(raw_qname).split(":")[-1].strip()


def parse_date_safe(raw_value) -> dt.date | None:
    """Parse a YYYY-MM-DD date string, returning None on failure."""
    try:
        return dt.datetime.strptime(raw_value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


# ─── Context extraction ─────────────────────────────────────────────────────


def extract_context_metadata(root: ET.Element) -> dict:
    """Extract metadata from all ``<context>`` elements in the XBRL document.

    Returns a dict keyed by context ID, with each value containing:
        - is_consolidated: bool
        - has_dimensions: bool
        - duration_days: int | None
        - start_date: date | None
        - end_date: date | None
        - instant_date: date | None
    """
    metadata = {}
    for node in root.iter():
        if local_name(node.tag) != "context":
            continue
        context_id = node.attrib.get("id", "")
        if not context_id:
            continue

        dimensions = []
        start_date = None
        end_date = None
        instant = None
        for child in node.iter():
            child_local = local_name(child.tag)
            if child_local == "explicitMember":
                axis_local = qname_local_token(
                    child.attrib.get("dimension", "")
                )
                member_local = qname_local_token((child.text or "").strip())
                dimensions.append((axis_local, member_local))
            elif child_local == "startDate":
                start_date = (child.text or "").strip()
            elif child_local == "endDate":
                end_date = (child.text or "").strip()
            elif child_local == "instant":
                instant = (child.text or "").strip()

        is_consolidated = any(
            axis in CONSOLIDATED_AXES and member in CONSOLIDATED_MEMBERS
            for axis, member in dimensions
        )

        duration_days = None
        start_parsed = parse_date_safe(start_date)
        end_parsed = parse_date_safe(end_date)
        if start_parsed and end_parsed:
            duration_days = (end_parsed - start_parsed).days + 1

        metadata[context_id] = {
            "is_consolidated": is_consolidated,
            "has_dimensions": len(dimensions) > 0,
            "duration_days": duration_days,
            "start_date": parse_date_safe(start_date),
            "end_date": parse_date_safe(end_date),
            "instant_date": parse_date_safe(instant),
        }
    return metadata


# ─── Fact extraction ─────────────────────────────────────────────────────────


def extract_facts(file_path: str) -> tuple[dict, dict]:
    """Extract all numeric facts from an XBRL file.

    Prefers consolidated contexts when explicit consolidated dimensional
    members are present. Falls back to non-dimensional (base) contexts
    for filings that are already consolidated and don't use dimensions.

    Returns:
        (facts, contexts) where:
        - facts: dict mapping tag_name → list of fact entries
        - contexts: dict mapping context_id → context metadata
    """
    content = read_xbrl_text(file_path)
    if not content:
        return {}, {}
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return {}, {}

    contexts = extract_context_metadata(root)
    has_explicit_consolidated = any(
        ctx["is_consolidated"] for ctx in contexts.values()
    )

    facts: dict[str, list[dict]] = {}
    for elem in root.iter():
        # Skip container elements (only process leaf nodes with text)
        if len(elem):
            continue
        text = (elem.text or "").strip()
        if not text:
            continue
        context_ref = elem.attrib.get("contextRef", "").strip()
        if not context_ref:
            continue
        context_meta = contexts.get(context_ref)
        if not context_meta:
            continue

        # Context filtering: prefer consolidated data
        if has_explicit_consolidated:
            if not context_meta["is_consolidated"]:
                continue
        else:
            # Fallback: filings that are already consolidated often keep
            # primary facts in base (non-dimensional) contexts like OneD/FourD.
            if context_meta["has_dimensions"]:
                continue

        numeric_value = to_number(text)
        if numeric_value is None:
            continue

        local = local_name(elem.tag)
        ns_uri = namespace_uri(elem.tag)
        facts.setdefault(local, []).append(
            {
                "value": numeric_value,
                "context_ref": context_ref,
                "namespace_uri": ns_uri,
                "has_dimensions": context_meta["has_dimensions"],
                "duration_days": context_meta["duration_days"],
                "start_date": context_meta["start_date"],
                "end_date": context_meta["end_date"],
                "instant_date": context_meta["instant_date"],
            }
        )
    return facts, contexts


# ─── Filing metadata extraction ──────────────────────────────────────────────


def extract_filing_metadata(file_path: str) -> FilingMetadata:
    """Read filing metadata (nature, company name) from XBRL content.

    This reads the ``NatureOfReportStandaloneConsolidated`` and company
    name tags without performing full fact extraction.
    """
    content = read_xbrl_text(file_path)
    if not content:
        return FilingMetadata()
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return FilingMetadata()

    nature = "Unknown"
    company_name = "Unknown"
    reporting_period = "Unknown"

    for elem in root.iter():
        tag = local_name(elem.tag)
        text = (elem.text or "").strip()
        if not text:
            continue
        if tag == FILING_NATURE_TAG and nature == "Unknown":
            nature = text
        elif tag in COMPANY_NAME_TAGS and company_name == "Unknown":
            company_name = text
        elif tag == "TypeOfReportingPeriod" and reporting_period == "Unknown":
            reporting_period = text

    return FilingMetadata(
        nature=nature,
        reporting_period=reporting_period,
        company_name=company_name,
    )


# ─── Fact selection and picking ──────────────────────────────────────────────


def select_entries(
    facts: dict,
    tag_candidates: tuple[str, ...],
    *,
    namespace_mode: str | None = None,
    context_ref: str | None = None,
    duration_between: tuple[int, int] | None = None,
    end_date: dt.date | None = None,
    instant_date: dt.date | None = None,
    no_dimensions: bool | None = None,
) -> list[dict]:
    """Select fact entries matching the given filter criteria.

    Args:
        facts: The parsed facts dictionary from ``extract_facts``.
        tag_candidates: XBRL tag names to search, in priority order.
        namespace_mode: Namespace filter ("ind-as", "in-gaap", "either", None).
        context_ref: If set, only match this exact context reference.
        duration_between: (min_days, max_days) for the context's duration.
        end_date: If set, only match contexts ending on this date.
        instant_date: If set, only match instant contexts on this date.
        no_dimensions: If True, exclude dimensional contexts. If False,
            exclude non-dimensional. If None, allow both.
    """
    selected = []
    for tag_name in tag_candidates:
        for entry in facts.get(tag_name, []):
            if not matches_namespace(
                entry.get("namespace_uri", ""), namespace_mode
            ):
                continue
            if context_ref and entry.get("context_ref") != context_ref:
                continue
            if no_dimensions is True and entry.get("has_dimensions"):
                continue
            if no_dimensions is False and not entry.get("has_dimensions"):
                continue
            if end_date and entry.get("end_date") != end_date:
                continue
            if instant_date and entry.get("instant_date") != instant_date:
                continue
            if duration_between:
                duration = entry.get("duration_days")
                if duration is None:
                    continue
                min_days, max_days = duration_between
                if duration < min_days or duration > max_days:
                    continue
            selected.append({"tag": tag_name, **entry})
    return selected


def entry_sort_key(entry: dict, target_end_date: dt.date) -> tuple:
    """Sort key for ranking fact entries by relevance to a target quarter."""
    duration = entry.get("duration_days")
    duration_penalty = abs(duration - 90) if duration is not None else 9999
    end_date = entry.get("end_date")
    end_penalty = abs((end_date - target_end_date).days) if end_date else 9999
    dim_penalty = 1 if entry.get("has_dimensions") else 0
    return (dim_penalty, end_penalty, duration_penalty)


def pick_numeric(
    entries: list[dict],
    target_end_date: dt.date | None = None,
) -> tuple[float | None, dict | None]:
    """Pick the best numeric value from a list of candidate fact entries.

    Returns (value, entry) or (None, None) if no entries.
    """
    if not entries:
        return None, None
    if target_end_date:
        ranked = sorted(
            entries, key=lambda item: entry_sort_key(item, target_end_date)
        )
    else:
        ranked = sorted(
            entries, key=lambda item: (1 if item.get("has_dimensions") else 0)
        )
    top = ranked[0]
    return top["value"], top
