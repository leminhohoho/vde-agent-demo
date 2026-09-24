"""The agent turn (spec §7.2) and compaction (§7.3), independent of the gRPC transport.

The loop is stateless: everything it knows comes from `InvokeStart`. Frames go out through `emit`;
`send_to_agent` calls go through `call_agent`, which the transport resolves with the backend's
`AgentCallResult`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from vdagent_proto import agent_pb2

from vdagent_agents.llm import AssistantMessage, LLMClient, ToolCall
from vdagent_agents.mcp_client import McpSession, openai_tool_schema, run_mcp_tool

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SEND_TO_AGENT = "send_to_agent"
DEFAULT_MAX_STEPS = 12
SUMMARY_HEADING = "## Summary of earlier work with this user"
STEP_LIMIT_TEXT = "[step limit reached before I could finish; no further tool calls were made]"
COMPACT_TOOL_TEXT_CHARS = 2_000

Emit = Callable[[agent_pb2.AgentFrame], Awaitable[None]]
CallAgent = Callable[[str, str, str], Awaitable[str]]
"""`(tool_call_id, target, message) -> reply content` (already `error: …` when the call failed)."""


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def build_system_prompt(prompt: str, summary: str) -> str:
    if not summary.strip():
        return prompt
    return f"{prompt}\n\n{SUMMARY_HEADING}\n{summary.strip()}"


def history_to_openai(history: Sequence[agent_pb2.Message]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for msg in history:
        if msg.role == agent_pb2.USER:
            messages.append({"role": "user", "content": msg.content})
        elif msg.role == agent_pb2.ASSISTANT:
            calls = [ToolCall(id=tc.id, name=tc.name, arguments_json=tc.arguments_json) for tc in msg.tool_calls]
            messages.append(AssistantMessage(content=msg.content, tool_calls=calls).to_openai())
        elif msg.role == agent_pb2.TOOL:
            messages.append({"role": "tool", "tool_call_id": msg.tool_call_id, "content": msg.content})
        else:
            logger.warning("skipping history message with unspecified role")
    return messages


def send_to_agent_tool(peers: Sequence[agent_pb2.Peer]) -> dict[str, Any]:
    roster = "\n".join(f"- {p.name}: {p.description}" for p in peers)
    return {
        "type": "function",
        "function": {
            "name": SEND_TO_AGENT,
            "description": (
                "Send a message to another agent and wait for its reply. "
                f"The reply is returned as this tool's result. Agents:\n{roster}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "enum": [p.name for p in peers]},
                    "message": {
                        "type": "string",
                        "description": "Self-contained request, including any dataset ids it needs.",
                    },
                },
                "required": ["agent", "message"],
            },
        },
    }


def _assistant_frame(reply: AssistantMessage) -> agent_pb2.AgentFrame:
    return agent_pb2.AgentFrame(
        message=agent_pb2.Message(
            role=agent_pb2.ASSISTANT,
            content=reply.content,
            tool_calls=[
                agent_pb2.ToolCall(id=tc.id, name=tc.name, arguments_json=tc.arguments_json)
                for tc in reply.tool_calls
            ],
        )
    )


def _tool_frame(tool_call_id: str, content: str) -> agent_pb2.AgentFrame:
    return agent_pb2.AgentFrame(
        message=agent_pb2.Message(role=agent_pb2.TOOL, content=content, tool_call_id=tool_call_id)
    )


class Invocation:
    """One `Invoke` turn."""

    def __init__(
        self,
        start: agent_pb2.InvokeStart,
        *,
        system_prompt: str,
        llm: LLMClient,
        mcp: McpSession,
        emit: Emit,
        call_agent: CallAgent,
    ) -> None:
        self._start = start
        self._system_prompt = system_prompt
        self._llm = llm
        self._mcp = mcp
        self._emit = emit
        self._call_agent = call_agent
        self._mcp_tool_names: set[str] = set()

    async def run(self) -> None:
        start = self._start
        tools: list[dict[str, Any]] = []
        for tool in await self._mcp.list_tools():
            if tool.name == SEND_TO_AGENT:
                continue
            self._mcp_tool_names.add(tool.name)
            tools.append(openai_tool_schema(tool))
        if start.peers:
            tools.append(send_to_agent_tool(start.peers))

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(self._system_prompt, start.summary)},
            *history_to_openai(start.history),
        ]
        max_steps = start.max_steps if start.max_steps > 0 else DEFAULT_MAX_STEPS

        for step in range(1, max_steps + 1):
            last_step = step == max_steps
            reply = await self._llm.complete(messages, tools, "none" if last_step else "auto")
            if last_step and reply.tool_calls:
                # The model ignored tool_choice="none"; there is no step left to run the calls.
                reply = AssistantMessage(content=reply.content or STEP_LIMIT_TEXT)
            await self._emit(_assistant_frame(reply))
            messages.append(reply.to_openai())
            if not reply.tool_calls:
                await self._emit(agent_pb2.AgentFrame(final=agent_pb2.Final(content=reply.content)))
                return
            async with asyncio.TaskGroup() as tg:
                tasks = [tg.create_task(self._run_tool_call(tc)) for tc in reply.tool_calls]
            for tc, task in zip(reply.tool_calls, tasks, strict=True):
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": task.result()})

    async def _run_tool_call(self, tc: ToolCall) -> str:
        content = await self._tool_content(tc)
        await self._emit(_tool_frame(tc.id, content))
        return content

    async def _tool_content(self, tc: ToolCall) -> str:
        try:
            arguments = json.loads(tc.arguments_json) if tc.arguments_json.strip() else {}
        except json.JSONDecodeError as exc:
            return f"error: invalid JSON arguments for '{tc.name}': {exc}"
        if not isinstance(arguments, dict):
            return f"error: arguments for '{tc.name}' must be a JSON object"

        if tc.name == SEND_TO_AGENT and self._start.peers:
            target, message = arguments.get("agent"), arguments.get("message")
            if not isinstance(target, str) or not target.strip():
                return "error: send_to_agent requires 'agent' (the name of the agent to call)"
            if not isinstance(message, str) or not message.strip():
                return "error: send_to_agent requires a non-empty 'message'"
            return await self._call_agent(tc.id, target.strip(), message)
        if tc.name in self._mcp_tool_names:
            return await run_mcp_tool(self._mcp, tc.name, arguments)
        return f"error: unknown tool '{tc.name}'"


def render_for_compaction(previous_summary: str, messages: Sequence[agent_pb2.Message]) -> str:
    lines = ["Previous summary:", previous_summary.strip() or "(none)", "", "Messages to fold in:"]
    for msg in messages:
        if msg.role == agent_pb2.USER:
            lines.append(f"[inbound] {msg.content}")
        elif msg.role == agent_pb2.ASSISTANT:
            if msg.content:
                lines.append(f"[you] {msg.content}")
            for tc in msg.tool_calls:
                lines.append(f"[you called {tc.name}] {tc.arguments_json}")
        elif msg.role == agent_pb2.TOOL:
            content = msg.content
            if len(content) > COMPACT_TOOL_TEXT_CHARS:
                content = content[:COMPACT_TOOL_TEXT_CHARS] + "…[truncated]"
            lines.append(f"[tool result] {content}")
    return "\n".join(lines)


async def compact(
    llm: LLMClient, prompt: str, previous_summary: str, messages: Sequence[agent_pb2.Message]
) -> str:
    reply = await llm.complete(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": render_for_compaction(previous_summary, messages)},
        ],
        [],
        "none",
    )
    return reply.content.strip()
