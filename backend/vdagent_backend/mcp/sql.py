"""Read-only SQL for the MCP tools (§6.2).

Blocking sqlite3 code: callers run these functions in a worker thread. Every function opens its
own connection, so nothing here is shared between threads.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SQL_TIMEOUT_S = 10.0
MAX_ROWS = 10_000
_PROGRESS_EVERY_N_OPS = 1_000
_LEADING_WORD = re.compile(r"[A-Za-z_]+")


class SqlError(Exception):
    """A user-facing SQL failure; its message becomes the tool error text."""


@dataclass(frozen=True)
class QueryResult:
    columns: list[dict[str, str]]  # [{"name", "type"}], type ∈ INTEGER | REAL | TEXT
    rows: list[list[Any]]
    truncated: bool


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _skip_trivia(sql: str, i: int, *, semicolons: bool) -> int:
    """Index of the first character at/after `i` that is not whitespace, a comment (or `;`)."""
    n = len(sql)
    while i < n:
        if sql[i].isspace() or (semicolons and sql[i] == ";"):
            i += 1
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end < 0 else end + 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            break
    return i


def check_select(sql: str) -> str:
    """Validate that `sql` is exactly one `SELECT` / `WITH … SELECT` statement; return that statement.

    Statement boundaries come from SQLite's own tokenizer (`sqlite3.complete_statement`), so `;`
    inside strings, identifiers and comments is handled correctly.
    """
    start = _skip_trivia(sql, 0, semicolons=True)
    if start == len(sql):
        raise SqlError("empty SQL statement")
    word = _LEADING_WORD.match(sql, start)
    if word is None or word.group(0).upper() not in ("SELECT", "WITH"):
        raise SqlError("only a single read-only SELECT (or WITH … SELECT) statement is allowed")

    for i in range(start, len(sql)):
        if sql[i] == ";" and sqlite3.complete_statement(sql[: i + 1]):
            if _skip_trivia(sql, i + 1, semicolons=True) != len(sql):
                raise SqlError("exactly one SQL statement is allowed")
            return sql[start : i + 1]
    if not sqlite3.complete_statement(sql + "\n;"):
        raise SqlError("incomplete SQL statement")
    return sql[start:]


@contextmanager
def _deadline(conn: sqlite3.Connection, timeout_s: float) -> Iterator[None]:
    """Abort any statement on `conn` that runs past `timeout_s` (raises `SqlError`)."""
    deadline = time.monotonic() + timeout_s
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), _PROGRESS_EVERY_N_OPS)
    try:
        yield
    except sqlite3.OperationalError as exc:
        if str(exc) == "interrupted" and time.monotonic() > deadline:
            raise SqlError(f"query exceeded the {timeout_s:g} s time limit") from None
        raise SqlError(str(exc)) from None
    except sqlite3.Error as exc:
        raise SqlError(str(exc)) from None
    finally:
        conn.set_progress_handler(None, 0)


def _json_value(value: Any) -> Any:
    return value.hex() if isinstance(value, bytes | bytearray | memoryview) else value


def _column_type(rows: list[list[Any]], index: int) -> str:
    for row in rows:
        value = row[index]
        if value is None:
            continue
        if isinstance(value, int):
            return "INTEGER"
        if isinstance(value, float):
            return "REAL"
        return "TEXT"
    return "TEXT"


def _unique_names(names: Sequence[str]) -> list[str]:
    """Make column names unique (`a, a` → `a, a_2`) so datasets load as tables and chart fields."""
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        candidate, n = name, 1
        while candidate.lower() in seen:
            n += 1
            candidate = f"{name}_{n}"
        seen.add(candidate.lower())
        out.append(candidate)
    return out


def execute_select(conn: sqlite3.Connection, sql: str, *, timeout_s: float) -> QueryResult:
    statement = check_select(sql)
    with _deadline(conn, timeout_s):
        cur = conn.execute(statement)
        fetched = cur.fetchmany(MAX_ROWS + 1)
        names = [d[0] for d in cur.description]
        cur.close()
    rows = [[_json_value(v) for v in row] for row in fetched[:MAX_ROWS]]
    columns = [
        {"name": name, "type": _column_type(rows, i)} for i, name in enumerate(_unique_names(names))
    ]
    return QueryResult(columns=columns, rows=rows, truncated=len(fetched) > MAX_ROWS)


def connect_warehouse(path: str) -> sqlite3.Connection:
    """Open the warehouse read-only: `file:…?mode=ro` URI plus `PRAGMA query_only=ON`."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise SqlError(f"warehouse unavailable: {exc}") from None
    conn.execute("PRAGMA query_only=ON")
    return conn


def warehouse_query(path: str, sql: str, *, timeout_s: float) -> QueryResult:
    with closing(connect_warehouse(path)) as conn:
        return execute_select(conn, sql, timeout_s=timeout_s)


def warehouse_tables(path: str, *, timeout_s: float) -> list[dict[str, Any]]:
    with closing(connect_warehouse(path)) as conn, _deadline(conn, timeout_s):
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return [
            {"name": name, "row_count": conn.execute(f"SELECT COUNT(*) FROM {quote_ident(name)}").fetchone()[0]}
            for name in names
        ]


def warehouse_describe(path: str, table: str, *, timeout_s: float, sample_rows: int = 5) -> dict[str, Any]:
    with closing(connect_warehouse(path)) as conn, _deadline(conn, timeout_s):
        found = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            " AND name = ? COLLATE NOCASE",
            (table,),
        ).fetchone()
        if found is None:
            raise SqlError(f"table not found: {table}")
        name = found[0]
        columns = [
            {"name": r[1], "type": r[2]} for r in conn.execute(f"PRAGMA table_info({quote_ident(name)})")
        ]
        sample = conn.execute(f"SELECT * FROM {quote_ident(name)} LIMIT ?", (sample_rows,)).fetchall()
    return {
        "table": name,
        "columns": columns,
        "sample_rows": [[_json_value(v) for v in row] for row in sample],
    }


def datasets_query(sql: str, datasets: Sequence[dict[str, Any]], *, timeout_s: float) -> QueryResult:
    """Run `sql` over a fresh in-memory SQLite holding each dataset as a table named by its id."""
    check_select(sql)
    with closing(sqlite3.connect(":memory:")) as conn:
        for ds in datasets:
            table = quote_ident(ds["id"])
            cols = ", ".join(f"{quote_ident(c['name'])} {c['type']}" for c in ds["columns"])
            conn.execute(f"CREATE TABLE {table} ({cols})")
            placeholders = ", ".join("?" for _ in ds["columns"])
            conn.executemany(f"INSERT INTO {table} VALUES ({placeholders})", ds["rows"])
        conn.commit()
        conn.execute("PRAGMA query_only=ON")
        return execute_select(conn, sql, timeout_s=timeout_s)
