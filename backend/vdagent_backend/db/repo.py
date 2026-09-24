"""Queries over the engine/core tables (§8) and the DTO builders of §10.

All functions take the `AsyncEngine` from `database.create_db`. Rows are returned as plain dicts
with the table's column names; `*_dto` turn them into the exact REST/SSE shapes.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

from vdagent_backend.ids import new_id, now_iso

Row = dict[str, Any]

TERMINAL_INVOCATION = ("completed", "failed", "cancelled", "rejected")


async def _all(db: AsyncEngine, sql: str, **params: Any) -> list[Row]:
    async with db.connect() as conn:
        res = await conn.execute(text(sql), params)
        return [dict(r) for r in res.mappings().all()]


async def _one(db: AsyncEngine, sql: str, **params: Any) -> Row | None:
    rows = await _all(db, sql, **params)
    return rows[0] if rows else None


# --------------------------------------------------------------------------- DTOs


def user_dto(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"]}


def task_dto(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "root_agent": row["root_agent"],
        "status": row["status"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
    }


_INVOCATION_FIELDS = (
    "id", "task_id", "agent", "caller", "parent_id", "tool_call_id", "depth", "inbound_text",
    "status", "result_text", "error", "created_at", "started_at", "finished_at",
)  # fmt: skip


def invocation_dto(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in _INVOCATION_FIELDS}


def message_dto(row: Mapping[str, Any]) -> dict[str, Any]:
    raw_calls = row["tool_calls_json"]
    return {
        "id": row["id"],
        "seq": row["seq"],
        "task_id": row["task_id"],
        "invocation_id": row["invocation_id"],
        "role": row["role"],
        "sender": row["sender"],
        "content": row["content"],
        "tool_calls": json.loads(raw_calls) if raw_calls else None,
        "tool_call_id": row["tool_call_id"],
        "compacted": bool(row["compacted"]),
        "created_at": row["created_at"],
    }


# --------------------------------------------------------------------------- users


async def list_users(db: AsyncEngine) -> list[Row]:
    return await _all(db, "SELECT id, name, created_at FROM users ORDER BY created_at, id")


async def get_user(db: AsyncEngine, user_id: str) -> Row | None:
    return await _one(db, "SELECT id, name, created_at FROM users WHERE id = :id", id=user_id)


async def create_user(db: AsyncEngine, name: str) -> Row:
    async with db.begin() as conn:
        res = await conn.execute(
            text("INSERT INTO users (id, name, created_at) VALUES (:id, :name, :now) RETURNING *"),
            {"id": new_id("u"), "name": name, "now": now_iso()},
        )
        return dict(res.mappings().one())


# --------------------------------------------------------------------------- tasks


async def create_task(db: AsyncEngine, user_id: str, root_agent: str, inbound_text: str) -> tuple[Row, Row]:
    """Human trigger (§4.1 step 1): a running task and its queued root invocation, atomically."""
    now = now_iso()
    task_id, inv_id = new_id("t"), new_id("inv")
    async with db.begin() as conn:
        task = await conn.execute(
            text(
                "INSERT INTO tasks (id, user_id, root_agent, status, created_at) "
                "VALUES (:id, :user_id, :agent, 'running', :now) RETURNING *"
            ),
            {"id": task_id, "user_id": user_id, "agent": root_agent, "now": now},
        )
        task_row = dict(task.mappings().one())
        inv = await conn.execute(
            text(
                "INSERT INTO invocations (id, task_id, user_id, agent, caller, depth, inbound_text, status, created_at) "
                "VALUES (:id, :task_id, :user_id, :agent, 'user', 0, :text, 'queued', :now) RETURNING *"
            ),
            {"id": inv_id, "task_id": task_id, "user_id": user_id, "agent": root_agent, "text": inbound_text, "now": now},
        )
        return task_row, dict(inv.mappings().one())


async def get_task(db: AsyncEngine, task_id: str, user_id: str | None = None) -> Row | None:
    """A task by id; with `user_id`, only if owned by that user."""
    sql = "SELECT * FROM tasks WHERE id = :id"
    if user_id is not None:
        sql += " AND user_id = :user_id"
    return await _one(db, sql, id=task_id, user_id=user_id)


async def list_tasks(db: AsyncEngine, user_id: str, status: str | None = None, limit: int = 50) -> list[Row]:
    sql = "SELECT * FROM tasks WHERE user_id = :user_id"
    if status is not None:
        sql += " AND status = :status"
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT :limit"
    return await _all(db, sql, user_id=user_id, status=status, limit=limit)


async def finish_task(db: AsyncEngine, task_id: str, status: str) -> Row | None:
    """Move a running task to a terminal status; returns the updated row, or None if it was not running."""
    async with db.begin() as conn:
        res = await conn.execute(
            text(
                "UPDATE tasks SET status = :status, finished_at = :now "
                "WHERE id = :id AND status = 'running' RETURNING *"
            ),
            {"id": task_id, "status": status, "now": now_iso()},
        )
        row = res.mappings().one_or_none()
        return dict(row) if row else None


async def fail_running_tasks(db: AsyncEngine) -> list[Row]:
    """Startup recovery: every running task → failed."""
    async with db.begin() as conn:
        res = await conn.execute(
            text("UPDATE tasks SET status = 'failed', finished_at = :now WHERE status = 'running' RETURNING *"),
            {"now": now_iso()},
        )
        return [dict(r) for r in res.mappings().all()]


# --------------------------------------------------------------------------- invocations


async def insert_invocation(
    db: AsyncEngine,
    *,
    id: str,
    task_id: str,
    user_id: str,
    agent: str,
    caller: str,
    parent_id: str | None,
    tool_call_id: str | None,
    depth: int,
    inbound_text: str,
    status: str,
    error: str | None = None,
) -> Row:
    now = now_iso()
    async with db.begin() as conn:
        res = await conn.execute(
            text(
                "INSERT INTO invocations (id, task_id, user_id, agent, caller, parent_id, tool_call_id, depth, "
                "inbound_text, status, error, created_at, finished_at) "
                "VALUES (:id, :task_id, :user_id, :agent, :caller, :parent_id, :tool_call_id, :depth, "
                ":inbound_text, :status, :error, :now, :finished_at) RETURNING *"
            ),
            {
                "id": id,
                "task_id": task_id,
                "user_id": user_id,
                "agent": agent,
                "caller": caller,
                "parent_id": parent_id,
                "tool_call_id": tool_call_id,
                "depth": depth,
                "inbound_text": inbound_text,
                "status": status,
                "error": error,
                "now": now,
                "finished_at": now if status in TERMINAL_INVOCATION else None,
            },
        )
        return dict(res.mappings().one())


async def get_invocation(db: AsyncEngine, invocation_id: str) -> Row | None:
    return await _one(db, "SELECT * FROM invocations WHERE id = :id", id=invocation_id)


async def mark_invocation_running(db: AsyncEngine, invocation_id: str) -> Row:
    async with db.begin() as conn:
        res = await conn.execute(
            text("UPDATE invocations SET status = 'running', started_at = :now WHERE id = :id RETURNING *"),
            {"id": invocation_id, "now": now_iso()},
        )
        return dict(res.mappings().one())


async def finish_invocation(
    db: AsyncEngine,
    invocation_id: str,
    status: str,
    *,
    result_text: str | None = None,
    error: str | None = None,
) -> Row | None:
    """Move a non-terminal invocation to a terminal status; None if it was already terminal."""
    async with db.begin() as conn:
        res = await conn.execute(
            text(
                "UPDATE invocations SET status = :status, result_text = :result_text, error = :error, "
                "finished_at = :now WHERE id = :id AND status IN ('queued', 'running') RETURNING *"
            ),
            {"id": invocation_id, "status": status, "result_text": result_text, "error": error, "now": now_iso()},
        )
        row = res.mappings().one_or_none()
        return dict(row) if row else None


async def cancel_task_invocations(db: AsyncEngine, task_id: str) -> list[Row]:
    """Every non-terminal invocation of the task → `cancelled`; returns the changed rows."""
    async with db.begin() as conn:
        res = await conn.execute(
            text(
                "UPDATE invocations SET status = 'cancelled', finished_at = :now "
                "WHERE task_id = :task_id AND status IN ('queued', 'running') RETURNING *"
            ),
            {"task_id": task_id, "now": now_iso()},
        )
        return [dict(r) for r in res.mappings().all()]


async def list_task_invocations(db: AsyncEngine, task_id: str) -> list[Row]:
    return await _all(db, "SELECT * FROM invocations WHERE task_id = :task_id ORDER BY created_at, rowid", task_id=task_id)


async def inflight_invocations(db: AsyncEngine) -> list[Row]:
    return await _all(
        db, "SELECT * FROM invocations WHERE status IN ('queued', 'running') ORDER BY created_at, rowid"
    )


# --------------------------------------------------------------------------- messages


async def append_message(
    db: AsyncEngine,
    *,
    user_id: str,
    agent: str,
    task_id: str,
    invocation_id: str,
    role: str,
    content: str,
    sender: str | None = None,
    tool_calls: Sequence[Mapping[str, str]] | None = None,
    tool_call_id: str | None = None,
) -> Row:
    """Append to stack (user_id, agent) with `seq = max(seq)+1` (safe under the stack lock, I1)."""
    async with db.begin() as conn:
        res = await conn.execute(
            text(
                "INSERT INTO messages (user_id, agent, seq, task_id, invocation_id, role, sender, content, "
                "tool_calls_json, tool_call_id, created_at) "
                "SELECT :user_id, :agent, COALESCE(MAX(seq), 0) + 1, :task_id, :invocation_id, :role, :sender, "
                ":content, :tool_calls_json, :tool_call_id, :now FROM messages "
                "WHERE user_id = :user_id AND agent = :agent RETURNING *"
            ),
            {
                "user_id": user_id,
                "agent": agent,
                "task_id": task_id,
                "invocation_id": invocation_id,
                "role": role,
                "sender": sender,
                "content": content,
                "tool_calls_json": json.dumps([dict(tc) for tc in tool_calls]) if tool_calls else None,
                "tool_call_id": tool_call_id,
                "now": now_iso(),
            },
        )
        return dict(res.mappings().one())


async def stack_history(db: AsyncEngine, user_id: str, agent: str) -> list[Row]:
    """Uncompacted stack messages in `seq` order (InvokeStart history)."""
    return await _all(
        db,
        "SELECT * FROM messages WHERE user_id = :user_id AND agent = :agent AND compacted = 0 ORDER BY seq",
        user_id=user_id,
        agent=agent,
    )


async def compaction_candidates(db: AsyncEngine, user_id: str, agent: str, current_task_id: str) -> list[Row]:
    """Uncompacted messages of finished tasks other than the current one (§4.5)."""
    return await _all(
        db,
        "SELECT m.* FROM messages m JOIN tasks t ON t.id = m.task_id "
        "WHERE m.user_id = :user_id AND m.agent = :agent AND m.compacted = 0 "
        "AND m.task_id <> :current AND t.status <> 'running' ORDER BY m.seq",
        user_id=user_id,
        agent=agent,
        current=current_task_id,
    )


async def apply_compaction(
    db: AsyncEngine, user_id: str, agent: str, summary: str, message_ids: Iterable[int]
) -> None:
    """One transaction: upsert the stack summary and flag the summarised messages compacted."""
    ids = list(message_ids)
    async with db.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO stack_summaries (user_id, agent, summary, updated_at) VALUES (:user_id, :agent, :summary, :now) "
                "ON CONFLICT (user_id, agent) DO UPDATE SET summary = excluded.summary, updated_at = excluded.updated_at"
            ),
            {"user_id": user_id, "agent": agent, "summary": summary, "now": now_iso()},
        )
        await conn.execute(
            text("UPDATE messages SET compacted = 1 WHERE id IN :ids").bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        )


async def get_summary(db: AsyncEngine, user_id: str, agent: str) -> str | None:
    row = await _one(
        db, "SELECT summary FROM stack_summaries WHERE user_id = :user_id AND agent = :agent", user_id=user_id, agent=agent
    )
    return row["summary"] if row else None


async def messages_page(
    db: AsyncEngine, user_id: str, agent: str, before_seq: int | None, limit: int
) -> list[Row]:
    """Newest `limit` messages with `seq < before_seq` (all when None), in ascending `seq`."""
    cond = " AND seq < :before" if before_seq is not None else ""
    return await _all(
        db,
        "SELECT * FROM (SELECT * FROM messages WHERE user_id = :user_id AND agent = :agent"
        f"{cond} ORDER BY seq DESC LIMIT :limit) ORDER BY seq",
        user_id=user_id,
        agent=agent,
        before=before_seq,
        limit=limit,
    )


async def invocation_messages(db: AsyncEngine, invocation_id: str) -> list[Row]:
    return await _all(db, "SELECT * FROM messages WHERE invocation_id = :id ORDER BY seq", id=invocation_id)
