"""Engine / API test harness: five fake in-process `Agent` gRPC servers replaying scripted turns.

Each fake agent runs a per-test `handler(session)` coroutine for every `Invoke`; the `Session`
helpers write `AgentFrame`s and read `BackendFrame`s exactly like a real agent would.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc
from grpc_health.v1.health import aio as health_aio

from vdagent_backend.config import AgentSpec, Config
from vdagent_backend.db import repo
from vdagent_backend.db.database import create_db
from vdagent_backend.engine import AgentClients, Engine
from vdagent_backend.events import EventBus
from vdagent_backend.tokens import TokenRegistry
from vdagent_proto import agent_pb2 as pb
from vdagent_proto import agent_pb2_grpc

ALICE, BOB = "u_000000000001", "u_000000000002"
AGENTS = ("orchestrator", "data", "compare", "insight", "report")
WAIT_S = 5.0


class Session:
    """One fake `Invoke` stream, seen from the agent side."""

    def __init__(self, start: pb.InvokeStart, context: grpc.aio.ServicerContext) -> None:
        self.start = start
        self.context = context
        self._n = 0

    @property
    def inbound(self) -> str:
        return self.start.history[-1].content

    def _tcid(self) -> str:
        self._n += 1
        return f"{self.start.invocation_id}_c{self._n}"

    async def assistant(self, content: str = "", calls: list[tuple[str, str, dict[str, Any]]] | None = None) -> None:
        await self.context.write(
            pb.AgentFrame(
                message=pb.Message(
                    role=pb.ASSISTANT,
                    tool_calls=[pb.ToolCall(id=i, name=n, arguments_json=json.dumps(a)) for i, n, a in calls or []],
                    content=content,
                )
            )
        )

    async def tool(self, tool_call_id: str, content: str) -> None:
        await self.context.write(pb.AgentFrame(message=pb.Message(role=pb.TOOL, content=content, tool_call_id=tool_call_id)))

    async def call(self, tool_call_id: str, target: str, message: str) -> None:
        await self.context.write(pb.AgentFrame(call=pb.AgentCallRequest(tool_call_id=tool_call_id, target=target, message=message)))

    async def result(self) -> pb.AgentCallResult:
        frame = await self.context.read()
        assert frame is not grpc.aio.EOF, "stream closed while waiting for call_result"
        assert frame.WhichOneof("kind") == "call_result", frame
        return frame.call_result

    async def final(self, content: str, *, with_message: bool = True) -> None:
        """End the turn like a real agent: the last assistant message (no tool calls), then `final`."""
        if with_message:
            await self.assistant(content)
        await self.context.write(pb.AgentFrame(final=pb.Final(content=content)))

    def send_to(self, target: str, message: str) -> tuple[str, str, dict[str, Any]]:
        return (self._tcid(), "send_to_agent", {"agent": target, "message": message})

    async def ask(self, target: str, message: str) -> pb.AgentCallResult:
        """One full send_to_agent step: assistant(tool_call) → call → call_result → tool message."""
        tc = self.send_to(target, message)
        await self.assistant(calls=[tc])
        await self.call(tc[0], target, message)
        res = await self.result()
        await self.tool(tc[0], res.content)
        return res


Handler = Callable[[Session], Awaitable[None]]


async def _echo(session: Session) -> None:
    await session.final(f"done: {session.inbound}")


@dataclass
class FakeAgent(agent_pb2_grpc.AgentServicer):
    name: str
    handler: Handler = _echo
    starts: list[pb.InvokeStart] = field(default_factory=list)
    compacts: list[pb.CompactRequest] = field(default_factory=list)
    compact_fails: bool = False
    health: health_aio.HealthServicer = field(default_factory=health_aio.HealthServicer)
    server: grpc.aio.Server | None = None
    address: str = ""

    async def Invoke(self, request_iterator, context):  # noqa: N802, ANN001
        first = await context.read()
        assert first.WhichOneof("kind") == "start"
        self.starts.append(first.start)
        await self.handler(Session(first.start, context))

    async def Compact(self, request, context):  # noqa: N802, ANN001
        self.compacts.append(request)
        if self.compact_fails:
            await context.abort(grpc.StatusCode.INTERNAL, "summariser down")
        return pb.CompactResponse(summary=f"SUMMARY#{len(self.compacts)}")

    async def set_healthy(self, healthy: bool) -> None:
        status = health_pb2.HealthCheckResponse.SERVING if healthy else health_pb2.HealthCheckResponse.NOT_SERVING
        await self.health.set("", status)


@dataclass
class Harness:
    cfg: Config
    db: Any
    bus: EventBus
    tokens: TokenRegistry
    clients: AgentClients
    engine: Engine
    agents: dict[str, FakeAgent]

    def on(self, agent: str, handler: Handler) -> None:
        self.agents[agent].handler = handler

    async def post(self, agent: str, content: str, user_id: str = ALICE) -> str:
        task, _ = await self.engine.post_message(user_id, agent, content)
        return task["id"]

    async def wait_task(self, task_id: str, status: str | None = None) -> dict[str, Any]:
        row = await wait_for(lambda: self._task_done(task_id))
        if status is not None:
            assert row["status"] == status, row
        return row

    async def _task_done(self, task_id: str) -> dict[str, Any] | None:
        row = await repo.get_task(self.db, task_id)
        return row if row and row["status"] != "running" else None

    async def invocations(self, task_id: str) -> list[dict[str, Any]]:
        return await repo.list_task_invocations(self.db, task_id)

    async def stack(self, agent: str, user_id: str = ALICE) -> list[dict[str, Any]]:
        return await repo.messages_page(self.db, user_id, agent, None, 1000)

    async def idle(self) -> None:
        """Wait until no invocation is queued or running."""
        await wait_for(lambda: self._idle())

    async def _idle(self) -> bool:
        return not await repo.inflight_invocations(self.db)


async def wait_for(probe: Callable[[], Awaitable[Any]], timeout: float = WAIT_S) -> Any:
    async with asyncio.timeout(timeout):
        while True:
            value = await probe()
            if value:
                return value
            await asyncio.sleep(0.01)


def seed_users(path: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.executemany("INSERT INTO users (id, name) VALUES (?, ?)", [(ALICE, "Alice"), (BOB, "Bob")])
    conn.close()


async def start_fake_agents() -> dict[str, FakeAgent]:
    agents: dict[str, FakeAgent] = {}
    for name in AGENTS:
        fake = FakeAgent(name)
        server = grpc.aio.server()
        agent_pb2_grpc.add_AgentServicer_to_server(fake, server)
        health_pb2_grpc.add_HealthServicer_to_server(fake.health, server)
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        await fake.set_healthy(True)
        fake.server, fake.address = server, f"127.0.0.1:{port}"
        agents[name] = fake
    return agents


def make_config(tmp_path: Path, agents: dict[str, FakeAgent], **overrides: Any) -> Config:
    cfg = Config(
        backend_db=str(tmp_path / "backend.db"),
        warehouse_db=str(tmp_path / "warehouse.db"),
        mcp_public_url="http://mcp.test/mcp",
        frontend_dist=str(tmp_path / "no-dist"),
        max_depth=4,
        max_steps=12,
        health_interval_s=3600,
        agents={n: AgentSpec(n, a.address, f"{n} agent") for n, a in agents.items()},
    )
    return replace(cfg, **overrides)


@pytest.fixture
async def fake_agents() -> AsyncIterator[dict[str, FakeAgent]]:
    agents = await start_fake_agents()
    yield agents
    await asyncio.gather(*(a.server.stop(None) for a in agents.values() if a.server))


@pytest.fixture
def cfg_overrides() -> dict[str, Any]:
    return {}


@pytest.fixture
async def harness(tmp_path: Path, fake_agents: dict[str, FakeAgent], cfg_overrides: dict[str, Any]) -> AsyncIterator[Harness]:
    cfg = make_config(tmp_path, fake_agents, **cfg_overrides)
    db = create_db(cfg.backend_db)
    seed_users(cfg.backend_db)
    bus, tokens = EventBus(), TokenRegistry()
    clients = AgentClients(cfg.agents, health_interval_s=cfg.health_interval_s)
    engine = Engine(cfg, db, bus, tokens, clients)
    await engine.start()
    yield Harness(cfg, db, bus, tokens, clients, engine, fake_agents)
    await engine.stop()
    await db.dispose()
