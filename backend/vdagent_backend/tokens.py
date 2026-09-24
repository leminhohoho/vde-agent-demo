"""In-memory per-invocation MCP bearer tokens (§4.1 step 3, §6).

The engine issues a token when an invocation starts and revokes it when the invocation ends;
the MCP auth middleware resolves it to the caller identity.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class McpIdentity:
    user_id: str
    agent: str
    invocation_id: str


class TokenRegistry:
    def __init__(self) -> None:
        self._tokens: dict[str, McpIdentity] = {}

    def issue(self, user_id: str, agent: str, invocation_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens[token] = McpIdentity(user_id, agent, invocation_id)
        return token

    def resolve(self, token: str) -> McpIdentity | None:
        return self._tokens.get(token)

    def revoke(self, token: str | None) -> None:
        if token:
            self._tokens.pop(token, None)
