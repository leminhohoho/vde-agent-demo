"""MCP server end-to-end (§6, §13 "MCP"): real uvicorn + the mcp SDK streamable-HTTP client."""

from __future__ import annotations

import asyncio
import json
import socket
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from sqlalchemy.ext.asyncio import AsyncEngine

from vdagent_backend.config import Config
from vdagent_backend.db import artifacts
from vdagent_backend.db.database import create_db
from vdagent_backend.mcp.server import create_mcp
from vdagent_backend.tokens import TokenRegistry

ALICE, BOB = "u_000000000001", "u_000000000002"
INVOCATION = {ALICE: "inv_00000000000a", BOB: "inv_00000000000b"}
SQL_TIMEOUT_S = 1.0
BIG_ROWS = 10_050
SALES = [
    ("North", 2024, 100.0),
    ("North", 2024, 50.0),
    ("South", 2024, 80.0),
    ("East", 2024, 40.0),
    ("North", 2025, 210.0),
    ("South", 2025, 60.0),
    ("East", 2025, 45.5),
]
# §6.1 permission matrix, stated independently of the implementation.
EXPECTED_TOOLS = {
    "orchestrator": {"describe_dataset", "get_dataset_rows"},
    "data": {"list_tables", "describe_table", "run_query", "describe_dataset", "get_dataset_rows", "query_datasets"},
    "compare": {"describe_dataset", "get_dataset_rows", "query_datasets"},
    "insight": {"describe_dataset", "get_dataset_rows", "query_datasets"},
    "report": {"describe_dataset", "get_dataset_rows", "create_chart", "save_report"},
}


def build_warehouse(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sales (region TEXT, year INTEGER, revenue REAL)")
        conn.executemany("INSERT INTO sales VALUES (?, ?, ?)", SALES)
        conn.execute("CREATE TABLE big (n INTEGER)")
        conn.executemany("INSERT INTO big VALUES (?)", ((i,) for i in range(BIG_ROWS)))
    conn.close()


def seed_backend(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        for user_id, name in ((ALICE, "Alice"), (BOB, "Bob")):
            task_id = f"t_{user_id[-12:]}"
            conn.execute("INSERT INTO users (id, name) VALUES (?, ?)", (user_id, name))
            conn.execute(
                "INSERT INTO tasks (id, user_id, root_agent, status) VALUES (?, ?, 'orchestrator', 'running')",
                (task_id, user_id),
            )
            conn.execute(
                "INSERT INTO invocations (id, task_id, user_id, agent, caller, depth, inbound_text, status)"
                " VALUES (?, ?, ?, 'orchestrator', 'user', 0, 'hi', 'running')",
                (INVOCATION[user_id], task_id, user_id),
            )
    conn.close()


@dataclass
class McpEnv:
    url: str
    db: AsyncEngine
    tokens: TokenRegistry
    warehouse: Path

    def token(self, agent: str, user_id: str = ALICE) -> str:
        return self.tokens.issue(user_id, agent, INVOCATION[user_id])


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[McpEnv]:
    warehouse, backend = tmp_path / "warehouse.db", tmp_path / "backend.db"
    build_warehouse(warehouse)
    db = create_db(str(backend))
    seed_backend(backend)
    cfg = Config(
        backend_db=str(backend),
        warehouse_db=str(warehouse),
        mcp_public_url="",
        frontend_dist=str(tmp_path / "dist"),
    )
    tokens = TokenRegistry()
    mcp = create_mcp(cfg, db, tokens, sql_timeout_s=SQL_TIMEOUT_S)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with mcp.lifespan():
            yield

    app = FastAPI(lifespan=lifespan)
    mcp.install(app)

    @app.get("/{path:path}")  # SPA-style catch-all must not shadow /mcp
    async def catch_all(path: str) -> dict[str, str]:
        return {"spa": path}

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=1))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    deadline = time.monotonic() + 10
    while not server.started:
        assert not serving.done() and time.monotonic() < deadline, "uvicorn failed to start"
        await asyncio.sleep(0.01)
    try:
        yield McpEnv(url=f"http://127.0.0.1:{port}/mcp", db=db, tokens=tokens, warehouse=warehouse)
    finally:
        server.should_exit = True
        await serving
        sock.close()
        await db.dispose()


@asynccontextmanager
async def connect(url: str, token: str, mode: str = "auto") -> AsyncIterator[Client]:
    """Same client construction as the agents' MCP client (§7.2)."""
    async with create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}) as http_client:
        async with Client(streamable_http_client(url, http_client=http_client), cache=None, mode=mode) as client:
            yield client


async def call(env: McpEnv, agent: str, tool: str, args: dict[str, Any], user_id: str = ALICE) -> tuple[bool, str]:
    async with connect(env.url, env.token(agent, user_id)) as client:
        result = await client.call_tool(tool, args)
    return bool(result.is_error), result.content[0].text


async def call_ok(env: McpEnv, agent: str, tool: str, args: dict[str, Any], user_id: str = ALICE) -> dict[str, Any]:
    is_error, text = await call(env, agent, tool, args, user_id)
    assert not is_error, text
    return json.loads(text)


def warehouse_count(env: McpEnv, table: str) -> int:
    with sqlite3.connect(env.warehouse) as conn:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return count


async def dataset_count(env: McpEnv) -> int:
    async with env.db.connect() as conn:
        return (await conn.exec_driver_sql("SELECT COUNT(*) FROM datasets")).scalar_one()


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_tools_list_is_filtered_per_agent(env: McpEnv, mode: str) -> None:
    for agent, expected in EXPECTED_TOOLS.items():
        async with connect(env.url, env.token(agent), mode=mode) as client:
            listed = await client.list_tools()
        assert {t.name for t in listed.tools} == expected, agent


@pytest.mark.parametrize(
    ("agent", "tool", "args"),
    [
        ("orchestrator", "run_query", {"sql": "SELECT * FROM sales"}),
        ("report", "query_datasets", {"sql": "SELECT 1", "dataset_ids": ["ds_000000000000"]}),
        ("compare", "list_tables", {}),
        ("data", "save_report", {"title": "t", "markdown": "m"}),
    ],
)
async def test_tools_call_rejects_non_permitted_tool(env: McpEnv, agent: str, tool: str, args: dict[str, Any]) -> None:
    is_error, text = await call(env, agent, tool, args)
    assert is_error
    assert text.startswith("error:") and tool in text
    assert await dataset_count(env) == 0


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO sales VALUES ('West', 2025, 1.0)",
        "PRAGMA writable_schema = ON",
        "SELECT 1; DELETE FROM sales",
        "SELECT ';' AS s; DROP TABLE sales",
        "WITH doomed AS (SELECT 1) DELETE FROM sales",
    ],
)
async def test_run_query_rejects_anything_but_one_select(env: McpEnv, sql: str) -> None:
    is_error, text = await call(env, "data", "run_query", {"sql": sql})
    assert is_error and text.startswith("error:")
    assert warehouse_count(env, "sales") == len(SALES)
    assert await dataset_count(env) == 0


async def test_run_query_caps_rows_at_10000_and_flags_truncation(env: McpEnv) -> None:
    capped = await call_ok(env, "data", "run_query", {"sql": "SELECT n FROM big ORDER BY n", "name": "big"})
    assert capped["row_count"] == 10_000 and capped["truncated"] is True
    assert capped["columns"] == [{"name": "n", "type": "INTEGER"}]
    assert capped["preview"] == [[i] for i in range(20)]
    stored = await artifacts.get_dataset(env.db, ALICE, capped["dataset_id"])
    assert stored is not None and len(stored["rows"]) == 10_000 and stored["truncated"] is True
    assert stored["rows"][-1] == [9_999]

    exact = await call_ok(env, "data", "run_query", {"sql": "SELECT n FROM big LIMIT 10000"})
    assert exact["row_count"] == 10_000 and exact["truncated"] is False


async def test_run_query_times_out(env: McpEnv) -> None:
    endless = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
    started = time.monotonic()
    is_error, text = await call(env, "data", "run_query", {"sql": endless})
    assert is_error and "time limit" in text
    assert time.monotonic() - started < SQL_TIMEOUT_S + 4
    assert await dataset_count(env) == 0
    assert (await call_ok(env, "data", "run_query", {"sql": "SELECT 1 AS one"}))["preview"] == [[1]]


async def test_other_users_dataset_is_not_found(env: McpEnv) -> None:
    created = await call_ok(env, "data", "run_query", {"sql": "SELECT region, revenue FROM sales"}, ALICE)
    dataset_id = created["dataset_id"]
    assert (await call_ok(env, "data", "describe_dataset", {"dataset_id": dataset_id}, ALICE))["row_count"] == len(SALES)

    for tool, args in [
        ("describe_dataset", {"dataset_id": dataset_id}),
        ("get_dataset_rows", {"dataset_id": dataset_id}),
        ("query_datasets", {"sql": f'SELECT * FROM "{dataset_id}"', "dataset_ids": [dataset_id]}),
    ]:
        is_error, text = await call(env, "data", tool, args, BOB)
        assert is_error and text.startswith("error: dataset not found"), (tool, text)
    assert await artifacts.get_dataset(env.db, BOB, dataset_id) is None


async def test_query_datasets_joins_two_datasets(env: McpEnv) -> None:
    by_year = "SELECT region, SUM(revenue) AS revenue FROM sales WHERE year = {} GROUP BY region"
    y2024 = (await call_ok(env, "data", "run_query", {"sql": by_year.format(2024)}))["dataset_id"]
    y2025 = (await call_ok(env, "data", "run_query", {"sql": by_year.format(2025)}))["dataset_id"]

    joined = await call_ok(
        env,
        "compare",
        "query_datasets",
        {
            "sql": f'SELECT a.region, b.revenue - a.revenue AS delta FROM "{y2024}" a JOIN "{y2025}" b'
            " USING (region) ORDER BY a.region",
            "dataset_ids": [y2024, y2025],
            "name": "delta",
        },
    )
    assert joined["columns"] == [{"name": "region", "type": "TEXT"}, {"name": "delta", "type": "REAL"}]
    assert joined["preview"] == [["East", 5.5], ["North", 60.0], ["South", -20.0]]
    assert joined["name"] == "delta" and joined["truncated"] is False
    stored = await artifacts.get_dataset(env.db, ALICE, joined["dataset_id"])
    assert stored is not None and stored["rows"] == joined["preview"]


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
async def test_invalid_or_revoked_token_gets_401(env: McpEnv, path: str) -> None:
    url = env.url.removesuffix("/mcp") + path
    token = env.token("data")
    async with connect(url, token) as client:
        assert {t.name for t in (await client.list_tools()).tools} == EXPECTED_TOOLS["data"]
    env.tokens.revoke(token)

    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    async with httpx.AsyncClient(follow_redirects=False) as http:
        for headers in (
            {"Authorization": f"Bearer {token}"},  # revoked
            {"Authorization": "Bearer not-a-token"},
            {"Authorization": token},  # not a bearer credential
            {},
        ):
            response = await http.post(
                url, json=ping, headers={"Accept": "application/json, text/event-stream", **headers}
            )
            assert response.status_code == 401, headers
            assert response.json()["error"]["code"] == "invalid_token"
