"""backend.db access: async SQLAlchemy Core engine over aiosqlite (§8).

`create_db(path)` applies `schema.sql` (idempotent) and returns an `AsyncEngine` whose connections
carry the required pragmas. Query functions live in `repo.py` (engine/core tables) and
`artifacts.py` (datasets / charts / reports).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def apply_schema(path: str) -> None:
    """Create tables if missing (sync; startup / seed scripts only)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA_PATH.read_text())


def _set_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def create_db(path: str) -> AsyncEngine:
    apply_schema(path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    event.listen(engine.sync_engine, "connect", _set_pragmas)
    return engine
