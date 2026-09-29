"""SQLite store of the Insight Agent (pipeline step 10, D-51): `var/insight_artifacts.db` by default.

- `insight_artifacts`: immutable artifacts (the insight envelope, and the task's
  `insight_candidates`, D-31) with their content hash; `idempotency_key` is UNIQUE, so a second
  write of the same task returns the first artifact (E17) and a replayed task costs nothing (TC-29);
  `superseded_by` links a drill-down's new version (spec 9.3).
- `insight_llm_usage`: one row per LLM call (`UsageSink`); `spent_today` sums `cost_usd` of the
  local day (Asia/Ho_Chi_Minh, UTC+7) for the daily budget warning.

sqlite3 calls run in a worker thread (sdk R10); one short connection per call.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .contracts import ArtifactEnvelope, LlmUsage

LOCAL_TZ = timezone(timedelta(hours=7))
SCHEMA = """
CREATE TABLE IF NOT EXISTS insight_artifacts (
  artifact_id     TEXT PRIMARY KEY,
  idempotency_key TEXT UNIQUE,
  artifact_type   TEXT NOT NULL,
  status          TEXT,
  content_hash    TEXT NOT NULL,
  body_json       TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  superseded_by   TEXT
);
CREATE TABLE IF NOT EXISTS insight_llm_usage (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id       TEXT NOT NULL,
  provider      TEXT NOT NULL,
  model_id      TEXT NOT NULL,
  call_type     TEXT NOT NULL,
  input_tokens  INTEGER NOT NULL,
  cached_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  thinking_tokens INTEGER NOT NULL,
  cost_usd      TEXT,
  latency_ms    INTEGER NOT NULL,
  finish_reason TEXT NOT NULL,
  local_day     TEXT NOT NULL,
  created_at    TEXT NOT NULL
);
"""

INSERT_INSIGHT = """
INSERT OR IGNORE INTO insight_artifacts
  (artifact_id, idempotency_key, artifact_type, status, content_hash, body_json, created_at)
VALUES (?, ?, 'insight', ?, ?, ?, ?)
"""
INSERT_JSON = """
INSERT OR IGNORE INTO insight_artifacts (artifact_id, artifact_type, content_hash, body_json, created_at)
VALUES (?, ?, ?, ?, ?)
"""
INSERT_USAGE = """
INSERT INTO insight_llm_usage (task_id, provider, model_id, call_type, input_tokens, cached_tokens, output_tokens,
  thinking_tokens, cost_usd, latency_ms, finish_reason, local_day, created_at)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


class InsightStore:
    def __init__(self, path: Path, clock: Callable[[], datetime] | None = None) -> None:
        self._path = path
        self._clock = clock or (lambda: datetime.now(LOCAL_TZ))
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        if not self._ready:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        if not self._ready:
            conn.executescript(SCHEMA)
            self._ready = True
        return conn

    def _run[T](self, fn: Callable[[sqlite3.Connection], T]) -> asyncio.Future[T]:
        def work() -> T:
            conn = self._connect()
            try:
                with conn:
                    return fn(conn)
            finally:
                conn.close()

        return asyncio.ensure_future(asyncio.to_thread(work))

    def _now(self) -> datetime:
        return self._clock().astimezone(LOCAL_TZ)

    async def find(self, idempotency_key: str) -> ArtifactEnvelope | None:
        row = await self._run(
            lambda c: c.execute(
                "SELECT body_json FROM insight_artifacts WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        )
        return ArtifactEnvelope.model_validate_json(row[0]) if row else None

    async def get(self, artifact_id: str) -> ArtifactEnvelope | None:
        row = await self._run(
            lambda c: c.execute(
                "SELECT body_json FROM insight_artifacts WHERE artifact_id = ? AND artifact_type = 'insight'", (artifact_id,)
            ).fetchone()
        )
        return ArtifactEnvelope.model_validate_json(row[0]) if row else None

    async def save(self, envelope: ArtifactEnvelope, idempotency_key: str) -> ArtifactEnvelope:
        """Insert once; with an existing key, return the stored artifact instead (E17)."""
        now = self._now().isoformat()

        def insert(c: sqlite3.Connection) -> str:
            c.execute(
                INSERT_INSIGHT,
                (envelope.artifact_id, idempotency_key, envelope.status, envelope.content_hash, envelope.model_dump_json(), now),
            )
            return c.execute("SELECT body_json FROM insight_artifacts WHERE idempotency_key = ?", (idempotency_key,)).fetchone()[
                0
            ]

        return ArtifactEnvelope.model_validate_json(await self._run(insert))

    async def save_json(self, artifact_id: str, artifact_type: str, body: dict[str, Any], content_hash: str) -> None:
        now = self._now().isoformat()
        text = json.dumps(body, ensure_ascii=False)
        await self._run(
            lambda c: c.execute(
                INSERT_JSON,
                (artifact_id, artifact_type, content_hash, text, now),
            )
        )

    async def get_json(self, artifact_id: str) -> dict[str, Any] | None:
        row = await self._run(
            lambda c: c.execute("SELECT body_json FROM insight_artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        )
        return json.loads(row[0]) if row else None

    async def supersede(self, old_id: str, new_id: str) -> None:
        await self._run(
            lambda c: c.execute("UPDATE insight_artifacts SET superseded_by = ? WHERE artifact_id = ?", (new_id, old_id))
        )

    async def superseded_by(self, artifact_id: str) -> str | None:
        row = await self._run(
            lambda c: c.execute("SELECT superseded_by FROM insight_artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        )
        return row[0] if row else None

    # ---- UsageSink ---------------------------------------------------------------------------------

    async def record(self, task_id: str, usages: list[LlmUsage]) -> None:
        now = self._now()
        day, stamp = now.date().isoformat(), now.isoformat()
        rows = [
            (
                task_id,
                u.provider,
                u.model_id,
                u.call_type,
                u.input_tokens,
                u.cached_input_tokens,
                u.output_tokens,
                u.thinking_tokens,
                None if u.cost_usd is None else str(u.cost_usd),
                u.latency_ms,
                u.finish_reason,
                day,
                stamp,
            )
            for u in usages
        ]
        await self._run(
            lambda c: c.executemany(
                INSERT_USAGE,
                rows,
            )
        )

    async def spent_today(self) -> Decimal:
        day = self._now().date().isoformat()
        rows = await self._run(
            lambda c: c.execute(
                "SELECT cost_usd FROM insight_llm_usage WHERE local_day = ? AND cost_usd IS NOT NULL", (day,)
            ).fetchall()
        )
        return sum((Decimal(r[0]) for r in rows), Decimal(0))

    async def usage_count(self, task_id: str) -> int:
        row = await self._run(
            lambda c: c.execute("SELECT COUNT(*) FROM insight_llm_usage WHERE task_id = ?", (task_id,)).fetchone()
        )
        return int(row[0])
