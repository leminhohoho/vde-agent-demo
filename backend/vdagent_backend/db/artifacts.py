"""Datasets, charts and reports in backend.db (§3, §8).

Every read is scoped by the owning user (I4): another user's id reads exactly like an unknown id
(`None`). Writes take the owner and creating invocation from the MCP caller identity.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from vdagent_backend.ids import new_id


async def insert_dataset(
    db: AsyncEngine,
    *,
    user_id: str,
    invocation_id: str,
    name: str | None,
    source_sql: str,
    columns: list[dict[str, str]],
    rows: list[list[Any]],
    truncated: bool,
) -> str:
    dataset_id = new_id("ds")
    async with db.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO datasets (id, user_id, invocation_id, name, source_sql, columns_json, rows_json,"
                " row_count, truncated) VALUES (:id, :user_id, :invocation_id, :name, :source_sql,"
                " :columns_json, :rows_json, :row_count, :truncated)"
            ),
            {
                "id": dataset_id,
                "user_id": user_id,
                "invocation_id": invocation_id,
                "name": name,
                "source_sql": source_sql,
                "columns_json": json.dumps(columns),
                "rows_json": json.dumps(rows),
                "row_count": len(rows),
                "truncated": int(truncated),
            },
        )
    return dataset_id


async def get_dataset(db: AsyncEngine, user_id: str, dataset_id: str) -> dict[str, Any] | None:
    async with db.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT id, name, columns_json, rows_json, row_count, truncated, source_sql, created_at"
                    " FROM datasets WHERE id = :id AND user_id = :user_id"
                ),
                {"id": dataset_id, "user_id": user_id},
            )
        ).first()
    if row is None:
        return None
    return {
        "id": row.id,
        "name": row.name,
        "columns": json.loads(row.columns_json),
        "row_count": row.row_count,
        "truncated": bool(row.truncated),
        "source_sql": row.source_sql,
        "rows": json.loads(row.rows_json),
        "created_at": row.created_at,
    }


async def insert_chart(
    db: AsyncEngine,
    *,
    user_id: str,
    invocation_id: str,
    dataset_id: str,
    title: str,
    spec: dict[str, Any],
) -> str:
    chart_id = new_id("ch")
    async with db.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO charts (id, user_id, invocation_id, dataset_id, title, spec_json)"
                " VALUES (:id, :user_id, :invocation_id, :dataset_id, :title, :spec_json)"
            ),
            {
                "id": chart_id,
                "user_id": user_id,
                "invocation_id": invocation_id,
                "dataset_id": dataset_id,
                "title": title,
                "spec_json": json.dumps(spec),
            },
        )
    return chart_id


async def get_chart(db: AsyncEngine, user_id: str, chart_id: str) -> dict[str, Any] | None:
    async with db.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT id, title, dataset_id, spec_json FROM charts WHERE id = :id AND user_id = :user_id"),
                {"id": chart_id, "user_id": user_id},
            )
        ).first()
    if row is None:
        return None
    return {"id": row.id, "title": row.title, "dataset_id": row.dataset_id, "spec": json.loads(row.spec_json)}


async def insert_report(db: AsyncEngine, *, user_id: str, invocation_id: str, title: str, markdown: str) -> str:
    report_id = new_id("rp")
    async with db.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO reports (id, user_id, invocation_id, title, markdown)"
                " VALUES (:id, :user_id, :invocation_id, :title, :markdown)"
            ),
            {
                "id": report_id,
                "user_id": user_id,
                "invocation_id": invocation_id,
                "title": title,
                "markdown": markdown,
            },
        )
    return report_id


async def list_reports(db: AsyncEngine, user_id: str) -> list[dict[str, Any]]:
    async with db.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, title, created_at FROM reports WHERE user_id = :user_id"
                    " ORDER BY created_at DESC, rowid DESC"
                ),
                {"user_id": user_id},
            )
        ).all()
    return [{"id": r.id, "title": r.title, "created_at": r.created_at} for r in rows]


async def get_report(db: AsyncEngine, user_id: str, report_id: str) -> dict[str, Any] | None:
    async with db.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT id, title, markdown, created_at FROM reports WHERE id = :id AND user_id = :user_id"),
                {"id": report_id, "user_id": user_id},
            )
        ).first()
    if row is None:
        return None
    return {"id": row.id, "title": row.title, "markdown": row.markdown, "created_at": row.created_at}


async def existing_artifact_ids(db: AsyncEngine, user_id: str, table: str, ids: set[str]) -> set[str]:
    """The subset of `ids` in `table` (`datasets` | `charts`) owned by `user_id`."""
    if table not in ("datasets", "charts"):
        raise ValueError(f"not an artifact table: {table}")
    if not ids:
        return set()
    params = {f"id{i}": v for i, v in enumerate(sorted(ids))}
    placeholders = ", ".join(f":{k}" for k in params)
    async with db.connect() as conn:
        rows = (
            await conn.execute(
                text(f"SELECT id FROM {table} WHERE user_id = :user_id AND id IN ({placeholders})"),
                {"user_id": user_id, **params},
            )
        ).all()
    return {r.id for r in rows}
