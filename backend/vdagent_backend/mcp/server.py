"""MCP server at `/mcp` (§6): streamable HTTP via the official SDK's low-level `Server`.

The low-level `Server` lets `tools/list` and `tools/call` see the per-request caller identity, so
the tool list is filtered per agent. The SDK session manager runs stateless: every HTTP request is
self-contained and authenticated on its own, so a revoked token stops working immediately.

Usage (FastAPI app lifespan):

    mcp = create_mcp(cfg, db, tokens)
    mcp.install(app)
    async with mcp.lifespan():
        yield
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import mcp_types as types
from fastapi import FastAPI
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from vdagent_backend.config import Config
from vdagent_backend.mcp.auth import BearerAuth, current_identity
from vdagent_backend.mcp.sql import SQL_TIMEOUT_S
from vdagent_backend.mcp.tools import McpTools, tool_error
from vdagent_backend.tokens import TokenRegistry

MCP_PATHS = ("/mcp", "/mcp/")


class McpServer:
    def __init__(self, tools: McpTools, tokens: TokenRegistry) -> None:
        self._tools = tools
        self._server: Server[Any] = Server(
            "vdagent",
            version="0.1.0",
            instructions="vdagent warehouse and artifact tools.",
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
        )
        self._manager: StreamableHTTPSessionManager | None = None
        self._endpoint = BearerAuth(tokens, self._handle)

    async def _list_tools(
        self, ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        identity = current_identity.get()
        return types.ListToolsResult(tools=self._tools.list_for(identity.agent) if identity else [])

    async def _call_tool(self, ctx: ServerRequestContext[Any], params: types.CallToolRequestParams) -> types.CallToolResult:
        identity = current_identity.get()
        if identity is None:
            return tool_error("unauthenticated")
        return await self._tools.call(identity, params.name, params.arguments or {})

    async def _handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        manager = self._manager
        if manager is None:
            response = JSONResponse(
                {"error": {"code": "mcp_unavailable", "message": "MCP server is not running"}}, status_code=503
            )
            await response(scope, receive, send)
            return
        await manager.handle_request(scope, receive, send)

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Run the SDK session manager; the FastAPI app lifespan MUST enter this."""
        # A session manager runs once; a fresh one per lifespan keeps this re-enterable.
        manager = StreamableHTTPSessionManager(app=self._server, stateless=True)
        async with manager.run():
            self._manager = manager
            try:
                yield
            finally:
                self._manager = None

    def install(self, app: FastAPI) -> None:
        """Route exactly `/mcp` and `/mcp/` (no slash redirect) ahead of any catch-all route."""
        for path in MCP_PATHS:
            app.router.routes.insert(0, Route(path, endpoint=self._endpoint))


def create_mcp(
    cfg: Config, db: AsyncEngine, tokens: TokenRegistry, *, sql_timeout_s: float = SQL_TIMEOUT_S
) -> McpServer:
    return McpServer(McpTools(db, cfg.warehouse_db, sql_timeout_s=sql_timeout_s), tokens)
