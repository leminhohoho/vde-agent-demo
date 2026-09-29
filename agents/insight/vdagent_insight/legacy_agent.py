"""This agent's brain: a LangChain 1.x agent (`create_agent`) with the Backend's MCP tools and its
own long-term memory.

Per turn: open an MCP session, build the tools (MCP + `send_to_agent`) and a fresh agent with two
middlewares: `MemoryMiddleware` (memory.py) recalls earlier findings into the system prompt and
saves new ones after the answer; `CtxBridge` (bridge.py) maps LangChain's loop onto the turn
contract. LangChain runs the loop; the tool calls of one step run concurrently.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import openai
from langchain.agents import create_agent
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import SecretStr
from vdagent_sdk import Agent, AgentTimeoutError, InvocationContext, Message

from .bridge import STEP_LIMIT_TEXT, CtxBridge
from .mcp_client import McpSessionFactory, open_mcp_session
from .memory import MemoryMiddleware
from .settings import DEFAULT_LLM_TIMEOUT_S, load_settings
from .tools import build_tools

__all__ = ["DESCRIPTION", "NAME", "STEP_LIMIT_TEXT", "LangChainAgent", "build_agent"]

NAME = "insight"
DESCRIPTION = "Explains trends, anomalies and drivers."

PROMPTS_DIR = Path(__file__).parent / "prompts"
SUMMARY_HEADING = "## Summary of earlier work with this user"
COMPACT_TOOL_TEXT_CHARS = 2_000


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def build_system_prompt(prompt: str, summary: str) -> str:
    if not summary.strip():
        return prompt
    return f"{prompt}\n\n{SUMMARY_HEADING}\n{summary.strip()}"


def render_for_compaction(previous_summary: str, messages: Sequence[Message]) -> str:
    lines = ["Previous summary:", previous_summary.strip() or "(none)", "", "Messages to fold in:"]
    for msg in messages:
        if msg["role"] == "user":
            lines.append(f"[inbound] {msg['content']}")
        elif msg["role"] == "assistant":
            if msg.get("content"):
                lines.append(f"[you] {msg['content']}")
            for tc in msg.get("tool_calls") or []:
                lines.append(f"[you called {tc['function']['name']}] {tc['function']['arguments']}")
        elif msg["role"] == "tool":
            content = msg["content"]
            if len(content) > COMPACT_TOOL_TEXT_CHARS:
                content = content[:COMPACT_TOOL_TEXT_CHARS] + "…[truncated]"
            lines.append(f"[tool result] {content}")
    return "\n".join(lines)


class LangChainAgent:
    def __init__(
        self,
        *,
        model: BaseChatModel,
        embeddings: Embeddings,
        mcp_session_factory: McpSessionFactory = open_mcp_session,
        system_prompt: str,
        compact_prompt: str,
        extract_prompt: str,
        timeout_s: float = DEFAULT_LLM_TIMEOUT_S,
    ) -> None:
        self._model = model
        self._embeddings = embeddings
        self._extract_prompt = extract_prompt
        self._mcp_session_factory = mcp_session_factory
        self._system_prompt = system_prompt
        self._compact_prompt = compact_prompt
        self._timeout_s = timeout_s

    async def invoke(self, ctx: InvocationContext) -> None:
        async with self._mcp_session_factory(ctx.mcp.url, ctx.mcp.token) as mcp:
            agent = create_agent(
                self._model,
                build_tools(mcp, await mcp.list_tools(), ctx.peers),
                system_prompt=build_system_prompt(self._system_prompt, ctx.summary),
                middleware=[
                    MemoryMiddleware(ctx, self._model, self._embeddings, self._extract_prompt),
                    CtxBridge(ctx, self._timeout_s),
                ],
            )
            # Each step passes through a few graph nodes; the step budget itself is CtxBridge's.
            messages: list[AnyMessage | dict[str, Any]] = [*ctx.history]  # OpenAI-shaped dicts
            await agent.ainvoke({"messages": messages}, {"recursion_limit": 4 * ctx.max_steps + 10})

    async def compact(self, previous_summary: str, messages: list[Message]) -> str:
        prompt = [
            SystemMessage(self._compact_prompt),
            HumanMessage(render_for_compaction(previous_summary, messages)),
        ]
        try:
            async with asyncio.timeout(self._timeout_s):
                reply = await self._model.ainvoke(prompt)
        except (TimeoutError, openai.APITimeoutError) as exc:
            raise AgentTimeoutError(f"model call timed out after {self._timeout_s:g}s") from exc
        return reply.text.strip()


def build_agent(env: Mapping[str, str]) -> Agent:
    """Raises `PluginConfigError` naming the missing or invalid setting."""
    settings = load_settings(env)
    for noisy in ("httpx", "openai"):  # per-request INFO lines drown out agent logs
        logging.getLogger(noisy).setLevel(logging.WARNING)
    model = ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.openai_base_url,
        api_key=SecretStr(settings.openai_api_key),
        timeout=settings.llm_timeout_s,
    )
    embeddings = OpenAIEmbeddings(
        model=settings.embed_model,
        base_url=settings.openai_base_url,
        api_key=SecretStr(settings.openai_api_key),
        timeout=settings.llm_timeout_s,
        check_embedding_ctx_length=False,  # send text, not tiktoken ids: the endpoint is not OpenAI's
    )
    return LangChainAgent(
        model=model,
        embeddings=embeddings,
        system_prompt=load_prompt("system"),
        compact_prompt=load_prompt("compact"),
        extract_prompt=load_prompt("extract"),
        timeout_s=settings.llm_timeout_s,
    )
