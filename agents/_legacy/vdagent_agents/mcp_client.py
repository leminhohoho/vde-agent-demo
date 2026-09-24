"""MCP access for the agent loop (spec §7.2, §4.7).

`McpSession` is the narrow surface the loop needs; `open_mcp_session` provides it over the official
SDK's streamable-HTTP client with the invocation's bearer token. `run_mcp_tool` applies the runtime
rules shared by every session: 30 s timeout, tool errors as `error: …` text, 16 000-char truncation.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from mcp import types as mcp_types
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

MCP_TOOL_TIMEOUT_S = 30.0
MAX_TOOL_RESULT_CHARS = 16_000
TRUNCATION_MARKER = "…[truncated]"


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolOutcome:
    text: str
    is_error: bool = False


class McpSession(Protocol):
    async def list_tools(self) -> list[McpTool]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome: ...


McpSessionFactory = Callable[[str, str], AbstractAsyncContextManager[McpSession]]
"""`(mcp_url, mcp_token) -> async context manager yielding an open session`."""


def openai_tool_schema(tool: McpTool) -> dict[str, Any]:
    parameters = tool.input_schema or {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {"name": tool.name, "description": tool.description, "parameters": parameters},
    }


def truncate_tool_text(text: str) -> str:
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    return text[:MAX_TOOL_RESULT_CHARS] + TRUNCATION_MARKER


def _as_error(text: str) -> str:
    text = text.strip() or "tool failed"
    return text if text.startswith("error:") else f"error: {text}"


async def run_mcp_tool(session: McpSession, name: str, arguments: dict[str, Any]) -> str:
    """Execute one MCP tool call; never raises for tool-level failures (they go back to the model)."""
    try:
        async with asyncio.timeout(MCP_TOOL_TIMEOUT_S):
            outcome = await session.call_tool(name, arguments)
    except TimeoutError:
        return f"error: tool '{name}' timed out after {MCP_TOOL_TIMEOUT_S:g}s"
    except Exception as exc:  # transport or protocol failure of this one call
        return truncate_tool_text(_as_error(f"tool '{name}' failed: {exc}"))
    text = _as_error(outcome.text) if outcome.is_error else outcome.text
    return truncate_tool_text(text)


def _render_content(result: mcp_types.CallToolResult) -> str:
    parts: list[str] = []
    for block in result.content:
        if isinstance(block, mcp_types.TextContent):
            parts.append(block.text)
        else:
            parts.append(f"[{block.type} content omitted]")
    if not any(p.strip() for p in parts) and result.structured_content is not None:
        return json.dumps(result.structured_content, ensure_ascii=False)
    return "\n".join(parts)


class _SdkSession:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def list_tools(self) -> list[McpTool]:
        tools: list[McpTool] = []
        cursor: str | None = None
        while True:
            page = await self._client.list_tools(cursor=cursor)
            tools.extend(
                McpTool(name=t.name, description=t.description or t.title or "", input_schema=dict(t.input_schema))
                for t in page.tools
            )
            cursor = page.next_cursor
            if not cursor:
                return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        result = await self._client.call_tool(name, arguments, read_timeout_seconds=MCP_TOOL_TIMEOUT_S)
        return ToolOutcome(text=_render_content(result), is_error=bool(result.is_error))


@asynccontextmanager
async def open_mcp_session(url: str, token: str) -> AsyncIterator[McpSession]:
    """Streamable-HTTP MCP session authenticated with `Authorization: Bearer <token>`."""
    async with create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}) as http_client:
        transport = streamable_http_client(url, http_client=http_client)
        async with Client(transport, cache=None) as client:
            yield _SdkSession(client)
