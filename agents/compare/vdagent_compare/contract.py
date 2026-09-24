"""The agent contract: the types an agent implements and receives.

COPIED FROM `agents/_template/` — do not edit in an agent folder. Change the template and re-copy.

An agent is two layers:

- the **host** (`host.py`, this file, `__main__.py`): speaks gRPC to the Backend, identical in
  every agent;
- the **brain** (`agent.py` and whatever it uses): implements `Agent` with any framework.

A turn (one `invoke`) follows these rules. Rules marked (host) are checked in-process: breaking
one raises `ContractViolation` at the offending call and fails the turn.

- R1  No memory between turns: `ctx.summary` + `ctx.history` is the whole truth.
- R2  (host) Emit an assistant step (with its tool calls) before any result or `call_agent` for
      those calls. A new assistant step may only be emitted once every tool call of the previous
      one has a result. Tool-call ids are non-empty and unique within a step.
- R3  (host) Every tool call gets exactly one `emit_tool_result` — including `send_to_agent`:
      `call_agent`, then emit the reply as its result.
- R4  (host) `call_agent` only for an unresolved `send_to_agent` call of the latest assistant
      step, at most once per id; no `emit_tool_result` for that id while its call is pending.
- R5  (host) The turn ends when `invoke` returns. By then every tool call is resolved and the
      last emitted assistant step has no tool calls; its content is the final answer. Nothing may
      be emitted after `invoke` returns.
- R6  Tool failures become result content `error: …` and the turn continues. LLM timeout →
      raise `AgentTimeoutError`. Any other exception fails the turn.
- R7  Make at most `ctx.max_steps` LLM calls.
- R8  One agent object serves concurrent turns: keep per-turn state off `self`.
- R9  Never swallow `asyncio.CancelledError`; a cancelled task arrives as cancellation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

SEND_TO_AGENT = "send_to_agent"
"""The Backend only accepts agent calls made from a tool call with exactly this name."""

Message = dict[str, Any]
"""One history message in OpenAI chat-completions shape:

- `{"role": "user", "content": str}` — inbound, rendered as `[from: <sender>] <text>`
- `{"role": "assistant", "content": str | None, "tool_calls": [{"id": str, "type": "function",
  "function": {"name": str, "arguments": str}}]}` — `tool_calls` present only when non-empty;
  `content` is `None` when empty and tool calls are present
- `{"role": "tool", "tool_call_id": str, "content": str}`
"""


@dataclass(frozen=True)
class ToolCall:
    """A tool call requested by an assistant step. `arguments_json` is the raw JSON object text."""

    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class Peer:
    """Another agent this one may call through `send_to_agent`."""

    name: str
    description: str


@dataclass(frozen=True)
class McpEndpoint:
    """The Backend's MCP server for this turn.

    Connect over streamable HTTP with header `Authorization: Bearer <token>`. The token is valid
    only while the turn runs; the Backend decides which tools this agent sees.
    """

    url: str
    token: str


class InvocationContext(Protocol):
    """Everything one turn knows, plus the only ways to report progress. Built by the host."""

    invocation_id: str
    task_id: str
    user_id: str
    summary: str
    """Rolling summary of this user's earlier, compacted tasks; `""` if none."""
    history: list[Message]
    """Uncompacted messages in order; the last one is the inbound message that started the turn."""
    peers: list[Peer]
    """Every other agent."""
    mcp: McpEndpoint
    max_steps: int
    """Budget of LLM calls for this turn (R7)."""

    async def emit_assistant(self, content: str, tool_calls: Sequence[ToolCall] = ()) -> None:
        """Record one assistant step. A step without tool calls that ends the turn is the answer."""
        ...

    async def emit_tool_result(self, tool_call_id: str, content: str) -> None:
        """Record the result of one tool call of the latest assistant step."""
        ...

    async def call_agent(self, tool_call_id: str, target: str, message: str) -> str:
        """Send `message` to agent `target` for `send_to_agent` call `tool_call_id`; wait for its reply.

        Returns the peer's reply text, or `error: …` text when the Backend rejects the call or the
        peer fails. Raises only if the Backend ends the stream while waiting — let that propagate.
        The reply is not recorded automatically: emit it with `emit_tool_result`.
        """
        ...


class Agent(Protocol):
    """What `agent.py` implements. One instance serves every turn of the process (R8)."""

    async def invoke(self, ctx: InvocationContext) -> None:
        """Run one turn, reporting every step through `ctx` (R2–R5)."""
        ...

    async def compact(self, previous_summary: str, messages: list[Message]) -> str:
        """Fold `messages` from finished tasks into `previous_summary`; return the new summary."""
        ...


class AgentConfigError(Exception):
    """Raise from `build_agent()` for bad or missing configuration; the process exits 2 with the message."""


class AgentTimeoutError(Exception):
    """Raise from `invoke`/`compact` when the model call times out; reported as DEADLINE_EXCEEDED."""


class ContractViolation(Exception):
    """Raised by the host when a turn breaks rules R2–R5; the turn fails."""
