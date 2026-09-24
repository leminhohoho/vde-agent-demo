# vdagent — Agent Template — Design Spec

Status: approved design, pre-implementation · Date: 2026-09-24
Amends: `2026-09-24-vdagent-design.md` (§2.1 Agents, §2.3 layout, §7 Agent service, §12, §13).

## 1. Purpose and scope

Replace the single shared agent runtime (`agents/vdagent_agents/`, one package run five times with
`AGENT_NAME`) with:

1. **An agent template** — `agents/_template/`, a self-contained, copyable skeleton any agent is
   built from. It owns the gRPC side of the Backend↔agent protocol once, exposes a small
   framework-agnostic Python API, and leaves the "brain" (LLM, framework, tools) entirely to the
   developer: LiteLLM, LangGraph/LangChain, the OpenAI Agents SDK, or plain code.
2. **The five existing agents rebuilt on it** — one self-contained folder each
   (`agents/orchestrator/`, `data/`, `compare/`, `insight/`, `report/`), behaviour unchanged.

In scope: template (host, contract, stub agent, tests, README with framework recipes), migration of
the five agents, workspace/pytest/pyright config, Docker/compose, amendments to the main spec.

Out of scope: any Backend, proto, or Frontend change; non-Python agents; a runnable non-LiteLLM
example agent; a local-tool registry in the LiteLLM agents (local tools are a documented capability
only, §6); agent shutdown hooks.

### 1.1 Decisions log

| # | Decision |
|---|---|
| T1 | **Python only.** The template is a Python skeleton; other languages are not supported. |
| T2 | **Copy, not import.** The template is a skeleton that is copied per agent; there is no shared agent library. Each agent folder is fully self-contained. |
| T3 | **One folder per agent**, including the five LiteLLM agents (each carries its own copy of the host and of the LiteLLM loop). Any agent can switch framework without touching the others. |
| T4 | **Framework openness via README recipes** (LangGraph, OpenAI Agents SDK), not a runnable example. |
| T5 | **Callback-context API**: the developer implements `invoke(ctx)` and `compact(...)`; `ctx` offers `emit_assistant`, `emit_tool_result`, `call_agent`. No protobuf in developer code. |
| T6 | **Host guards the protocol locally**: frame-ordering rules the Backend enforces (main spec §5.3) are checked in-process and raise `ContractViolation` at the offending call site. |
| T7 | Entrypoint `python -m <package>`; `__main__.py` calls `host.main(NAME, build_agent)`. `AGENT_NAME` env var is removed. |
| T8 | Registry (`backend/config.yaml`) and MCP permissions (`backend/.../mcp/tools.py` `PERMISSIONS`) stay in the Backend. |
| T9 | The template's only dependencies: `vdagent-proto`, `grpcio`, `grpcio-health-checking`, `python-dotenv`. No LLM, framework, or MCP client. |
| T10 | Agents may run **local tools** in-process; they must still be emitted (§6). |
| T11 | Tests live **inside each agent package** (`vdagent_<name>/tests/`) so identically named test files in different agents get unique module names under pytest's default import mode. |
| T12 | `invoke` returns `None`. The turn's final answer is the content of the **last emitted assistant step, which must have no tool calls**; the host sends `final` from it. (Streaming adapters forward every AI message and need no special final handling.) |

## 2. Layout

```
agents/
├── _template/                     # the skeleton; copy to create an agent
├── orchestrator/
├── data/
├── compare/
├── insight/
└── report/
```

Removed: `agents/pyproject.toml`, `agents/vdagent_agents/`, `agents/tests/`.

### 2.1 Template folder

```
agents/_template/
├── README.md                      # contract, "create an agent" steps, tools, framework recipes
├── pyproject.toml                 # name "vdagent-agent-template"; deps per T9; hatchling, packages = ["agent_template"]
└── agent_template/                # rename to vdagent_<name> when copying
    ├── __init__.py
    ├── __main__.py                # copied, never edited
    ├── contract.py                # copied, never edited
    ├── host.py                    # copied, never edited
    ├── agent.py                   # EDIT: NAME, build_agent(), the Agent implementation (echo stub)
    └── tests/
        ├── __init__.py
        ├── test_host.py           # copied, never edited
        └── test_agent.py          # EDIT: the agent's own tests (stub example)
```

Files marked "copied, never edited" start with a module docstring saying so and naming
`agents/_template/` as the source of truth.

### 2.2 A migrated agent (all five identical in shape)

```
agents/orchestrator/
├── README.md                      # role, MCP tools granted by the Backend, env vars, how to run
├── pyproject.toml                 # "vdagent-orchestrator"; template deps + litellm, mcp
└── vdagent_orchestrator/
    ├── __init__.py
    ├── __main__.py                # copied
    ├── contract.py                # copied
    ├── host.py                    # copied
    ├── agent.py                   # NAME="orchestrator", build_agent(), LiteLLMAgent (tool loop + compact)
    ├── llm.py                     # LLMClient protocol + LiteLLMClient
    ├── mcp_client.py              # MCP session, schema conversion, truncation
    ├── settings.py                # LLM env vars → AgentConfigError on bad/missing values
    ├── prompts/
    │   ├── system.md              # role prompt (was prompts/orchestrator.md)
    │   └── compact.md
    └── tests/
        ├── __init__.py
        ├── test_host.py           # copied
        └── test_agent.py          # LiteLLM loop tests (ported from agents/tests/test_agent_runtime.py)
```

| Agent | Package | Local port | Prompt source |
|---|---|---|---|
| orchestrator | `vdagent_orchestrator` | 50051 | `prompts/orchestrator.md` |
| data | `vdagent_data` | 50052 | `prompts/data.md` |
| compare | `vdagent_compare` | 50053 | `prompts/compare.md` |
| insight | `vdagent_insight` | 50054 | `prompts/insight.md` |
| report | `vdagent_report` | 50055 | `prompts/report.md` |

### 2.3 Migration map (old → new, per LiteLLM agent)

| Old | New |
|---|---|
| `server.py` `AgentService`, `_CallRouter`, `start_server`, `serve`, `main`, error mapping | `host.py` (generic, no LLM) |
| `settings.py` `load_env_file`, `GRPC_PORT` | `host.py` |
| `settings.py` LLM vars (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `LLM_MODEL`, `LLM_TIMEOUT_S`) | `settings.py` (per agent) |
| `settings.py` `AGENT_NAMES`, `AGENT_NAME` | removed; `agent.py` `NAME` |
| `loop.py` `Invocation`, `send_to_agent_tool`, `build_system_prompt`, `compact`, `render_for_compaction` | `agent.py` `LiteLLMAgent`, operating on `ctx` and OpenAI-shaped dicts |
| `loop.py` `history_to_openai` | `host.py` (proto → dict conversion) |
| `llm.py` | unchanged in the agent package, except its `ToolCall` is replaced by `contract.ToolCall` (one type) |
| `mcp_client.py` | unchanged in the agent package |
| `server.py` quieting `httpx`/`LiteLLM` loggers | `agent.py` `build_agent()` |
| `LLMTimeoutError` | stays in `llm.py`; `LiteLLMAgent` catches it around each LLM call and raises `AgentTimeoutError(str(exc)) from exc` |

## 3. Contract (`contract.py`)

Developer-facing types. Full docstrings in the file; the README restates the rules.

```python
SEND_TO_AGENT = "send_to_agent"      # the Backend only accepts agent calls from a tool call with this name

Message = dict[str, Any]
"""OpenAI chat-completions shape:
{"role": "user", "content": str}
{"role": "assistant", "content": str | None, "tool_calls": [{"id", "type": "function", "function": {"name", "arguments"}}]}
{"role": "tool", "tool_call_id": str, "content": str}"""

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str

@dataclass(frozen=True)
class Peer:
    name: str
    description: str

@dataclass(frozen=True)
class McpEndpoint:
    url: str
    token: str                       # send `Authorization: Bearer <token>`; valid for this turn only

class InvocationContext(Protocol):
    invocation_id: str
    task_id: str
    user_id: str
    summary: str                     # rolling summary of earlier tasks; "" if none
    history: list[Message]           # uncompacted, seq order; last item is the inbound "[from: <sender>] …"
    peers: list[Peer]                # every other agent
    mcp: McpEndpoint
    max_steps: int                   # LLM-call budget for this turn (Backend-provided; 12 if unset)

    async def emit_assistant(self, content: str, tool_calls: Sequence[ToolCall] = ()) -> None: ...
    async def emit_tool_result(self, tool_call_id: str, content: str) -> None: ...
    async def call_agent(self, tool_call_id: str, target: str, message: str) -> str: ...

class Agent(Protocol):
    async def invoke(self, ctx: InvocationContext) -> None: ...
    async def compact(self, previous_summary: str, messages: list[Message]) -> str: ...

class AgentConfigError(Exception): ...    # raise from build_agent → process exits 2 with the message
class AgentTimeoutError(Exception): ...   # raise from invoke/compact → gRPC DEADLINE_EXCEEDED
class ContractViolation(Exception): ...   # raised by the host; the turn fails with INTERNAL
```

`call_agent` returns the peer's reply text. When the Backend rejects or the peer fails, it returns
`error: …` text (never raises for that). It raises only if the Backend ends the stream while the
call is pending; that exception must propagate.

### 3.1 Rules

| # | Rule | Host-enforced |
|---|---|:-:|
| R1 | No memory between turns: `summary` + `history` is the whole truth. | — |
| R2 | Emit an assistant step (with its tool calls) before any result or `call_agent` for those calls. A new assistant step may only be emitted when every tool call of the previous one has a result. Tool-call ids are non-empty and unique within a step. | ✓ |
| R3 | Every tool call gets exactly one `emit_tool_result` — including `send_to_agent`: `call_agent`, then emit the reply as its result. | ✓ |
| R4 | `call_agent` only for an unresolved `send_to_agent` call of the latest assistant step, at most once per id; no `emit_tool_result` for that id while its `call_agent` is pending. | ✓ |
| R5 | The turn ends when `invoke` returns. At that point every tool call is resolved and the last emitted assistant step has no tool calls; its content is the final answer. Nothing may be emitted after `invoke` returns. | ✓ |
| R6 | Tool failures (MCP, local, bad arguments) become result content `error: …` and the turn continues. LLM timeout → raise `AgentTimeoutError`. Any other exception fails the turn. | mapping ✓ |
| R7 | Make at most `ctx.max_steps` LLM calls. | — |
| R8 | One agent object serves concurrent turns (different users/tasks): no per-turn state on `self`. | — |
| R9 | Never swallow `asyncio.CancelledError` — task cancel from the Backend arrives as cancellation. | — |

## 4. Host (`host.py`)

Public surface: `main(name, build_agent)`, `start_server(agent, address) -> (server, health, port)`
(used by tests), `load_env_file()`. Everything else is private.

### 4.1 `main(name, build_agent)`

1. `logging.basicConfig(INFO, "%(asctime)s %(levelname)s %(name)s: %(message)s")`.
2. Load the repo-root `.env` without overriding process env (nearest `.env` walking up from
   `host.py`, else `find_dotenv(usecwd=True)` — today's logic).
3. `GRPC_PORT` (default 50051); non-integer → stderr `"<name>: GRPC_PORT must be an integer; got …"`, exit 2.
4. `agent = build_agent()`; `AgentConfigError` → stderr `"<name>: <message>"`, exit 2.
5. `start_server(agent, f"[::]:{port}")`: `grpc.aio` server with the `Agent` servicer and
   `grpc.health.v1`; health `SERVING` for `""` and `vdagent.v1.Agent`. Log
   `"agent <name> serving on port <port>"`.
6. On SIGINT/SIGTERM: health graceful shutdown, `server.stop(5.0)`.

### 4.2 `Invoke`

1. First frame must be `start`, else abort `INVALID_ARGUMENT`.
2. Build a stream-backed `InvocationContext` from `InvokeStart` (proto → dataclasses/dicts;
   assistant messages with tool calls get `content: None` when empty, matching today's
   `AssistantMessage.to_openai`; unspecified roles are skipped with a warning; `max_steps <= 0` → 12).
3. Start a reader task: `call_result` frames resolve the pending `call_agent` future by
   `tool_call_id`; EOF or read error fails all pending futures with a stream-closed error; other
   frames are logged and ignored (today's `_CallRouter`).
4. `await agent.invoke(ctx)`. Frame writes are serialised by a lock.
5. After return: enforce R5 (context closed; later emits raise `ContractViolation`), send
   `final(content=<last assistant step content>)`.
6. Errors: any `ContractViolation` recorded during the turn → abort `INTERNAL`,
   `"contract violation: <message>"` (even if the agent caught it); `AgentTimeoutError` anywhere in
   the exception (including exception groups) → `DEADLINE_EXCEEDED`; any other exception →
   `INTERNAL` with `"<Type>: <message>"`. Always cancel the reader task.

### 4.3 Ordering guard (T6)

Per turn: `latest: dict[id, name]`, `unresolved: set[id]`, `calling: set[id]`, `called: set[id]`,
`last_step_had_calls: bool | None`, `closed: bool`, `violation: str | None`. Each `emit_*` /
`call_agent` checks R2–R5 before writing a frame; a failure records the violation and raises
`ContractViolation` with a message naming the rule and the tool-call id, e.g.
`"call_agent('c1'): tool call 'c1' is 'run_query', not send_to_agent (R4)"`.

### 4.4 `Compact`

Convert `CompactRequest.messages` to `Message` dicts, `await agent.compact(previous_summary,
messages)`, return `CompactResponse(summary=…)`. Error mapping as §4.2 step 6.

## 5. Template stub (`agent_template/agent.py`)

```python
NAME = "echo"

class EchoAgent:
    async def invoke(self, ctx: InvocationContext) -> None:
        await ctx.emit_assistant(f"echo: {ctx.history[-1]['content']}")

    async def compact(self, previous_summary: str, messages: list[Message]) -> str:
        lines = [previous_summary] if previous_summary else []
        lines += [m["content"] for m in messages if m["role"] == "user"]
        return "\n".join(lines)[-2000:]   # keeps the newest 2 000 chars

def build_agent() -> Agent:
    return EchoAgent()
```

Runnable with `uv run python -m agent_template`; pointing a `backend/config.yaml` entry at it makes a
human message come back as `echo: [from: user] …`.

## 6. Tools

| Kind | Executed by | Access control | Reported via |
|---|---|---|---|
| MCP tool | Backend `/mcp` (connect with `ctx.mcp`) | Backend `PERMISSIONS` | `emit_assistant` → `emit_tool_result` |
| `send_to_agent` | Backend routes to the peer | Backend call checks (main spec §4.4) | `emit_assistant` → `call_agent` → `emit_tool_result` |
| Local tool | The agent process | The agent | `emit_assistant` → `emit_tool_result` |

Local-tool rules (README):
1. Emit them. Unemitted steps are valid for the Backend but vanish from the next turn's history
   and from the UI; only the final text survives.
2. Artifacts other agents or the user must reference (`ds_`/`ch_`/`rp_`) must be created through
   MCP. Never open `warehouse.db` or `backend.db` directly — that bypasses read-only and per-user
   ownership (main spec I4).
3. No per-user state across turns (R1, R8).
4. Apply your own timeout (the Backend has no per-turn deadline; MCP tools use 30 s).
5. Never name a tool `send_to_agent`; avoid MCP tool names; truncate large results (16 000 chars,
   `…[truncated]`, as MCP results).

## 7. LiteLLM agents

`agent.py` holds `LiteLLMAgent(llm, mcp_session_factory, system_prompt, compact_prompt)`, a port
of today's `Invocation` + `compact` onto `ctx`:

- Opens the MCP session with `ctx.mcp.url` / `ctx.mcp.token`, lists tools, adds `send_to_agent`
  (roster from `ctx.peers`) when peers exist.
- System prompt = `prompts/system.md` + summary section (`## Summary of earlier work with this user`).
- Step loop up to `ctx.max_steps`; `tool_choice="none"` on the last step; tool calls returned
  anyway on the last step are dropped and replaced with `STEP_LIMIT_TEXT` content (today's behaviour).
- Each reply → `emit_assistant`; tool calls run concurrently (`TaskGroup`); `send_to_agent` →
  `call_agent`; MCP → `run_mcp_tool`; invalid JSON / unknown tool → `error: …`; each result →
  `emit_tool_result`.
- `LLMTimeoutError` → `AgentTimeoutError`.
- `compact` renders messages as today (`render_for_compaction`, tool text capped at 2 000 chars)
  and calls the LLM with `prompts/compact.md`.

`build_agent()` loads `settings.py` (required `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `LLM_MODEL`;
`LLM_TIMEOUT_S` default 120, positive), raising `AgentConfigError` naming the variable; builds
`LiteLLMClient`; lowers `httpx`/`httpx2`/`LiteLLM` loggers to WARNING.

The five agents' code is identical except `NAME`, package name, and `prompts/system.md`.

## 8. README (`agents/_template/README.md`)

Sections:
1. **What an agent is** — host vs. brain; which files are copied vs. edited; the Backend owns
   registry, permissions, routing, history.
2. **Create an agent** — copy `_template` to `agents/<name>`; rename `agent_template` →
   `vdagent_<name>`; set `name` and `packages` in `pyproject.toml`; set `NAME`; add the member,
   dependency, and source to the root `pyproject.toml` and the folder to pyright `extraPaths`; add
   the `COPY agents/<name>/pyproject.toml` line to `Dockerfile.python`; register in
   `backend/config.yaml`, `backend/config.compose.yaml`, and `docker-compose.yml`; grant MCP tools
   in `PERMISSIONS`; implement `agent.py`; run `uv run pytest agents/<name>`.
3. **The contract** — §3 types and rules R1–R9, the turn lifecycle diagram.
4. **Tools** — §6.
5. **Recipes** (unexecuted sketches, marked as such, written against the frameworks' docs current
   at implementation time):
   - *LangGraph*: history dicts → LangChain messages; MCP tools via `langchain-mcp-adapters`
     (streamable HTTP, bearer header); `send_to_agent` as a tool receiving its id through
     `InjectedToolCallId` and calling `ctx.call_agent`; emit assistant steps and tool results
     from inside the graph (model hook / tool wrapper) rather than from the stream consumer, so
     ordering (R2) cannot lag execution.
   - *OpenAI Agents SDK*: MCP via its streamable-HTTP MCP server with the bearer header;
     `send_to_agent` as a function tool using the tool context's call id; emit one assistant step
     per model response (all its tool calls together) and each tool output.
   - Both: map framework timeouts to `AgentTimeoutError`; cap steps at `ctx.max_steps`.
6. **Testing** — how `test_host.py` drives the host; writing `test_agent.py` with fakes.

## 9. Workspace, tooling, deployment

- Root `pyproject.toml`: `members = ["proto", "backend", "agents/_template", "agents/orchestrator",
  "agents/data", "agents/compare", "agents/insight", "agents/report"]`; root `dependencies` and
  `[tool.uv.sources]` list the six agent packages instead of `vdagent-agents`; pytest
  `testpaths = ["backend/tests", "agents"]`; pyright `extraPaths` lists each agent folder.
  `uv.lock` regenerated.
- `Dockerfile.python`: one `COPY agents/<name>/pyproject.toml agents/<name>/pyproject.toml` line
  per agent folder (incl. `_template`) before the dependency sync; `COPY agents/ agents/` stays.
- `docker-compose.yml`: drop the shared `command` from `x-agent`; each agent service sets
  `command: ["python", "-m", "vdagent_<name>"]`; remove `AGENT_NAME`. `GRPC_PORT: "50051"` stays.
  `config.compose.yaml` unchanged.
- Local run: `GRPC_PORT=50052 uv run python -m vdagent_data` (×5).

## 10. Testing

No real LLM. Tests drive the real `grpc.aio` server in-process (today's harness style).

**`test_host.py`** (template; copied into every agent — a drifted host copy fails its own test):
- First frame not `start` → `INVALID_ARGUMENT`.
- Scripted brain: assistant step with tool call → tool result → final step ⇒ frames
  `message:assistant, message:tool, message:assistant, final`; `final.content` = last step content.
- History conversion: user / assistant-with-tool-calls (`content: None`) / tool messages reach the
  brain as documented dicts; `summary`, `peers`, `mcp`, `max_steps` (0 → 12) populated.
- Two concurrent `call_agent`s answered out of order each get their own reply.
- Backend closes the stream while a call is pending → `call_agent` raises; turn ends.
- Each enforced rule violated (R2 assistant while unresolved, duplicate/empty ids; R3/R4
  `emit_tool_result` unknown id, `call_agent` on non-`send_to_agent`, duplicate `call_agent`,
  result while call pending; R5 return with unresolved calls, return with no final step) →
  `ContractViolation` at the call site and stream `INTERNAL` "contract violation: …", including
  when the brain catches it.
- `AgentTimeoutError` (plain and inside an exception group) → `DEADLINE_EXCEEDED`; other
  exception → `INTERNAL`.
- `Compact` returns the brain's summary; timeout mapping.
- Health `SERVING` after `start_server`.
- `main`: `AgentConfigError` and bad `GRPC_PORT` exit 2 with the named message.

**`test_agent.py`** (template): the echo stub's reply and compact output.

**`test_agent.py`** (each LiteLLM agent; ported from today's `test_agent_runtime.py`): terminates at
`max_steps` with `tool_choice="none"` on the last step; concurrent `send_to_agent` calls each
produce a `call` and consume the matching `call_result`; MCP results emitted as tool messages and
truncated; tool failures and unknown tools become `error:` content and the turn continues;
`LLMTimeoutError` → `DEADLINE_EXCEEDED`; `Compact` returns the summary; missing `LLM_MODEL` →
`AgentConfigError` naming it.

## 11. Main-spec amendments

In `2026-09-24-vdagent-design.md`:
- §2.1 *Agents*: "one folder per agent built from `agents/_template/`" instead of "one shared
  runtime package run as five processes (`AGENT_NAME=…`)"; link this spec.
- §2.3 layout: replace the `agents/` subtree with §2 of this spec.
- §7.1: entrypoint `python -m vdagent_<name>`; env: `GRPC_PORT` (host) + LLM vars (LiteLLM agents);
  no `AGENT_NAME`; prompts at `vdagent_<name>/prompts/{system,compact}.md`.
- §7.2/§7.3: note they describe the LiteLLM agents; the transport contract is this spec §3–§4.
- §12: local-run line and compose paragraph per §9.
- §13 *Agent runtime*: point to this spec §10.

## 12. Acceptance

- `uv sync` succeeds; `uv run pytest` passes (backend + all six agent packages).
- No reference to `vdagent_agents`, `vdagent-agents`, or `AGENT_NAME` remains outside git history.
- `uv run python -m agent_template` serves; with a config entry pointing at it, a human message
  returns `echo: …` in the UI.
- `docker compose up --build` starts all services healthy; the main spec §13 E2E smoke passes
  (Orchestrator → Data → Compare → Insight → Report, saved report with a chart).
