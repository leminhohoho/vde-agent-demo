"""Engine / API test harness: five fake agents that dial the in-process hub and replay scripted turns.

Each fake agent runs a per-test `handler(session)` coroutine for every turn started on its hub
session; the `Session` helpers send `AgentFrame`s and read `call_result`s exactly like a real agent.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import grpc
import pytest

from hub_client import HubClient
from vdagent_backend.config import AgentSpec, Config
from vdagent_backend.db import repo
from vdagent_backend.db.database import create_db
from vdagent_backend.engine import AgentHub, Engine
from vdagent_backend.events import EventBus
from vdagent_backend.tokens import TokenRegistry
from vdagent_proto import agent_pb2 as pb

ALICE, BOB = "u_000000000001", "u_000000000002"
AGENTS = ("orchestrator", "data", "compare", "insight", "report")
WAIT_S = 5.0


class Session:
    """One fake turn, seen from the agent side."""

    def __init__(self, start: pb.InvokeStart, agent: FakeAgent, ref: str) -> None:
        self.start = start
        self.ref = ref
        self._agent = agent
        self._n = 0
        self.inbox: asyncio.Queue[pb.BackendFrame] = asyncio.Queue()

    @property
    def inbound(self) -> str:
        return self.start.history[-1].content

    def _tcid(self) -> str:
        self._n += 1
        return f"{self.start.invocation_id}_c{self._n}"

    async def _write(self, frame: pb.AgentFrame) -> None:
        assert self._agent.client is not None
        await self._agent.client.frame(self.ref, frame)

    async def assistant(self, content: str = "", calls: list[tuple[str, str, dict[str, Any]]] | None = None) -> None:
        await self._write(
            pb.AgentFrame(
                message=pb.Message(
                    role=pb.ASSISTANT,
                    tool_calls=[pb.ToolCall(id=i, name=n, arguments_json=json.dumps(a)) for i, n, a in calls or []],
                    content=content,
                )
            )
        )

    async def tool(self, tool_call_id: str, content: str) -> None:
        await self._write(pb.AgentFrame(message=pb.Message(role=pb.TOOL, content=content, tool_call_id=tool_call_id)))

    async def call(self, tool_call_id: str, target: str, message: str) -> None:
        await self._write(pb.AgentFrame(call=pb.AgentCallRequest(tool_call_id=tool_call_id, target=target, message=message)))

    async def result(self) -> pb.AgentCallResult:
        frame = await self.inbox.get()
        assert frame.WhichOneof("kind") == "call_result", frame
        return frame.call_result

    async def final(self, content: str, *, with_message: bool = True) -> None:
        """End the turn like a real agent: the last assistant message (no tool calls), then `final`."""
        if with_message:
            await self.assistant(content)
        await self._write(pb.AgentFrame(final=pb.Final(content=content)))

    async def fail(self, code: str, detail: str) -> None:
        """End the turn with a `failure`, like the host after a brain error."""
        assert self._agent.client is not None
        await self._agent.client.send(self.ref, failure=pb.Failure(code=code, detail=detail))

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
class FakeAgent:
    """A hub client that runs `handler` for every turn and answers compactions."""

    name: str
    handler: Handler = _echo
    starts: list[pb.InvokeStart] = field(default_factory=list)
    compacts: list[pb.CompactRequest] = field(default_factory=list)
    compact_fails: bool = False
    cancelled: list[str] = field(default_factory=list)  # refs the hub cancelled
    client: HubClient | None = None
    hub: AgentHub | None = None
    _sessions: dict[str, Session] = field(default_factory=dict)
    _tasks: set[asyncio.Task[None]] = field(default_factory=set)
    _turn_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)

    async def connect(self, hub: AgentHub) -> None:
        assert hub.port is not None
        self.hub, self.client = hub, HubClient(hub.port)
        welcome = await self.client.hello(self.name)
        assert welcome.WhichOneof("kind") == "welcome", welcome
        self._spawn(self._loop())

    async def disconnect(self) -> None:
        """Drop the session like a crashed agent; returns once the hub sees the agent unhealthy."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.client is not None:
            await self.client.close()
            self.client = None
        if self.hub is not None:
            hub = self.hub
            await wait_for(lambda: _true(not hub.is_healthy(self.name)))

    def _spawn(self, coro: Awaitable[None]) -> asyncio.Task[None]:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _loop(self) -> None:
        assert self.client is not None
        with contextlib.suppress(grpc.aio.AioRpcError):
            while (msg := await self.client.call.read()) is not grpc.aio.EOF:
                kind, ref = msg.WhichOneof("kind"), msg.ref
                if kind == "frame" and msg.frame.WhichOneof("kind") == "start":
                    self.starts.append(msg.frame.start)
                    session = self._sessions[ref] = Session(msg.frame.start, self, ref)
                    self._turn_tasks[ref] = self._spawn(self._turn(session))
                elif kind == "frame" and ref in self._sessions:
                    self._sessions[ref].inbox.put_nowait(msg.frame)
                elif kind == "cancel":
                    self.cancelled.append(ref)
                    task = self._turn_tasks.get(ref)
                    if task is not None:
                        task.cancel()
                elif kind == "compact":
                    self._spawn(self._compact(ref, msg.compact))

    async def _turn(self, session: Session) -> None:
        try:
            await self.handler(session)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # a broken script fails its turn, like a crashing brain
            await session.fail("INTERNAL", repr(e))

    async def _compact(self, ref: str, request: pb.CompactRequest) -> None:
        assert self.client is not None
        self.compacts.append(request)
        if self.compact_fails:
            await self.client.send(ref, failure=pb.Failure(code="INTERNAL", detail="summariser down"))
        else:
            await self.client.send(ref, compacted=pb.CompactResponse(summary=f"SUMMARY#{len(self.compacts)}"))


async def _true(value: bool) -> bool:
    return value


@dataclass
class Harness:
    cfg: Config
    db: Any
    bus: EventBus
    tokens: TokenRegistry
    hub: AgentHub
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


async def connect_all(agents: dict[str, FakeAgent], hub: AgentHub) -> None:
    for agent in agents.values():
        await agent.connect(hub)


def make_config(tmp_path: Path, agents: dict[str, FakeAgent], **overrides: Any) -> Config:
    cfg = Config(
        backend_db=str(tmp_path / "backend.db"),
        warehouse_db=str(tmp_path / "warehouse.db"),
        mcp_public_url="http://mcp.test/mcp",
        frontend_dist=str(tmp_path / "no-dist"),
        agent_listen="127.0.0.1:0",
        max_depth=4,
        max_steps=12,
        agents={n: AgentSpec(n, f"{n} agent") for n in agents},
    )
    return replace(cfg, **overrides)


@pytest.fixture
async def fake_agents() -> AsyncIterator[dict[str, FakeAgent]]:
    agents = {name: FakeAgent(name) for name in AGENTS}
    yield agents
    for agent in agents.values():
        agent.hub = None  # the hub may already be closed: do not wait for it
        await agent.disconnect()


@pytest.fixture
def cfg_overrides() -> dict[str, Any]:
    return {}


@pytest.fixture
async def harness(tmp_path: Path, fake_agents: dict[str, FakeAgent], cfg_overrides: dict[str, Any]) -> AsyncIterator[Harness]:
    cfg = make_config(tmp_path, fake_agents, **cfg_overrides)
    db = create_db(cfg.backend_db)
    seed_users(cfg.backend_db)
    bus, tokens = EventBus(), TokenRegistry()
    hub = AgentHub(cfg.agents)
    engine = Engine(cfg, db, bus, tokens, hub)
    await engine.start()
    await connect_all(fake_agents, hub)
    yield Harness(cfg, db, bus, tokens, hub, engine, fake_agents)
    await engine.stop()
    await db.dispose()
