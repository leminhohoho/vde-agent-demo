"""Agent runtime (spec §13): the real grpc.aio servicer driven in-process with a scripted LLM and fake MCP."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc
from vdagent_proto import agent_pb2, agent_pb2_grpc

from vdagent_agents.llm import AssistantMessage, LLMTimeoutError, ToolCall
from vdagent_agents.mcp_client import MAX_TOOL_RESULT_CHARS, TRUNCATION_MARKER, McpSession, McpTool, ToolOutcome
from vdagent_agents.server import AgentService, start_server
from vdagent_agents.settings import SettingsError, load_settings

SYSTEM_PROMPT = "You are the test agent."
COMPACT_PROMPT = "Summarise the conversation."

Script = AssistantMessage | Exception | Callable[[list[dict[str, Any]], list[dict[str, Any]], str], AssistantMessage]


@dataclass
class LLMCall:
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    tool_choice: str


@dataclass
class ScriptedLLM:
    """Returns scripted replies in order; the last entry repeats once the script is exhausted."""

    script: list[Script]
    calls: list[LLMCall] = field(default_factory=list)

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], tool_choice: str):
        self.calls.append(LLMCall(json.loads(json.dumps(messages)), tools, tool_choice))
        entry = self.script[min(len(self.calls), len(self.script)) - 1]
        if isinstance(entry, Exception):
            raise entry
        if callable(entry):
            return entry(messages, tools, tool_choice)
        return entry


@dataclass
class FakeMcp:
    tools: list[McpTool]
    handlers: dict[str, Callable[[dict[str, Any]], ToolOutcome]]
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    opened_with: list[tuple[str, str]] = field(default_factory=list)

    async def list_tools(self) -> list[McpTool]:
        return self.tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, arguments))
        return self.handlers[name](arguments)

    @asynccontextmanager
    async def factory(self, url: str, token: str) -> AsyncIterator[McpSession]:
        self.opened_with.append((url, token))
        yield self


RUN_QUERY = McpTool(
    name="run_query",
    description="Run a read-only SELECT on the warehouse.",
    input_schema={"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
)
PEERS = [
    agent_pb2.Peer(name="data", description="Queries the warehouse; returns dataset ids."),
    agent_pb2.Peer(name="compare", description="Compares datasets, periods and segments."),
]


def tool_call(id: str, name: str, args: dict[str, Any] | str) -> ToolCall:
    return ToolCall(id=id, name=name, arguments_json=args if isinstance(args, str) else json.dumps(args))


def start_frame(max_steps: int = 12, summary: str = "") -> agent_pb2.BackendFrame:
    return agent_pb2.BackendFrame(
        start=agent_pb2.InvokeStart(
            invocation_id="inv_000000000001",
            task_id="t_000000000001",
            user_id="u_000000000001",
            summary=summary,
            history=[agent_pb2.Message(role=agent_pb2.USER, content="[from: user] revenue by region?")],
            peers=PEERS,
            mcp_url="http://localhost:8000/mcp",
            mcp_token="tok-123",
            max_steps=max_steps,
        )
    )


@asynccontextmanager
async def agent_stub(llm: ScriptedLLM, mcp: FakeMcp) -> AsyncIterator[agent_pb2_grpc.AgentStub]:
    service = AgentService(
        agent_name="orchestrator",
        llm=llm,
        mcp_session_factory=mcp.factory,
        system_prompt=SYSTEM_PROMPT,
        compact_prompt=COMPACT_PROMPT,
    )
    server, _, port = await start_server(service, "127.0.0.1:0")
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


async def test_terminates_at_max_steps_with_tool_choice_none_on_last_step():
    # A model that keeps asking for tools, even when told not to.
    llm = ScriptedLLM([AssistantMessage(content="", tool_calls=[tool_call("c", "run_query", {"sql": "SELECT 1"})])])
    mcp = FakeMcp(tools=[RUN_QUERY], handlers={"run_query": lambda a: ToolOutcome('{"dataset_id": "ds_1"}')})
    async with agent_stub(llm, mcp) as stub:
        call = stub.Invoke()
        await call.write(start_frame(max_steps=3))
        frames = await read_all(call)
        assert await call.code() == grpc.StatusCode.OK

    assert [c.tool_choice for c in llm.calls] == ["auto", "auto", "none"]
    assert kinds(frames) == [
        "message:assistant", "message:tool",
        "message:assistant", "message:tool",
        "message:assistant", "final",
    ]  # fmt: skip
    last_assistant = frames[-2].message
    assert list(last_assistant.tool_calls) == []  # no unresolved calls may precede `final`
    assert frames[-1].final.content == last_assistant.content != ""


async def test_concurrent_send_to_agent_calls_each_await_their_own_result():
    llm = ScriptedLLM(
        [
            AssistantMessage(
                content="Asking both.",
                tool_calls=[
                    tool_call("tc_a", "send_to_agent", {"agent": "data", "message": "revenue by region 2025"}),
                    tool_call("tc_b", "send_to_agent", {"agent": "compare", "message": "compare ds_1 vs ds_2"}),
                ],
            ),
            AssistantMessage(content="Revenue grew in every region (ds_9)."),
        ]
    )
    mcp = FakeMcp(tools=[], handlers={})
    async with agent_stub(llm, mcp) as stub:
        call = stub.Invoke()
        await call.write(start_frame())
        assert kinds([await read_frame(call)]) == ["message:assistant"]

        # Both calls are issued before either result arrives: they run concurrently.
        calls = {f.call.tool_call_id: f.call for f in [await read_frame(call), await read_frame(call)]}
        assert {k: (c.target, c.message) for k, c in calls.items()} == {
            "tc_a": ("data", "revenue by region 2025"),
            "tc_b": ("compare", "compare ds_1 vs ds_2"),
        }

        # Answer out of order; each result is routed to its own call.
        await call.write(
            agent_pb2.BackendFrame(call_result=agent_pb2.AgentCallResult(tool_call_id="tc_b", ok=True, content="ds_9"))
        )
        first = await read_frame(call)
        assert (first.message.role, first.message.tool_call_id, first.message.content) == (
            agent_pb2.TOOL, "tc_b", "ds_9"
        )  # fmt: skip
        await call.write(
            agent_pb2.BackendFrame(
                call_result=agent_pb2.AgentCallResult(
                    tool_call_id="tc_a", ok=False, content="error: calling data would deadlock"
                )
            )
        )
        second = await read_frame(call)
        assert (second.message.tool_call_id, second.message.content) == ("tc_a", "error: calling data would deadlock")

        rest = await read_all(call)
        assert kinds(rest) == ["message:assistant", "final"]
        assert rest[-1].final.content == "Revenue grew in every region (ds_9)."
        assert await call.code() == grpc.StatusCode.OK

    tool_messages = {m["tool_call_id"]: m["content"] for m in llm.calls[1].messages if m["role"] == "tool"}
    assert tool_messages == {"tc_a": "error: calling data would deadlock", "tc_b": "ds_9"}


async def test_mcp_results_are_emitted_as_tool_messages():
    big = "x" * (MAX_TOOL_RESULT_CHARS + 500)
    llm = ScriptedLLM(
        [
            AssistantMessage(
                content="",
                tool_calls=[
                    tool_call("q1", "run_query", {"sql": "SELECT region FROM dim_store"}),
                    tool_call("q2", "run_query", {"sql": "SELECT * FROM fact_sales"}),
                ],
            ),
            AssistantMessage(content="Done: ds_1."),
        ]
    )
    results = {"SELECT region FROM dim_store": '{"dataset_id": "ds_1"}', "SELECT * FROM fact_sales": big}
    mcp = FakeMcp(tools=[RUN_QUERY], handlers={"run_query": lambda a: ToolOutcome(results[a["sql"]])})
    async with agent_stub(llm, mcp) as stub:
        call = stub.Invoke()
        await call.write(start_frame(summary="User prefers EUR."))
        frames = await read_all(call)

    assert mcp.opened_with == [("http://localhost:8000/mcp", "tok-123")]
    assert sorted(mcp.calls, key=lambda c: c[1]["sql"]) == [
        ("run_query", {"sql": "SELECT * FROM fact_sales"}),
        ("run_query", {"sql": "SELECT region FROM dim_store"}),
    ]
    tool_msgs = {f.message.tool_call_id: f.message.content for f in frames if f.message.role == agent_pb2.TOOL}
    assert tool_msgs["q1"] == '{"dataset_id": "ds_1"}'
    assert tool_msgs["q2"] == big[:MAX_TOOL_RESULT_CHARS] + TRUNCATION_MARKER

    first = llm.calls[0]
    assert first.messages[0] == {
        "role": "system",
        "content": f"{SYSTEM_PROMPT}\n\n## Summary of earlier work with this user\nUser prefers EUR.",
    }
    assert first.messages[1:] == [{"role": "user", "content": "[from: user] revenue by region?"}]
    tools = {t["function"]["name"]: t["function"] for t in first.tools}
    assert tools["run_query"]["parameters"] == RUN_QUERY.input_schema
    assert tools["send_to_agent"]["parameters"]["properties"]["agent"]["enum"] == ["data", "compare"]
    assert "- data: Queries the warehouse; returns dataset ids." in tools["send_to_agent"]["description"]


async def test_tool_failures_become_error_tool_content_and_the_turn_continues():
    def failing(args: dict[str, Any]) -> ToolOutcome:
        if args["sql"] == "boom":
            raise ConnectionError("MCP connection reset")
        return ToolOutcome("only SELECT statements are allowed", is_error=True)

    llm = ScriptedLLM(
        [
            AssistantMessage(
                content="",
                tool_calls=[
                    tool_call("e1", "run_query", {"sql": "INSERT INTO x VALUES (1)"}),
                    tool_call("e2", "run_query", {"sql": "boom"}),
                    tool_call("e3", "run_query", "{not json"),
                    tool_call("e4", "drop_database", {}),
                ],
            ),
            AssistantMessage(content="I could not run that query."),
        ]
    )
    mcp = FakeMcp(tools=[RUN_QUERY], handlers={"run_query": failing})
    async with agent_stub(llm, mcp) as stub:
        call = stub.Invoke()
        await call.write(start_frame())
        frames = await read_all(call)
        assert await call.code() == grpc.StatusCode.OK

    tool_msgs = {f.message.tool_call_id: f.message.content for f in frames if f.message.role == agent_pb2.TOOL}
    assert tool_msgs["e1"] == "error: only SELECT statements are allowed"
    assert tool_msgs["e2"].startswith("error:") and "MCP connection reset" in tool_msgs["e2"]
    assert tool_msgs["e3"].startswith("error: invalid JSON arguments for 'run_query'")
    assert tool_msgs["e4"] == "error: unknown tool 'drop_database'"
    assert frames[-1].final.content == "I could not run that query."


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (LLMTimeoutError("LLM call timed out after 120s"), grpc.StatusCode.DEADLINE_EXCEEDED),
        (RuntimeError("provider returned 500"), grpc.StatusCode.INTERNAL),
    ],
)
async def test_llm_failure_aborts_the_stream(failure: Exception, status: grpc.StatusCode):
    llm = ScriptedLLM([failure])
    async with agent_stub(llm, FakeMcp(tools=[], handlers={})) as stub:
        call = stub.Invoke()
        await call.write(start_frame())
        with pytest.raises(grpc.aio.AioRpcError) as err:
            await read_all(call)
    assert err.value.code() == status
    assert str(failure) in (err.value.details() or "")


async def test_compact_returns_the_summary():
    llm = ScriptedLLM([AssistantMessage(content="  - User wants EUR.\n- ds_1: revenue by region 2025  ")])
    async with agent_stub(llm, FakeMcp(tools=[], handlers={})) as stub:
        response = await stub.Compact(
            agent_pb2.CompactRequest(
                previous_summary="- Earlier: ds_0 is 2024 revenue.",
                messages=[
                    agent_pb2.Message(role=agent_pb2.USER, content="[from: user] use EUR please"),
                    agent_pb2.Message(role=agent_pb2.ASSISTANT, content="Here is ds_1."),
                ],
            )
        )
    assert response.summary == "- User wants EUR.\n- ds_1: revenue by region 2025"
    (only,) = llm.calls
    assert only.messages[0] == {"role": "system", "content": COMPACT_PROMPT}
    rendered = only.messages[1]["content"]
    assert "ds_0 is 2024 revenue" in rendered and "[from: user] use EUR please" in rendered
    assert "Here is ds_1." in rendered


async def test_health_reports_serving_after_startup():
    service = AgentService(agent_name="data", llm=ScriptedLLM([]))  # loads the packaged prompts
    server, _, port = await start_server(service, "127.0.0.1:0")
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            reply = await health_pb2_grpc.HealthStub(channel).Check(health_pb2.HealthCheckRequest(service=""))
    finally:
        await server.stop(None)
    assert reply.status == health_pb2.HealthCheckResponse.SERVING


def test_missing_required_env_var_is_named():
    env = {"AGENT_NAME": "data", "OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "http://llm"}
    with pytest.raises(SettingsError, match="LLM_MODEL"):
        load_settings(env)
    settings = load_settings({**env, "LLM_MODEL": "m"})
    assert (settings.grpc_port, settings.llm_timeout_s) == (50051, 120.0)
