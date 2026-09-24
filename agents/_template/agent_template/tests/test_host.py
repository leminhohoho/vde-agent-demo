"""The host (`host.py`) driven over real in-process gRPC by a fake Backend and scripted brains.

COPIED FROM `agents/_template/` — do not edit in an agent folder. A host copy that drifted from the
template fails here.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import grpc
from vdagent_proto import agent_pb2, agent_pb2_grpc

from ..contract import InvocationContext, Message, ToolCall
from ..host import start_server

Brain = Callable[[InvocationContext], Awaitable[None]]


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


@asynccontextmanager
async def agent_stub(agent: Any) -> AsyncIterator[agent_pb2_grpc.AgentStub]:
    server, _, port = await start_server(agent, "127.0.0.1:0")
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            yield agent_pb2_grpc.AgentStub(channel)
    finally:
        await server.stop(None)


async def read_frame(call: Any) -> agent_pb2.AgentFrame:
    frame = await asyncio.wait_for(call.read(), timeout=5)
    assert frame is not grpc.aio.EOF, "stream ended unexpectedly"
    return frame


async def read_all(call: Any) -> list[agent_pb2.AgentFrame]:
    frames: list[agent_pb2.AgentFrame] = []
    while (frame := await asyncio.wait_for(call.read(), timeout=5)) is not grpc.aio.EOF:
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


async def test_first_frame_must_be_start():
    async def brain(ctx: InvocationContext) -> None:
        raise AssertionError("must not run")

    async with agent_stub(FnAgent(brain)) as stub:
        call = stub.Invoke()
        await call.write(
            agent_pb2.BackendFrame(call_result=agent_pb2.AgentCallResult(tool_call_id="x", ok=True, content="?"))
        )
        await call.done_writing()
        assert await read_all_or_status(call) == grpc.StatusCode.INVALID_ARGUMENT


async def read_all_or_status(call: Any) -> Any:
    try:
        await read_all(call)
    except grpc.aio.AioRpcError as err:
        return err.code()
    return await call.code()


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


async def test_backend_closing_the_stream_fails_a_pending_call():
    raised: list[BaseException] = []

    async def brain(ctx: InvocationContext) -> None:
        await ctx.emit_assistant("", [ToolCall("tc", "send_to_agent", "{}")])
        try:
            await ctx.call_agent("tc", "data", "hi")
        except Exception as exc:
            raised.append(exc)
            raise

    async with agent_stub(FnAgent(brain)) as stub:
        call = stub.Invoke()
        await call.write(start_frame())
        await read_frame(call)
        await read_frame(call)
        await call.done_writing()
        assert await read_all_or_status(call) == grpc.StatusCode.INTERNAL
    assert len(raised) == 1
