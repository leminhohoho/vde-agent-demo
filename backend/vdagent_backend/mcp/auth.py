"""Bearer-token auth for `/mcp` (§6).

`Authorization: Bearer <mcp_token>` is resolved through the in-memory `TokenRegistry`; the caller
identity `(user_id, agent, invocation_id)` is exposed to tool handlers via `current_identity`.
The MCP SDK runs each handler in the context of the HTTP request that carried the message, so the
variable set here is visible there.
"""

from __future__ import annotations

from contextvars import ContextVar

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from vdagent_backend.tokens import McpIdentity, TokenRegistry

current_identity: ContextVar[McpIdentity | None] = ContextVar("vdagent_mcp_identity", default=None)


def _bearer_token(scope: Scope) -> str | None:
    for key, value in scope.get("headers", ()):
        if key == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            token = token.strip()
            return token if scheme.lower() == "bearer" and token else None
    return None


class BearerAuth:
    """ASGI wrapper: resolve the bearer token or answer HTTP 401 (unknown, revoked or missing)."""

    def __init__(self, tokens: TokenRegistry, app: ASGIApp) -> None:
        self._tokens = tokens
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = _bearer_token(scope)
        identity = self._tokens.resolve(token) if token else None
        if identity is None:
            response = JSONResponse(
                {"error": {"code": "invalid_token", "message": "missing, unknown or revoked MCP token"}},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
            await response(scope, receive, send)
            return
        reset = current_identity.set(identity)
        try:
            await self._app(scope, receive, send)
        finally:
            current_identity.reset(reset)
