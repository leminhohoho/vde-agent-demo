"""MCP tool catalog, per-agent permission matrix and handlers (§6.1).

Handlers return a JSON-able payload (sent as text content) or raise a user-facing error that
becomes an MCP tool error result `error: …`. Blocking sqlite3 work runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

import mcp_types as types
from sqlalchemy.ext.asyncio import AsyncEngine

from vdagent_backend.db import artifacts
from vdagent_backend.mcp import sql
from vdagent_backend.mcp.charts import CHART_KINDS, ChartError, build_chart_spec
from vdagent_backend.tokens import McpIdentity

PREVIEW_ROWS = 20
DEFAULT_PAGE_ROWS = 50
MAX_PAGE_ROWS = 200
_EMBED = re.compile(r"\{\{\s*(chart|dataset)\s*:\s*([^}\s]+)\s*\}\}")

ALL_AGENTS = frozenset({"orchestrator", "data", "compare", "insight", "report"})

# §6.1 permission matrix: tool → agents that see it in tools/list and may call it.
PERMISSIONS: dict[str, frozenset[str]] = {
    "list_tables": frozenset({"data"}),
    "describe_table": frozenset({"data"}),
    "run_query": frozenset({"data"}),
    "describe_dataset": ALL_AGENTS,
    "get_dataset_rows": ALL_AGENTS,
    "query_datasets": frozenset({"data", "compare", "insight"}),
    "create_chart": frozenset({"report"}),
    "save_report": frozenset({"report"}),
}

_DATASET_RESULT = (
    ' Returns {"dataset_id", "name", "columns": [{"name", "type"}], "row_count", "truncated", "preview"}'
    " where preview holds the first 20 rows; results are capped at 10 000 rows (truncated=true)."
)


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


_DATASET_ID = {"type": "string", "description": "Dataset id (ds_…)."}
_DATASET_NAME = {"type": "string", "description": "Optional short name for the new dataset."}

TOOLS: list[types.Tool] = [
    types.Tool(
        name="list_tables",
        description="List the warehouse tables with their row counts.",
        input_schema=_schema({}, []),
    ),
    types.Tool(
        name="describe_table",
        description="Describe a warehouse table: its columns with types and 5 sample rows.",
        input_schema=_schema({"table": {"type": "string", "description": "Warehouse table name."}}, ["table"]),
    ),
    types.Tool(
        name="run_query",
        description=(
            "Run one read-only SQLite SELECT (or WITH … SELECT) statement on the warehouse and store the"
            " result as a new dataset. Queries are limited to 10 seconds." + _DATASET_RESULT
        ),
        input_schema=_schema(
            {"sql": {"type": "string", "description": "A single SELECT / WITH … SELECT statement."}, "name": _DATASET_NAME},
            ["sql"],
        ),
    ),
    types.Tool(
        name="describe_dataset",
        description="Describe a dataset: columns, row count, and per-column min / max / null count.",
        input_schema=_schema({"dataset_id": _DATASET_ID}, ["dataset_id"]),
    ),
    types.Tool(
        name="get_dataset_rows",
        description=f"Read a page of a dataset's rows (at most {MAX_PAGE_ROWS} per call).",
        input_schema=_schema(
            {
                "dataset_id": _DATASET_ID,
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_PAGE_ROWS, "default": DEFAULT_PAGE_ROWS},
            },
            ["dataset_id"],
        ),
    ),
    types.Tool(
        name="query_datasets",
        description=(
            "Run one SQLite SELECT (or WITH … SELECT) over existing datasets and store the result as a new"
            " dataset. Each listed dataset is a table named by its id, e.g."
            ' SELECT a.region, b.revenue - a.revenue AS delta FROM "ds_…" a JOIN "ds_…" b USING (region).'
            + _DATASET_RESULT
        ),
        input_schema=_schema(
            {
                "sql": {"type": "string", "description": "A single SELECT / WITH … SELECT statement."},
                "dataset_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "Datasets the query reads; each is loaded as a table named by its id.",
                },
                "name": _DATASET_NAME,
            },
            ["sql", "dataset_ids"],
        ),
    ),
    types.Tool(
        name="create_chart",
        description=(
            "Build a Vega-Lite chart from a dataset. bar/line: x is the category or date column, each y"
            " column becomes a series; pie: x gives the slices and y[0] their size."
            ' Returns {"chart_id", "embed"}; put the embed text ({{chart:<id>}}) into a report.'
        ),
        input_schema=_schema(
            {
                "dataset_id": _DATASET_ID,
                "kind": {"type": "string", "enum": list(CHART_KINDS)},
                "x": {"type": "string", "description": "Column for the x axis (or pie slices)."},
                "y": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "Numeric column(s) to plot.",
                },
                "title": {"type": "string"},
            },
            ["dataset_id", "kind", "x", "y", "title"],
        ),
    ),
    types.Tool(
        name="save_report",
        description=(
            "Save a markdown report and return its report_id. Embed charts with {{chart:<chart_id>}} and"
            " dataset tables with {{dataset:<dataset_id>}} on their own lines."
        ),
        input_schema=_schema(
            {"title": {"type": "string"}, "markdown": {"type": "string"}},
            ["title", "markdown"],
        ),
    ),
]


class ToolError(Exception):
    """A user-facing tool failure; its message becomes the tool error text."""


def _required_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"'{key}' is required and must be a non-empty string")
    return value.strip()


def _optional_str(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolError(f"'{key}' must be a string")
    return value.strip() or None


def _int(args: dict[str, Any], key: str, default: int, low: int, high: int | None = None) -> int:
    value = args.get(key)
    if value is None:
        return default
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"'{key}' must be an integer")
    if value < low or (high is not None and value > high):
        bounds = f"between {low} and {high}" if high is not None else f">= {low}"
        raise ToolError(f"'{key}' must be {bounds}")
    return value


def _str_list(args: dict[str, Any], key: str) -> list[str]:
    value = args.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v.strip() for v in value):
        raise ToolError(f"'{key}' must be a non-empty array of strings")
    return [v.strip() for v in value]


def _sort_key(value: Any) -> tuple[int, Any]:
    """SQLite ordering across storage classes: numbers before text."""
    return (0, value) if isinstance(value, int | float) else (1, str(value))


def _column_stats(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    stats: list[dict[str, Any]] = []
    for i, column in enumerate(dataset["columns"]):
        present = [row[i] for row in dataset["rows"] if row[i] is not None]
        stats.append(
            {
                "name": column["name"],
                "type": column["type"],
                "min": min(present, key=_sort_key) if present else None,
                "max": max(present, key=_sort_key) if present else None,
                "null_count": len(dataset["rows"]) - len(present),
            }
        )
    return stats


def _text_result(text: str, *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=is_error)


def tool_error(message: str) -> types.CallToolResult:
    return _text_result(f"error: {message}", is_error=True)


Handler = Callable[[McpIdentity, dict[str, Any]], Awaitable[dict[str, Any]]]


class McpTools:
    def __init__(self, db: AsyncEngine, warehouse_db: str, *, sql_timeout_s: float) -> None:
        self._db = db
        self._warehouse_db = warehouse_db
        self._timeout_s = sql_timeout_s
        self._handlers: dict[str, Handler] = {
            "list_tables": self._list_tables,
            "describe_table": self._describe_table,
            "run_query": self._run_query,
            "describe_dataset": self._describe_dataset,
            "get_dataset_rows": self._get_dataset_rows,
            "query_datasets": self._query_datasets,
            "create_chart": self._create_chart,
            "save_report": self._save_report,
        }

    def list_for(self, agent: str) -> list[types.Tool]:
        return [tool for tool in TOOLS if agent in PERMISSIONS[tool.name]]

    async def call(self, identity: McpIdentity, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        handler = self._handlers.get(name)
        if handler is None:
            return tool_error(f"unknown tool '{name}'")
        if identity.agent not in PERMISSIONS[name]:
            return tool_error(f"tool '{name}' is not available to the {identity.agent} agent")
        try:
            payload = await handler(identity, arguments)
        except (ToolError, sql.SqlError, ChartError) as exc:
            return tool_error(str(exc))
        return _text_result(json.dumps(payload, ensure_ascii=False))

    # -- helpers --------------------------------------------------------------------------------

    async def _dataset(self, identity: McpIdentity, dataset_id: str) -> dict[str, Any]:
        dataset = await artifacts.get_dataset(self._db, identity.user_id, dataset_id)
        if dataset is None:
            raise ToolError("dataset not found")
        return dataset

    async def _store_dataset(
        self, identity: McpIdentity, name: str | None, source_sql: str, result: sql.QueryResult
    ) -> dict[str, Any]:
        dataset_id = await artifacts.insert_dataset(
            self._db,
            user_id=identity.user_id,
            invocation_id=identity.invocation_id,
            name=name,
            source_sql=source_sql,
            columns=result.columns,
            rows=result.rows,
            truncated=result.truncated,
        )
        return {
            "dataset_id": dataset_id,
            "name": name,
            "columns": result.columns,
            "row_count": len(result.rows),
            "truncated": result.truncated,
            "preview": result.rows[:PREVIEW_ROWS],
        }

    # -- tools ----------------------------------------------------------------------------------

    async def _list_tables(self, identity: McpIdentity, args: dict[str, Any]) -> dict[str, Any]:
        tables = await asyncio.to_thread(sql.warehouse_tables, self._warehouse_db, timeout_s=self._timeout_s)
        return {"tables": tables}

    async def _describe_table(self, identity: McpIdentity, args: dict[str, Any]) -> dict[str, Any]:
        table = _required_str(args, "table")
        return await asyncio.to_thread(sql.warehouse_describe, self._warehouse_db, table, timeout_s=self._timeout_s)

    async def _run_query(self, identity: McpIdentity, args: dict[str, Any]) -> dict[str, Any]:
        query = _required_str(args, "sql")
        name = _optional_str(args, "name")
        result = await asyncio.to_thread(sql.warehouse_query, self._warehouse_db, query, timeout_s=self._timeout_s)
        return await self._store_dataset(identity, name, query, result)

    async def _describe_dataset(self, identity: McpIdentity, args: dict[str, Any]) -> dict[str, Any]:
        dataset = await self._dataset(identity, _required_str(args, "dataset_id"))
        columns = await asyncio.to_thread(_column_stats, dataset)
        return {
            "dataset_id": dataset["id"],
            "name": dataset["name"],
            "row_count": dataset["row_count"],
            "truncated": dataset["truncated"],
            "source_sql": dataset["source_sql"],
            "columns": columns,
        }

    async def _get_dataset_rows(self, identity: McpIdentity, args: dict[str, Any]) -> dict[str, Any]:
        dataset_id = _required_str(args, "dataset_id")
        offset = _int(args, "offset", 0, 0)
        limit = _int(args, "limit", DEFAULT_PAGE_ROWS, 1, MAX_PAGE_ROWS)
        dataset = await self._dataset(identity, dataset_id)
        return {
            "dataset_id": dataset["id"],
            "columns": dataset["columns"],
            "row_count": dataset["row_count"],
            "offset": offset,
            "limit": limit,
            "rows": dataset["rows"][offset : offset + limit],
        }

    async def _query_datasets(self, identity: McpIdentity, args: dict[str, Any]) -> dict[str, Any]:
        query = _required_str(args, "sql")
        dataset_ids = list(dict.fromkeys(_str_list(args, "dataset_ids")))
        name = _optional_str(args, "name")
        sql.check_select(query)
        datasets: list[dict[str, Any]] = []
        for dataset_id in dataset_ids:
            dataset = await artifacts.get_dataset(self._db, identity.user_id, dataset_id)
            if dataset is None:
                raise ToolError(f"dataset not found: {dataset_id}")
            datasets.append(dataset)
        result = await asyncio.to_thread(sql.datasets_query, query, datasets, timeout_s=self._timeout_s)
        return await self._store_dataset(identity, name, query, result)

    async def _create_chart(self, identity: McpIdentity, args: dict[str, Any]) -> dict[str, Any]:
        dataset_id = _required_str(args, "dataset_id")
        kind = _required_str(args, "kind")
        x = _required_str(args, "x")
        y = _str_list(args, "y")
        title = _required_str(args, "title")
        dataset = await self._dataset(identity, dataset_id)
        spec = await asyncio.to_thread(build_chart_spec, dataset, kind, x, y, title)
        chart_id = await artifacts.insert_chart(
            self._db,
            user_id=identity.user_id,
            invocation_id=identity.invocation_id,
            dataset_id=dataset_id,
            title=title,
            spec=spec,
        )
        return {
            "chart_id": chart_id,
            "title": title,
            "dataset_id": dataset_id,
            "kind": kind,
            "embed": f"{{{{chart:{chart_id}}}}}",
        }

    async def _save_report(self, identity: McpIdentity, args: dict[str, Any]) -> dict[str, Any]:
        title = _required_str(args, "title")
        markdown = _required_str(args, "markdown")
        refs: dict[str, set[str]] = {"charts": set(), "datasets": set()}
        for kind, artifact_id in _EMBED.findall(markdown):
            refs[f"{kind}s"].add(artifact_id)
        missing: set[str] = set()
        for table, ids in refs.items():
            missing |= ids - await artifacts.existing_artifact_ids(self._db, identity.user_id, table, ids)
        if missing:
            raise ToolError(f"markdown references unknown ids: {', '.join(sorted(missing))}")
        report_id = await artifacts.insert_report(
            self._db, user_id=identity.user_id, invocation_id=identity.invocation_id, title=title, markdown=markdown
        )
        return {"report_id": report_id, "title": title}
