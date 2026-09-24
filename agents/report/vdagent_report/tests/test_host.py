"""The host (`host.py`) dialing a fake in-process Backend hub over real gRPC, with scripted brains.

COPIED FROM `agents/_template/` — do not edit in an agent folder. A host copy that drifted from the
template fails here.

`agent_stub(agent)` connects the host to a fake hub and returns a call-shaped stub: `Invoke()` is a
per-turn handle (`write`, `read`, `code`, `cancel`) and `Compact(req)` returns the response; a
`failure` from the host surfaces as `grpc.aio.AioRpcError` with its code and details.
"""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent import futures
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import grpc
import pytest
from vdagent_proto import agent_pb2, agent_pb2_grpc

from .. import host
from ..contract import AgentConfigError, AgentTimeoutError, ContractViolation, InvocationContext, Message, ToolCall
from ..host import load_env_files, main, run_agent

Brain = Callable[[InvocationContext], Awaitable[None]]

NAME = "echo"
TOKEN = "tok-echo"
WAIT_S = 5.0


class FnAgent:
    """An agent whose turn is the given coroutine function; `compact` is scripted too."""

    def __init__(self, brain: Brain, compact: Callable[[str, list[Message]], Awaitable[str]] | None = None) -> None:
        self._brain = brain
        self._compact = compact

    async def invoke(self, ctx: InvocationContext) -> None:
        await self._brain(ctx)

    async def compact(self, previous_summary: str, messages: list[Message]) -> str:
        assert self._compact is not None
        return await self._compact(previous_summary, messages)


async def unused(ctx: InvocationContext) -> None:
    raise AssertionError("must not run")


PEERS = [
    agent_pb2.Peer(name="data", description="Queries the warehouse; returns dataset ids."),
    agent_pb2.Peer(name="compare", description="Compares datasets, periods and segments."),
]


def start_frame(
    history: list[agent_pb2.Message] | None = None, max_steps: int = 12, summary: str = ""
) -> agent_pb2.BackendFrame:
    return agent_pb2.BackendFrame(
        start=agent_pb2.InvokeStart(
            invocation_id="inv_000000000001",
            task_id="t_000000000001",
            user_id="u_000000000001",
            summary=summary,
            history=history or [agent_pb2.Message(role=agent_pb2.USER, content="[from: user] revenue by region?")],
            peers=PEERS,
            mcp_url="http://localhost:8000/mcp",
            mcp_token="tok-123",
            max_steps=max_steps,
        )
    )


# ---------------------------------------------------------------- fake Backend hub


def _rpc_error(code: grpc.StatusCode, details: str) -> grpc.aio.AioRpcError:
    return grpc.aio.AioRpcError(code, grpc.aio.Metadata(), grpc.aio.Metadata(), details=details)


class HubSession:
    """The hub side of one host session: uplinks are queued per `ref`."""

    def __init__(self, context: grpc.aio.ServicerContext) -> None:
        self._context = context
        self._queues: dict[str, asyncio.Queue[agent_pb2.AgentUplink]] = {}
        self._write_lock = asyncio.Lock()
        self._dropped = asyncio.Event()

    def queue(self, ref: str) -> asyncio.Queue[agent_pb2.AgentUplink]:
        return self._queues.setdefault(ref, asyncio.Queue())

    async def send(self, ref: str, **kind: Any) -> None:
        async with self._write_lock:
            await self._context.write(agent_pb2.HubDownlink(ref=ref, **kind))

    async def recv(self, ref: str) -> agent_pb2.AgentUplink:
        return await asyncio.wait_for(self.queue(ref).get(), WAIT_S)

    def drop(self) -> None:
        """End the session from the Backend side (e.g. a Backend restart)."""
        self._dropped.set()

    async def serve(self) -> None:
        reader = asyncio.create_task(self._read())
        dropped = asyncio.create_task(self._dropped.wait())
        try:
            await asyncio.wait({reader, dropped}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            reader.cancel()
            dropped.cancel()
        if self._dropped.is_set():
            await self._context.abort(grpc.StatusCode.UNAVAILABLE, "backend restarting")

    async def _read(self) -> None:
        while (msg := await self._context.read()) is not grpc.aio.EOF:
            self.queue(msg.ref).put_nowait(msg)


class FakeHub(agent_pb2_grpc.AgentHubServicer):
    """Accepts sessions whose hello carries `TOKEN`; hands each one to the test."""

    def __init__(self) -> None:
        self.hellos: list[agent_pb2.Hello] = []
        self._sessions: asyncio.Queue[HubSession] = asyncio.Queue()
        self.server: grpc.aio.Server | None = None

    async def Connect(self, request_iterator: Any, context: grpc.aio.ServicerContext) -> None:  # noqa: N802
        first = await context.read()
        self.hellos.append(first.hello)
        if first.hello.token != TOKEN:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, f"bad token for agent '{first.hello.agent}'")
        await context.write(agent_pb2.HubDownlink(welcome=agent_pb2.Welcome()))
        session = HubSession(context)
        self._sessions.put_nowait(session)
        await session.serve()

    async def next_session(self) -> HubSession:
        return await asyncio.wait_for(self._sessions.get(), WAIT_S)


async def start_hub(port: int = 0) -> tuple[FakeHub, int]:
    hub = FakeHub()
    hub.server = grpc.aio.server()
    agent_pb2_grpc.add_AgentHubServicer_to_server(hub, hub.server)
    bound = hub.server.add_insecure_port(f"127.0.0.1:{port}")
    await hub.server.start()
    return hub, bound


@asynccontextmanager
async def connected_host(agent: Any) -> AsyncIterator[tuple[FakeHub, HubSession]]:
    """Run the host for `agent` against a fresh fake hub; yields the hub and the first session."""
    hub, port = await start_hub()
    stop = asyncio.Event()
    runner = asyncio.create_task(run_agent(NAME, agent, f"127.0.0.1:{port}", TOKEN, stop))
    try:
        yield hub, await hub.next_session()
    finally:
        stop.set()
        await asyncio.wait_for(runner, WAIT_S)
        assert hub.server is not None
        await hub.server.stop(None)


class Turn:
    """One turn seen from the hub; shaped like the old `Invoke` call."""

    def __init__(self, session: HubSession) -> None:
        self._session = session
        self.ref = ""
        self._code: grpc.StatusCode | None = None

    async def write(self, frame: agent_pb2.BackendFrame) -> None:
        if not self.ref:
            self.ref = frame.start.invocation_id or "inv_without_start"
        await self._session.send(self.ref, frame=frame)

    async def read(self) -> Any:
        """The next frame; `grpc.aio.EOF` after `final`; a `failure` raises `AioRpcError`."""
        if self._code is not None:
            return grpc.aio.EOF
        msg = await self._session.recv(self.ref)
        if msg.WhichOneof("kind") == "failure":
            self._code = grpc.StatusCode[msg.failure.code]
            raise _rpc_error(self._code, msg.failure.detail)
        if msg.frame.WhichOneof("kind") == "final":
            self._code = grpc.StatusCode.OK
        return msg.frame

    async def code(self) -> grpc.StatusCode | None:
        return self._code

    async def cancel(self) -> None:
        await self._session.send(self.ref, cancel=agent_pb2.Cancel())


class HostStub:
    def __init__(self, session: HubSession) -> None:
        self.session = session
        self._compactions = 0

    def Invoke(self) -> Turn:  # noqa: N802
        return Turn(self.session)

    async def Compact(self, request: agent_pb2.CompactRequest) -> agent_pb2.CompactResponse:  # noqa: N802
        self._compactions += 1
        ref = f"cmp_{self._compactions:012x}"
        await self.session.send(ref, compact=request)
        msg = await self.session.recv(ref)
        if msg.WhichOneof("kind") == "failure":
            raise _rpc_error(grpc.StatusCode[msg.failure.code], msg.failure.detail)
        return msg.compacted


@asynccontextmanager
async def agent_stub(agent: Any) -> AsyncIterator[HostStub]:
    async with connected_host(agent) as (_, session):
        yield HostStub(session)


async def read_frame(call: Any) -> agent_pb2.AgentFrame:
    frame = await asyncio.wait_for(call.read(), timeout=WAIT_S)
    assert frame is not grpc.aio.EOF, "turn ended unexpectedly"
    return frame


async def read_all(call: Any) -> list[agent_pb2.AgentFrame]:
    frames: list[agent_pb2.AgentFrame] = []
    while (frame := await asyncio.wait_for(call.read(), timeout=WAIT_S)) is not grpc.aio.EOF:
        frames.append(frame)
    return frames


def kinds(frames: list[agent_pb2.AgentFrame]) -> list[str]:
    out = []
    for f in frames:
        kind = f.WhichOneof("kind")
        if kind == "message":
            kind = f"message:{agent_pb2.Role.Name(f.message.role).lower()}"
        out.append(kind)
    return out


async def run_turn(brain: Brain, start: agent_pb2.BackendFrame | None = None) -> tuple[list[agent_pb2.AgentFrame], Any]:
    """Run one turn that needs no Backend replies; returns `(frames, status code)`."""
    async with agent_stub(FnAgent(brain)) as stub:
        call = stub.Invoke()
        await call.write(start or start_frame())
        frames = await read_all(call)
        return frames, await call.code()


async def eventually(probe: Callable[[], bool]) -> None:
    async with asyncio.timeout(WAIT_S):
        while not probe():
            await asyncio.sleep(0.01)


# ---------------------------------------------------------------- turn basics


async def test_frames_follow_the_brains_steps_and_final_is_the_last_step():
    async def brain(ctx: InvocationContext) -> None:
        await ctx.emit_assistant("Looking it up.", [ToolCall("c1", "run_query", '{"sql": "SELECT 1"}')])
        await ctx.emit_tool_result("c1", '{"dataset_id": "ds_1"}')
        await ctx.emit_assistant("Revenue is in ds_1.")

    frames, code = await run_turn(brain)

    assert code == grpc.StatusCode.OK
    assert kinds(frames) == ["message:assistant", "message:tool", "message:assistant", "final"]
    first = frames[0].message
    assert first.content == "Looking it up."
    assert [(tc.id, tc.name, tc.arguments_json) for tc in first.tool_calls] == [
        ("c1", "run_query", '{"sql": "SELECT 1"}')
    ]
    assert (frames[1].message.tool_call_id, frames[1].message.content) == ("c1", '{"dataset_id": "ds_1"}')
    assert frames[3].final.content == "Revenue is in ds_1."


async def test_brain_receives_the_turn_as_plain_python():
    seen: dict[str, Any] = {}

    async def brain(ctx: InvocationContext) -> None:
        seen.update(
            ids=(ctx.invocation_id, ctx.task_id, ctx.user_id),
            summary=ctx.summary,
            history=ctx.history,
            peers=[(p.name, p.description) for p in ctx.peers],
            mcp=(ctx.mcp.url, ctx.mcp.token),
            max_steps=ctx.max_steps,
        )
        await ctx.emit_assistant("ok")

    history = [
        agent_pb2.Message(role=agent_pb2.USER, content="[from: user] revenue?"),
        agent_pb2.Message(
            role=agent_pb2.ASSISTANT,
            content="",
            tool_calls=[agent_pb2.ToolCall(id="c1", name="run_query", arguments_json='{"sql": "SELECT 1"}')],
        ),
        agent_pb2.Message(role=agent_pb2.TOOL, content="ds_1", tool_call_id="c1"),
        agent_pb2.Message(role=agent_pb2.ASSISTANT, content="It is ds_1."),
        agent_pb2.Message(role=agent_pb2.ROLE_UNSPECIFIED, content="dropped"),
        agent_pb2.Message(role=agent_pb2.USER, content="[from: compare] need 2024 too"),
    ]
    _, code = await run_turn(brain, start_frame(history=history, summary="User prefers EUR."))

    assert code == grpc.StatusCode.OK
    assert seen == {
        "ids": ("inv_000000000001", "t_000000000001", "u_000000000001"),
        "summary": "User prefers EUR.",
        "history": [
            {"role": "user", "content": "[from: user] revenue?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "run_query", "arguments": '{"sql": "SELECT 1"}'}}
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "ds_1"},
            {"role": "assistant", "content": "It is ds_1."},
            {"role": "user", "content": "[from: compare] need 2024 too"},
        ],
        "peers": [
            ("data", "Queries the warehouse; returns dataset ids."),
            ("compare", "Compares datasets, periods and segments."),
        ],
        "mcp": ("http://localhost:8000/mcp", "tok-123"),
        "max_steps": 12,
    }


async def test_unset_max_steps_defaults_to_twelve():
    seen: list[int] = []

    async def brain(ctx: InvocationContext) -> None:
        seen.append(ctx.max_steps)
        await ctx.emit_assistant("ok")

    await run_turn(brain, start_frame(max_steps=0))
    assert seen == [12]


async def test_malformed_start_fails_the_turn_with_invalid_argument():
    async with agent_stub(FnAgent(unused)) as stub:
        call = stub.Invoke()
        await call.write(agent_pb2.BackendFrame(start=agent_pb2.InvokeStart(invocation_id="inv_000000000009")))
        with pytest.raises(grpc.aio.AioRpcError) as err:
            await read_all(call)
    assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT


# ---------------------------------------------------------------- agent calls


def call_result(tool_call_id: str, content: str, ok: bool = True) -> agent_pb2.BackendFrame:
    return agent_pb2.BackendFrame(
        call_result=agent_pb2.AgentCallResult(tool_call_id=tool_call_id, ok=ok, content=content)
    )


async def test_concurrent_agent_calls_each_get_their_own_reply():
    replies: dict[str, str] = {}

    async def ask(ctx: InvocationContext, tool_call_id: str, target: str, message: str) -> None:
        replies[tool_call_id] = await ctx.call_agent(tool_call_id, target, message)
        await ctx.emit_tool_result(tool_call_id, replies[tool_call_id])

    async def brain(ctx: InvocationContext) -> None:
        await ctx.emit_assistant(
            "Asking both.",
            [
                ToolCall("tc_a", "send_to_agent", '{"agent": "data", "message": "revenue 2025"}'),
                ToolCall("tc_b", "send_to_agent", '{"agent": "compare", "message": "ds_1 vs ds_2"}'),
            ],
        )
        await asyncio.gather(
            ask(ctx, "tc_a", "data", "revenue 2025"),
            ask(ctx, "tc_b", "compare", "ds_1 vs ds_2"),
        )
        await ctx.emit_assistant("Done.")

    async with agent_stub(FnAgent(brain)) as stub:
        call = stub.Invoke()
        await call.write(start_frame())
        assert kinds([await read_frame(call)]) == ["message:assistant"]
        requests = {f.call.tool_call_id: (f.call.target, f.call.message) for f in [await read_frame(call), await read_frame(call)]}
        assert requests == {"tc_a": ("data", "revenue 2025"), "tc_b": ("compare", "ds_1 vs ds_2")}

        await call.write(call_result("tc_b", "ds_9"))
        first = await read_frame(call)
        assert (first.message.tool_call_id, first.message.content) == ("tc_b", "ds_9")
        await call.write(call_result("tc_a", "data failed: boom", ok=False))
        second = await read_frame(call)
        assert (second.message.tool_call_id, second.message.content) == ("tc_a", "error: data failed: boom")

        rest = await read_all(call)
        assert kinds(rest) == ["message:assistant", "final"]
        assert await call.code() == grpc.StatusCode.OK
    assert replies == {"tc_a": "error: data failed: boom", "tc_b": "ds_9"}


async def test_failed_call_reply_already_marked_as_error_is_kept_as_is():
    replies: list[str] = []

    async def brain(ctx: InvocationContext) -> None:
        await ctx.emit_assistant("", [ToolCall("tc", "send_to_agent", "{}")])
        replies.append(await ctx.call_agent("tc", "data", "hi"))
        await ctx.emit_tool_result("tc", replies[0])
        await ctx.emit_assistant("Sorry.")

    async with agent_stub(FnAgent(brain)) as stub:
        call = stub.Invoke()
        await call.write(start_frame())
        await read_frame(call)
        await read_frame(call)
        await call.write(call_result("tc", "error: calling data would deadlock", ok=False))
        await read_all(call)
    assert replies == ["error: calling data would deadlock"]


async def test_cancel_cancels_the_brain_and_nothing_more_is_sent():
    observed: list[str] = []

    async def brain(ctx: InvocationContext) -> None:
        await ctx.emit_assistant("", [ToolCall("tc", "send_to_agent", "{}")])
        try:
            await ctx.call_agent("tc", "data", "hi")
        except asyncio.CancelledError:
            observed.append("cancelled")
            raise

    async with agent_stub(FnAgent(brain)) as stub:
        call = stub.Invoke()
        await call.write(start_frame())
        assert kinds([await read_frame(call), await read_frame(call)]) == ["message:assistant", "call"]
        await call.cancel()
        await eventually(lambda: observed == ["cancelled"])
        await call.write(call_result("tc", "late reply"))  # dropped by the host
        await asyncio.sleep(0.1)
        assert stub.session.queue(call.ref).empty()

        again = stub.Invoke()  # the session survived: a new turn runs
        await again.write(start_frame())
        assert kinds([await read_frame(again)]) == ["message:assistant"]


async def test_session_loss_cancels_in_flight_turns_and_the_host_reconnects():
    observed: list[str] = []
    waiting = asyncio.Event()

    async def brain(ctx: InvocationContext) -> None:
        await ctx.emit_assistant("", [ToolCall("tc", "send_to_agent", "{}")])
        waiting.set()
        try:
            await ctx.call_agent("tc", "data", "hi")
        except asyncio.CancelledError:
            observed.append("cancelled")
            raise

    async with connected_host(FnAgent(brain)) as (hub, session):
        call = Turn(session)
        await call.write(start_frame())
        await asyncio.wait_for(waiting.wait(), WAIT_S)
        session.drop()
        second = await hub.next_session()
        assert observed == ["cancelled"]
        assert [h.agent for h in hub.hellos] == [NAME, NAME]

        again = Turn(second)
        await again.write(start_frame())
        assert kinds([await read_frame(again), await read_frame(again)]) == ["message:assistant", "call"]


# ---------------------------------------------------------------- contract rules (R2–R5)

RUN_Q = ToolCall("c1", "run_query", '{"sql": "SELECT 1"}')
ASK = ToolCall("tc", "send_to_agent", '{"agent": "data", "message": "hi"}')


async def assistant_while_unresolved(ctx: InvocationContext) -> None:
    await ctx.emit_assistant("", [RUN_Q])
    await ctx.emit_assistant("too early")


async def empty_tool_call_id(ctx: InvocationContext) -> None:
    await ctx.emit_assistant("", [ToolCall("", "run_query", "{}")])


async def duplicate_tool_call_ids(ctx: InvocationContext) -> None:
    await ctx.emit_assistant("", [RUN_Q, ToolCall("c1", "describe_table", "{}")])


async def result_for_unknown_call(ctx: InvocationContext) -> None:
    await ctx.emit_assistant("", [RUN_Q])
    await ctx.emit_tool_result("zz", "?")


async def result_before_any_step(ctx: InvocationContext) -> None:
    await ctx.emit_tool_result("c1", "?")


async def second_result_for_one_call(ctx: InvocationContext) -> None:
    await ctx.emit_assistant("", [RUN_Q, ToolCall("c2", "run_query", "{}")])
    await ctx.emit_tool_result("c1", "ds_1")
    await ctx.emit_tool_result("c1", "ds_1 again")


async def call_for_non_send_to_agent(ctx: InvocationContext) -> None:
    await ctx.emit_assistant("", [RUN_Q])
    await ctx.call_agent("c1", "data", "hi")


async def call_for_unknown_id(ctx: InvocationContext) -> None:
    await ctx.emit_assistant("", [ASK])
    await ctx.call_agent("nope", "data", "hi")


async def call_after_result(ctx: InvocationContext) -> None:
    await ctx.emit_assistant("", [ASK, RUN_Q])
    await ctx.emit_tool_result("tc", "answered without calling")
    await ctx.call_agent("tc", "data", "hi")


async def duplicate_call(ctx: InvocationContext) -> None:
    await ctx.emit_assistant("", [ASK])
    pending = asyncio.create_task(ctx.call_agent("tc", "data", "hi"))
    await asyncio.sleep(0.05)
    try:
        await ctx.call_agent("tc", "data", "again")
    finally:
        pending.cancel()


async def result_while_call_pending(ctx: InvocationContext) -> None:
    await ctx.emit_assistant("", [ASK])
    pending = asyncio.create_task(ctx.call_agent("tc", "data", "hi"))
    await asyncio.sleep(0.05)
    try:
        await ctx.emit_tool_result("tc", "made up")
    finally:
        pending.cancel()


async def return_with_unresolved_calls(ctx: InvocationContext) -> None:
    await ctx.emit_assistant("", [RUN_Q])


async def return_without_any_step(ctx: InvocationContext) -> None:
    return None


async def return_after_tool_results(ctx: InvocationContext) -> None:
    await ctx.emit_assistant("", [RUN_Q])
    await ctx.emit_tool_result("c1", "ds_1")


async def caught_violation(ctx: InvocationContext) -> None:
    try:
        await ctx.emit_tool_result("zz", "?")
    except ContractViolation:
        pass
    await ctx.emit_assistant("All good, honestly.")


VIOLATIONS = [
    (assistant_while_unresolved, "R2"),
    (empty_tool_call_id, "R2"),
    (duplicate_tool_call_ids, "R2"),
    (result_for_unknown_call, "R3"),
    (result_before_any_step, "R3"),
    (second_result_for_one_call, "R3"),
    (call_for_non_send_to_agent, "R4"),
    (call_for_unknown_id, "R4"),
    (call_after_result, "R4"),
    (duplicate_call, "R4"),
    (result_while_call_pending, "R4"),
    (return_with_unresolved_calls, "R5"),
    (return_without_any_step, "R5"),
    (return_after_tool_results, "R5"),
    (caught_violation, "R3"),
]


@pytest.mark.parametrize(("brain", "rule"), VIOLATIONS, ids=[b.__name__ for b, _ in VIOLATIONS])
async def test_contract_violation_fails_the_turn(brain: Brain, rule: str):
    raised: list[ContractViolation] = []

    async def watched(ctx: InvocationContext) -> None:
        try:
            await brain(ctx)
        except ContractViolation as exc:
            raised.append(exc)
            raise

    async with agent_stub(FnAgent(watched)) as stub:
        call = stub.Invoke()
        await call.write(start_frame())
        with pytest.raises(grpc.aio.AioRpcError) as err:
            await read_all(call)

    assert err.value.code() == grpc.StatusCode.INTERNAL
    details = err.value.details() or ""
    assert details.startswith("contract violation:")
    assert f"({rule})" in details
    if brain not in (return_with_unresolved_calls, return_without_any_step, return_after_tool_results, caught_violation):
        assert len(raised) == 1, "the violation is raised at the offending call"


async def test_violating_call_writes_no_frame():
    frames_seen: list[str] = []

    async def brain(ctx: InvocationContext) -> None:
        await ctx.emit_assistant("", [RUN_Q])
        await ctx.emit_tool_result("zz", "?")

    async with agent_stub(FnAgent(brain)) as stub:
        call = stub.Invoke()
        await call.write(start_frame())
        try:
            while (frame := await asyncio.wait_for(call.read(), timeout=5)) is not grpc.aio.EOF:
                frames_seen.append(kinds([frame])[0])
        except grpc.aio.AioRpcError:
            pass
    assert frames_seen == ["message:assistant"]


# ---------------------------------------------------------------- errors


async def timeout_plain(ctx: InvocationContext) -> None:
    raise AgentTimeoutError("LLM call timed out after 120s")


async def timeout_in_task_group(ctx: InvocationContext) -> None:
    async def slow() -> None:
        raise AgentTimeoutError("LLM call timed out after 120s")

    async with asyncio.TaskGroup() as tg:
        tg.create_task(slow())


async def provider_error(ctx: InvocationContext) -> None:
    raise RuntimeError("provider returned 500")


@pytest.mark.parametrize(
    ("brain", "status", "detail"),
    [
        (timeout_plain, grpc.StatusCode.DEADLINE_EXCEEDED, "LLM call timed out after 120s"),
        (timeout_in_task_group, grpc.StatusCode.DEADLINE_EXCEEDED, "LLM call timed out after 120s"),
        (provider_error, grpc.StatusCode.INTERNAL, "RuntimeError: provider returned 500"),
    ],
    ids=["timeout", "timeout-in-group", "other"],
)
async def test_brain_exceptions_map_to_grpc_status(brain: Brain, status: grpc.StatusCode, detail: str):
    async with agent_stub(FnAgent(brain)) as stub:
        call = stub.Invoke()
        await call.write(start_frame())
        with pytest.raises(grpc.aio.AioRpcError) as err:
            await read_all(call)
    assert err.value.code() == status
    assert detail in (err.value.details() or "")


# ---------------------------------------------------------------- compact


async def test_compact_hands_messages_to_the_brain_and_returns_its_summary():
    received: list[tuple[str, list[Message]]] = []

    async def compact(previous_summary: str, messages: list[Message]) -> str:
        received.append((previous_summary, messages))
        return "- ds_1: revenue by region 2025"

    async def unused(ctx: InvocationContext) -> None:
        raise AssertionError("must not run")

    async with agent_stub(FnAgent(unused, compact)) as stub:
        response = await stub.Compact(
            agent_pb2.CompactRequest(
                previous_summary="- ds_0: 2024 revenue",
                messages=[
                    agent_pb2.Message(role=agent_pb2.USER, content="[from: user] use EUR"),
                    agent_pb2.Message(
                        role=agent_pb2.ASSISTANT,
                        tool_calls=[agent_pb2.ToolCall(id="c1", name="run_query", arguments_json="{}")],
                    ),
                    agent_pb2.Message(role=agent_pb2.TOOL, content="ds_1", tool_call_id="c1"),
                ],
            )
        )
    assert response.summary == "- ds_1: revenue by region 2025"
    assert received == [
        (
            "- ds_0: 2024 revenue",
            [
                {"role": "user", "content": "[from: user] use EUR"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "run_query", "arguments": "{}"}}],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "ds_1"},
            ],
        )
    ]


@pytest.mark.parametrize(
    ("failure", "status"),
    [(AgentTimeoutError("summariser timed out"), grpc.StatusCode.DEADLINE_EXCEEDED), (RuntimeError("boom"), grpc.StatusCode.INTERNAL)],
    ids=["timeout", "other"],
)
async def test_compact_failures_map_to_grpc_status(failure: Exception, status: grpc.StatusCode):
    async def compact(previous_summary: str, messages: list[Message]) -> str:
        raise failure

    async def unused(ctx: InvocationContext) -> None:
        raise AssertionError("must not run")

    async with agent_stub(FnAgent(unused, compact)) as stub:
        with pytest.raises(grpc.aio.AioRpcError) as err:
            await stub.Compact(agent_pb2.CompactRequest(previous_summary="", messages=[]))
    assert err.value.code() == status
    assert str(failure) in (err.value.details() or "")


# ---------------------------------------------------------------- process


async def test_host_says_hello_with_its_name_and_token():
    async with connected_host(FnAgent(unused)) as (hub, _):
        (hello,) = hub.hellos
    assert (hello.agent, hello.token) == (NAME, TOKEN)
    assert hello.runtime


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_host_started_before_the_hub_connects_once_it_is_up():
    port = _free_port()
    stop = asyncio.Event()
    runner = asyncio.create_task(run_agent(NAME, FnAgent(unused), f"127.0.0.1:{port}", TOKEN, stop))
    await asyncio.sleep(0.3)  # at least one refused attempt
    hub, _ = await start_hub(port)
    try:
        await hub.next_session()
    finally:
        stop.set()
        await asyncio.wait_for(runner, WAIT_S)
        assert hub.server is not None
        await hub.server.stop(None)


@pytest.fixture
def main_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """`main` without real `.env` files: env loading has its own test."""
    monkeypatch.setattr(host, "load_env_files", lambda: None)
    monkeypatch.setenv("VDAGENT_AGENT_TOKEN_ECHO", TOKEN)
    return monkeypatch


def test_main_exits_2_naming_the_config_problem(main_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    def build_agent() -> Any:
        raise AgentConfigError("missing required environment variable LLM_MODEL")

    with pytest.raises(SystemExit) as exit_info:
        main(NAME, build_agent)
    assert exit_info.value.code == 2
    assert "echo: missing required environment variable LLM_MODEL" in capsys.readouterr().err


def test_main_exits_2_naming_a_missing_token(main_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    built: list[bool] = []

    def build_agent() -> Any:
        built.append(True)
        return FnAgent(unused)

    main_env.delenv("VDAGENT_AGENT_TOKEN_ECHO")
    with pytest.raises(SystemExit) as exit_info:
        main(NAME, build_agent)
    assert exit_info.value.code == 2
    assert "echo: missing required environment variable VDAGENT_AGENT_TOKEN_ECHO" in capsys.readouterr().err
    assert built == []


class _RefusingHub(agent_pb2_grpc.AgentHubServicer):
    def Connect(self, request_iterator: Any, context: grpc.ServicerContext) -> Any:  # noqa: N802
        next(request_iterator)
        context.abort(grpc.StatusCode.UNAUTHENTICATED, "bad token for agent 'echo'")


def test_main_exits_2_when_the_backend_refuses_the_session(
    main_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    agent_pb2_grpc.add_AgentHubServicer_to_server(_RefusingHub(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    main_env.setenv("VDAGENT_BACKEND", f"127.0.0.1:{port}")
    try:
        with pytest.raises(SystemExit) as exit_info:
            main(NAME, lambda: FnAgent(unused))
    finally:
        server.stop(None)
    assert exit_info.value.code == 2
    assert "bad token for agent 'echo'" in capsys.readouterr().err


def test_agent_env_file_beats_process_env_and_root_env_fills_the_gaps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for var in ("VDT_KEY", "VDT_ROOT_ONLY", "VDT_PROCESS_ONLY"):
        monkeypatch.setenv(var, "")  # restored (unset) after the test, even though load_dotenv sets them
        monkeypatch.delenv(var)
    agent_dir = tmp_path / "repo" / "agents" / "x"
    agent_dir.mkdir(parents=True)
    (tmp_path / "repo" / ".env").write_text("VDT_KEY=root\nVDT_ROOT_ONLY=root\nVDT_PROCESS_ONLY=root\n")
    (agent_dir / ".env").write_text("VDT_KEY=agent\n")
    monkeypatch.setenv("VDT_KEY", "process")
    monkeypatch.setenv("VDT_PROCESS_ONLY", "process")

    load_env_files(agent_dir)

    assert (os.environ["VDT_KEY"], os.environ["VDT_ROOT_ONLY"], os.environ["VDT_PROCESS_ONLY"]) == (
        "agent",
        "root",
        "process",
    )
