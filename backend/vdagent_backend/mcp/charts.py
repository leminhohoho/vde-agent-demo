"""Vega-Lite v5 specs for `create_chart` (§6.3)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"
CHART_KINDS = ("bar", "line", "pie")
MAX_CHART_ROWS = 10_000
_ISO_DATE_PREFIX = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUMERIC_TYPES = ("INTEGER", "REAL")


class ChartError(Exception):
    """A user-facing chart failure; its message becomes the tool error text."""


def _is_iso_date(value: str) -> bool:
    """Extended ISO-8601 date or date-time (`2025-01-31`, `2025-01-31T10:00:00Z`), as JS dates parse."""
    if not _ISO_DATE_PREFIX.match(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_temporal(name: str, values: list[Any]) -> bool:
    """Temporal if the name contains `date` or every value parses as an ISO date.

    Numeric values are never temporal: Vega-Lite would read an integer like `date_key` (yyyymmdd)
    as epoch milliseconds, so such columns stay nominal.
    """
    present = [v for v in values if v is not None]
    if any(not isinstance(v, str) for v in present):
        return False
    return "date" in name.lower() or (bool(present) and all(_is_iso_date(v) for v in present))


def build_chart_spec(dataset: dict[str, Any], kind: str, x: str, y: list[str], title: str) -> dict[str, Any]:
    if kind not in CHART_KINDS:
        raise ChartError(f"kind must be one of {', '.join(CHART_KINDS)}")
    if not y:
        raise ChartError("y must name at least one column")
    types = {c["name"]: c["type"] for c in dataset["columns"]}
    missing = [c for c in dict.fromkeys([x, *y]) if c not in types]
    if missing:
        raise ChartError(f"column not found: {', '.join(missing)} (columns: {', '.join(types)})")
    non_numeric = [c for c in y if types[c] not in _NUMERIC_TYPES]
    if non_numeric:
        raise ChartError(f"y columns must be numeric: {', '.join(non_numeric)}")

    index = {c["name"]: i for i, c in enumerate(dataset["columns"])}
    fields = list(dict.fromkeys([x, *y]))
    values = [{f: row[index[f]] for f in fields} for row in dataset["rows"][:MAX_CHART_ROWS]]
    spec: dict[str, Any] = {"$schema": VEGA_LITE_SCHEMA, "title": title, "data": {"values": values}}

    if kind == "pie":
        spec["mark"] = {"type": "arc", "tooltip": True}
        spec["encoding"] = {
            "theta": {"field": y[0], "type": "quantitative"},
            "color": {"field": x, "type": "nominal"},
        }
        return spec

    x_type = "temporal" if _is_temporal(x, [v[x] for v in values]) else "nominal"
    spec["mark"] = {"type": kind, "tooltip": True}
    if len(y) == 1:
        spec["encoding"] = {
            "x": {"field": x, "type": x_type},
            "y": {"field": y[0], "type": "quantitative"},
        }
        return spec

    series, value = ("series", "value") if x not in ("series", "value") else ("_series", "_value")
    spec["transform"] = [{"fold": y, "as": [series, value]}]
    encoding: dict[str, Any] = {
        "x": {"field": x, "type": x_type},
        "y": {"field": value, "type": "quantitative", "title": "value"},
        "color": {"field": series, "type": "nominal", "title": None},
    }
    if kind == "bar" and x_type == "nominal":
        encoding["xOffset"] = {"field": series}  # grouped bars
    spec["encoding"] = encoding
    return spec
