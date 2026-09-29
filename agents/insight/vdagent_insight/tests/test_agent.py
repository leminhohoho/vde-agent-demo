"""The plugin entry and the LangChain agent, driven through a recording `ctx` with a scripted chat
model and a fake MCP session."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import math

import httpx
import openai
import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from vdagent_sdk import Agent, AgentTimeoutError, McpEndpoint, Message, Note, Peer, PluginConfigError, ToolCall

from .. import setup
from ..legacy_agent import DESCRIPTION, NAME, STEP_LIMIT_TEXT, LangChainAgent, build_agent
from ..mcp_client import MAX_TOOL_RESULT_CHARS, TRUNCATION_MARKER, McpSession, McpTool, ToolOutcome
from ..settings import load_settings, read_env

SYSTEM_PROMPT = "You are the test agent."
COMPACT_PROMPT = "Summarise the conversation."
EXTRACT_PROMPT = "Extract durable findings as a JSON list."
LLM_ENV = {"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "http://llm", "LLM_MODEL": "m"}
PEERS = [
    Peer("data", "Queries the warehouse; returns dataset ids."),
    Peer("compare", "Compares datasets, periods and segments."),
]

Script = AIMessage | Exception | Callable[[list[BaseMessage]], AIMessage]


@dataclass
class ChatCall:
    messages: list[BaseMessage]
    tools: list[dict[str, Any]]

    @property
    def system(self) -> str:
        first = self.messages[0]
        return str(first.content) if isinstance(first, SystemMessage) else ""


@dataclass
class Recorder:
    """Scripted replies in order (the last entry repeats); every request is recorded."""

    script: Sequence[Script]
    calls: list[ChatCall] = field(default_factory=list)
    extract_reply: Script = field(default_factory=lambda: AIMessage("[]"))
    extract_calls: list[ChatCall] = field(default_factory=list)


class ScriptedChat(BaseChatModel):
    rec: Any

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any) -> Any:
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], **kwargs)

    def _reply(self, messages: list[BaseMessage], tools: list[dict[str, Any]]) -> ChatResult:
        rec: Recorder = self.rec
        call = ChatCall(list(messages), tools)
        if call.system == EXTRACT_PROMPT:
            rec.extract_calls.append(call)
            entry = rec.extract_reply
        else:
            rec.calls.append(call)
            entry = rec.script[min(len(rec.calls), len(rec.script)) - 1]
        if isinstance(entry, Exception):
            raise entry
        message = entry(messages) if callable(entry) else entry.model_copy(update={"id": None})  # fresh, like a model
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any) -> ChatResult:
        return self._reply(messages, kw.get("tools", []))

    async def _agenerate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        return self._reply(messages, kw.get("tools", []))


def chat(*script: Script) -> tuple[ScriptedChat, Recorder]:
    rec = Recorder(list(script))
    return ScriptedChat(rec=rec), rec


def ai(content: str = "", *calls: tuple[str | None, str, dict[str, Any]]) -> AIMessage:
    return AIMessage(content=content, tool_calls=[{"id": i, "name": n, "args": a, "type": "tool_call"} for i, n, a in calls])


class FakeEmbeddings(Embeddings):
    """`vectors[text]`, else [0, 1]; raises `fail` if set. Records every embedded text."""

    def __init__(self, vectors: dict[str, list[float]] | None = None, fail: Exception | None = None) -> None:
        self.vectors = vectors or {}
        self.fail = fail
        self.texts: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.texts.append(text)
        if self.fail:
            raise self.fail
        return self.vectors.get(text, [0.0, 1.0])

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]


def _cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return 1.0 - dot / (math.hypot(*a) * math.hypot(*b))


@dataclass
class FakeMemory:
    """In-process `Memory`: vector search by cosine distance; keyword search unused by this agent."""

    notes: list[tuple[Note, list[float] | None]] = field(default_factory=list)
    searches: list[tuple[str, list[float] | None]] = field(default_factory=list)
    fail: Exception | None = None

    def add(self, text: str, embedding: list[float], created_at: str = "2026-09-01T10:00:00Z") -> None:
        self.notes.append((Note(len(self.notes) + 1, "finding", text, created_at), embedding))

    async def save(self, text: str, kind: str = "note", embedding: Sequence[float] | None = None) -> int:
        self.notes.append((Note(len(self.notes) + 1, kind, text, "2026-09-28T00:00:00Z"), list(embedding) if embedding else None))
        return len(self.notes)

    async def search(self, query: str, limit: int = 5, embedding: Sequence[float] | None = None) -> list[Note]:
        self.searches.append((query, list(embedding) if embedding is not None else None))
        if self.fail:
            raise self.fail
        assert embedding is not None, "this agent always searches by vector"
        scored = [
            Note(n.id, n.kind, n.text, n.created_at, _cosine_distance(v, embedding))
            for n, v in self.notes
            if v is not None and len(v) == len(embedding)
        ]
        return sorted(scored, key=lambda n: n.score or 0.0)[:limit]

    async def recent(self, limit: int = 10) -> list[Note]:
        return [n for n, _ in reversed(self.notes)][:limit]

    async def delete(self, note_id: int) -> bool:
        raise AssertionError("this agent never deletes notes")

    def saved(self) -> list[tuple[str, str, list[float] | None]]:
        return [(n.kind, n.text, v) for n, v in self.notes]


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


@dataclass
class RecordingContext:
    """An `InvocationContext` that records every step; `call_agent` awaits `replies[tool_call_id]`."""

    history: list[Message] = field(default_factory=lambda: [{"role": "user", "content": "[from: user] revenue by region?"}])
    summary: str = ""
    max_steps: int = 12
    peers: list[Peer] = field(default_factory=lambda: list(PEERS))
    mcp: McpEndpoint = McpEndpoint("http://localhost:8000/mcp", "tok-123")
    invocation_id: str = "inv_1"
    task_id: str = "t_1"
    user_id: str = "u_1"
    memory: FakeMemory = field(default_factory=FakeMemory)
    replies: dict[str, asyncio.Future[str]] = field(default_factory=dict)
    events: list[tuple[str, Any, Any]] = field(default_factory=list)
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    async def emit_assistant(self, content: str, tool_calls: Sequence[ToolCall] = ()) -> None:
        self.events.append(("assistant", content, [tc.id for tc in tool_calls]))

    async def emit_tool_result(self, tool_call_id: str, content: str) -> None:
        self.events.append(("tool", tool_call_id, content))

    async def call_agent(self, tool_call_id: str, target: str, message: str) -> str:
        self.calls.append((tool_call_id, target, message))
        return await self.replies[tool_call_id]

    def kinds(self) -> list[str]:
        return [kind for kind, _, _ in self.events]

    def tool_results(self) -> dict[str, str]:
        return {tcid: content for kind, tcid, content in self.events if kind == "tool"}


@dataclass
class FakeAPI:
    agents: dict[str, tuple[str, Agent]] = field(default_factory=dict)
    plugin: str = "test"
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("test"))

    def register_agent(self, *, name: str, description: str, agent: Agent) -> None:
        self.agents[name] = (description, agent)

    def on_shutdown(self, fn: Callable[[], Awaitable[None]]) -> None:
        raise AssertionError("this plugin registers no shutdown hook")


RUN_QUERY = McpTool(
    name="run_query",
    description="Run a read-only SELECT on the warehouse.",
    input_schema={"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
)


def make_agent(model: ScriptedChat, mcp: FakeMcp, embeddings: FakeEmbeddings | None = None) -> LangChainAgent:
    return LangChainAgent(
        model=model,
        embeddings=embeddings or FakeEmbeddings(),
        mcp_session_factory=mcp.factory,
        system_prompt=SYSTEM_PROMPT,
        compact_prompt=COMPACT_PROMPT,
        extract_prompt=EXTRACT_PROMPT,
    )


def api_timeout() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=httpx.Request("POST", "http://llm/chat/completions"))


async def _until(probe: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not probe():
            await asyncio.sleep(0)


# --------------------------------------------------------------------------- plugin entry


def test_setup_registers_this_agent_built_from_the_plugin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.modules[setup.__module__], "read_env", lambda: dict(LLM_ENV))
    api = FakeAPI()
    setup(api, {})
    ((name, (description, agent)),) = api.agents.items()
    assert (name, description) == (NAME, DESCRIPTION) and isinstance(agent, LangChainAgent)


def test_setup_fails_with_the_missing_variable_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.modules[setup.__module__], "read_env", lambda: {})
    with pytest.raises(PluginConfigError, match="OPENAI_API_KEY"):
        setup(FakeAPI(), {})


def test_read_env_prefers_the_plugin_env_file_and_never_writes_os_environ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=from-file\nLLM_TIMEOUT_S=\n")
    monkeypatch.setenv("OPENAI_API_KEY", "from-process")
    monkeypatch.setenv("LLM_MODEL", "process-model")
    env = read_env(env_file)
    assert (env["OPENAI_API_KEY"], env["LLM_MODEL"], env["LLM_TIMEOUT_S"]) == ("from-file", "process-model", "")
    assert os.environ["OPENAI_API_KEY"] == "from-process"
    assert read_env(tmp_path / "missing.env")["OPENAI_API_KEY"] == "from-process"


def test_settings_name_the_missing_variable_and_default_the_timeout() -> None:
    env = {"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "http://llm"}
    with pytest.raises(PluginConfigError, match="LLM_MODEL"):
        load_settings(env)
    with pytest.raises(PluginConfigError, match="LLM_TIMEOUT_S"):
        load_settings({**env, "LLM_MODEL": "m", "LLM_TIMEOUT_S": "0"})
    settings = load_settings({**env, "LLM_MODEL": "m"})
    assert (settings.llm_timeout_s, settings.embed_model) == (120.0, "openai/text-embedding-3-small")
    assert load_settings({**env, "LLM_MODEL": "m", "EMBED_MODEL": " e "}).embed_model == "e"


def test_build_agent_reports_missing_configuration() -> None:
    with pytest.raises(PluginConfigError, match="OPENAI_BASE_URL"):
        build_agent({"OPENAI_API_KEY": "k"})


# --------------------------------------------------------------------------- the LangChain agent


async def test_last_step_has_no_tools_and_a_model_ignoring_that_yields_the_step_limit_text() -> None:
    model, rec = chat(ai("", ("c", "run_query", {"sql": "SELECT 1"})))  # keeps asking for tools
    mcp = FakeMcp(tools=[RUN_QUERY], handlers={"run_query": lambda a: ToolOutcome('{"dataset_id": "ds_1"}')})
    ctx = RecordingContext(max_steps=3)
    await make_agent(model, mcp).invoke(ctx)

    assert [len(c.tools) > 0 for c in rec.calls] == [True, True, False]
    assert ctx.kinds() == ["assistant", "tool", "assistant", "tool", "assistant"]
    assert ctx.events[-1] == ("assistant", STEP_LIMIT_TEXT, [])


async def test_concurrent_send_to_agent_calls_each_await_their_own_result() -> None:
    model, rec = chat(
        ai(
            "Asking both.",
            ("tc_a", "send_to_agent", {"agent": "data", "message": "revenue by region 2025"}),
            ("tc_b", "send_to_agent", {"agent": "compare", "message": "compare ds_1 vs ds_2"}),
        ),
        ai("Revenue grew in every region (ds_9)."),
    )
    loop = asyncio.get_running_loop()
    ctx = RecordingContext(replies={"tc_a": loop.create_future(), "tc_b": loop.create_future()})
    turn = asyncio.create_task(make_agent(model, FakeMcp(tools=[], handlers={})).invoke(ctx))

    await _until(lambda: len(ctx.calls) == 2)
    assert ctx.events == [("assistant", "Asking both.", ["tc_a", "tc_b"])]
    assert sorted(ctx.calls) == [("tc_a", "data", "revenue by region 2025"), ("tc_b", "compare", "compare ds_1 vs ds_2")]
    ctx.replies["tc_b"].set_result("ds_9")
    await _until(lambda: "tc_b" in ctx.tool_results())
    assert "tc_a" not in ctx.tool_results()
    ctx.replies["tc_a"].set_result("error: calling data would deadlock")
    await turn

    assert ctx.events[-1] == ("assistant", "Revenue grew in every region (ds_9).", [])
    tool_messages = {m.tool_call_id: m.content for m in rec.calls[1].messages if isinstance(m, ToolMessage)}
    assert tool_messages == {"tc_a": "error: calling data would deadlock", "tc_b": "ds_9"}


async def test_mcp_results_are_emitted_and_the_first_request_carries_prompt_history_and_tools() -> None:
    big = "x" * (MAX_TOOL_RESULT_CHARS + 500)
    model, rec = chat(
        ai("", ("q1", "run_query", {"sql": "SELECT region FROM dim_store"}), ("q2", "run_query", {"sql": "SELECT * FROM fact_sales"})),
        ai("Done: ds_1."),
    )
    results = {"SELECT region FROM dim_store": '{"dataset_id": "ds_1"}', "SELECT * FROM fact_sales": big}
    mcp = FakeMcp(tools=[RUN_QUERY], handlers={"run_query": lambda a: ToolOutcome(results[a["sql"]])})
    ctx = RecordingContext(
        summary="User prefers EUR.",
        history=[
            {"role": "user", "content": "[from: user] earlier"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "old", "type": "function", "function": {"name": "run_query", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "old", "content": "ds_0"},
            {"role": "assistant", "content": "ds_0 it is."},
            {"role": "user", "content": "[from: user] revenue by region?"},
        ],
    )
    await make_agent(model, mcp).invoke(ctx)

    assert mcp.opened_with == [("http://localhost:8000/mcp", "tok-123")]
    assert ctx.tool_results() == {"q1": '{"dataset_id": "ds_1"}', "q2": big[:MAX_TOOL_RESULT_CHARS] + TRUNCATION_MARKER}
    assert ctx.events[-1] == ("assistant", "Done: ds_1.", [])

    first = rec.calls[0]
    assert first.system == f"{SYSTEM_PROMPT}\n\n## Summary of earlier work with this user\nUser prefers EUR."
    assert [(type(m).__name__, m.content) for m in first.messages[1:]] == [
        ("HumanMessage", "[from: user] earlier"),
        ("AIMessage", ""),
        ("ToolMessage", "ds_0"),
        ("AIMessage", "ds_0 it is."),
        ("HumanMessage", "[from: user] revenue by region?"),
    ]
    tools = {t["function"]["name"]: t["function"] for t in first.tools}
    assert tools["run_query"]["parameters"] == RUN_QUERY.input_schema
    assert tools["send_to_agent"]["parameters"]["properties"]["agent"]["enum"] == ["data", "compare"]
    assert "- data: Queries the warehouse; returns dataset ids." in tools["send_to_agent"]["description"]


async def test_no_send_to_agent_tool_without_peers() -> None:
    model, rec = chat(ai("ok"))
    await make_agent(model, FakeMcp(tools=[RUN_QUERY], handlers={})).invoke(RecordingContext(peers=[]))
    assert [t["function"]["name"] for t in rec.calls[0].tools] == ["run_query"]


async def test_tool_failures_become_error_tool_content_and_the_turn_continues() -> None:
    def failing(args: dict[str, Any]) -> ToolOutcome:
        if args["sql"] == "boom":
            raise ConnectionError("MCP connection reset")
        return ToolOutcome("only SELECT statements are allowed", is_error=True)

    model, _ = chat(
        ai(
            "",
            ("e1", "run_query", {"sql": "INSERT INTO x VALUES (1)"}),
            ("e2", "run_query", {"sql": "boom"}),
            ("e4", "drop_database", {}),
            ("e5", "send_to_agent", {"agent": "data"}),
        ),
        ai("I could not run that query."),
    )
    ctx = RecordingContext()
    await make_agent(model, FakeMcp(tools=[RUN_QUERY], handlers={"run_query": failing})).invoke(ctx)

    results = ctx.tool_results()
    assert results["e1"] == "error: only SELECT statements are allowed"
    assert results["e2"].startswith("error:") and "MCP connection reset" in results["e2"]
    assert results["e4"] == "error: unknown tool 'drop_database'"
    assert results["e5"] == "error: send_to_agent requires a non-empty 'message'"
    assert ctx.events[-1] == ("assistant", "I could not run that query.", [])


async def test_tool_calls_without_ids_get_unique_ids_used_for_their_results() -> None:
    model, _ = chat(ai("", (None, "run_query", {"sql": "a"}), (None, "run_query", {"sql": "b"})), ai("done"))
    ctx = RecordingContext()
    await make_agent(model, FakeMcp(tools=[RUN_QUERY], handlers={"run_query": lambda a: ToolOutcome(a["sql"])})).invoke(ctx)

    _, _, ids = ctx.events[0]
    assert len(set(ids)) == 2 and all(ids)
    assert sorted(ctx.tool_results()) == sorted(ids)


async def test_model_timeout_is_agent_timeout_and_other_failures_propagate() -> None:
    timeout, _ = chat(api_timeout())
    with pytest.raises(AgentTimeoutError):
        await make_agent(timeout, FakeMcp([], {})).invoke(RecordingContext())
    broken, _ = chat(RuntimeError("provider returned 500"))
    with pytest.raises(RuntimeError, match="provider returned 500"):
        await make_agent(broken, FakeMcp([], {})).invoke(RecordingContext())


# --------------------------------------------------------------------------- memory

INBOUND = "[from: user] revenue by region?"


async def test_recalled_notes_are_found_by_the_inbound_embedding_and_reach_every_model_request() -> None:
    model, rec = chat(ai("", ("q1", "run_query", {"sql": "SELECT 1"})), ai("West fell again."))
    ctx = RecordingContext()
    ctx.memory.add("West revenue fell 8% in 2025 (ds_3).", [1.0, 0.0], created_at="2026-09-20T08:00:00Z")
    ctx.memory.add("Unrelated: staff rota.", [0.0, 1.0])
    embeddings = FakeEmbeddings({INBOUND: [1.0, 0.0]})
    mcp = FakeMcp(tools=[RUN_QUERY], handlers={"run_query": lambda a: ToolOutcome("ds_9")})
    await make_agent(model, mcp, embeddings).invoke(ctx)

    assert ctx.memory.searches[0] == (INBOUND, [1.0, 0.0])
    for call in rec.calls:
        assert call.system.startswith(SYSTEM_PROMPT)
        assert "(2026-09-20) West revenue fell 8% in 2025 (ds_3)." in call.system
    assert rec.calls[0].system.index("(2026-09-20) West") < rec.calls[0].system.index("Unrelated")  # nearest first


async def test_findings_extracted_from_the_answer_are_saved_with_their_embeddings() -> None:
    model, rec = chat(ai("West fell 8% (ds_3); East grew 12% (ds_4)."))
    rec.extract_reply = AIMessage('```json\n["West fell 8% in 2025 (ds_3).", "East grew 12% in 2025 (ds_4)."]\n```')
    embeddings = FakeEmbeddings({"West fell 8% in 2025 (ds_3).": [1.0, 0.0], "East grew 12% in 2025 (ds_4).": [0.6, 0.8]})
    ctx = RecordingContext()
    await make_agent(model, FakeMcp([], {}), embeddings).invoke(ctx)

    assert ctx.events[-1] == ("assistant", "West fell 8% (ds_3); East grew 12% (ds_4).", [])
    (extract,) = rec.extract_calls
    assert INBOUND in str(extract.messages[1].content)
    assert "West fell 8% (ds_3); East grew 12% (ds_4)." in str(extract.messages[1].content)
    assert ctx.memory.saved() == [
        ("finding", "West fell 8% in 2025 (ds_3).", [1.0, 0.0]),
        ("finding", "East grew 12% in 2025 (ds_4).", [0.6, 0.8]),
    ]


async def test_near_duplicate_findings_are_not_saved_again() -> None:
    model, rec = chat(ai("answer"))
    rec.extract_reply = AIMessage('["West fell 8 percent (ds_3).", "Electronics grew 58% (ds_5)."]')
    embeddings = FakeEmbeddings({"West fell 8 percent (ds_3).": [1.0, 0.01], "Electronics grew 58% (ds_5).": [0.0, 1.0]})
    ctx = RecordingContext()
    ctx.memory.add("West revenue fell 8% in 2025 (ds_3).", [1.0, 0.0])
    await make_agent(model, FakeMcp([], {}), embeddings).invoke(ctx)

    assert [text for _, text, _ in ctx.memory.saved()] == ["West revenue fell 8% in 2025 (ds_3).", "Electronics grew 58% (ds_5)."]


async def test_at_most_three_findings_are_saved_per_turn() -> None:
    model, rec = chat(ai("answer"))
    rec.extract_reply = AIMessage(json.dumps([f"finding {i}" for i in range(5)]))
    embeddings = FakeEmbeddings({f"finding {i}": [math.cos(i), math.sin(i)] for i in range(5)})
    ctx = RecordingContext()
    await make_agent(model, FakeMcp([], {}), embeddings).invoke(ctx)
    assert [text for _, text, _ in ctx.memory.saved()] == ["finding 0", "finding 1", "finding 2"]


@pytest.mark.parametrize("failure", ["embeddings", "search", "extraction_json", "extraction_call"])
async def test_memory_failures_never_fail_the_turn(failure: str) -> None:
    model, rec = chat(ai("final answer"))
    rec.extract_reply = {
        "extraction_json": AIMessage("these are not JSON findings"),
        "extraction_call": RuntimeError("extractor down"),
    }.get(failure, AIMessage('["a finding"]'))
    embeddings = FakeEmbeddings(fail=ConnectionError("embeddings down") if failure == "embeddings" else None)
    ctx = RecordingContext(memory=FakeMemory(fail=RuntimeError("db locked") if failure == "search" else None))
    await make_agent(model, FakeMcp([], {}), embeddings).invoke(ctx)

    assert ctx.events == [("assistant", "final answer", [])]
    assert ctx.memory.saved() == []
    assert rec.calls[0].system == SYSTEM_PROMPT


# --------------------------------------------------------------------------- compaction


async def test_compact_summarises_with_the_compact_prompt() -> None:
    model, rec = chat(ai("  - User wants EUR.\n- ds_1: revenue by region 2025  "))
    summary = await make_agent(model, FakeMcp([], {})).compact(
        "- Earlier: ds_0 is 2024 revenue.",
        [
            {"role": "user", "content": "[from: user] use EUR please"},
            {"role": "assistant", "content": "Here is ds_1."},
            {"role": "tool", "tool_call_id": "c1", "content": "y" * 2500},
        ],
    )
    assert summary == "- User wants EUR.\n- ds_1: revenue by region 2025"
    (only,) = rec.calls
    assert only.tools == [] and only.system == COMPACT_PROMPT
    rendered = str(only.messages[1].content)
    assert "ds_0 is 2024 revenue" in rendered and "[from: user] use EUR please" in rendered
    assert "Here is ds_1." in rendered
    assert "y" * 2000 + "…[truncated]" in rendered and "y" * 2001 not in rendered


async def test_compact_timeout_is_agent_timeout() -> None:
    model, _ = chat(api_timeout())
    with pytest.raises(AgentTimeoutError):
        await make_agent(model, FakeMcp([], {})).compact("", [])
