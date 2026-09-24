"""This agent's brain. EDIT THIS FILE (and add any modules it needs).

Implement `Agent` (see `contract.py` for the types and rules R1–R9) with any framework, and return
it from `build_agent()`. The host calls `build_agent()` once at startup, after loading `.env`:
read configuration and construct clients there, and raise `AgentConfigError` for bad settings.

This stub echoes the inbound message and keeps a naive summary; it needs no LLM.
"""

from __future__ import annotations

from .contract import Agent, InvocationContext, Message

NAME = "echo"
"""The name this agent connects to the Backend as: an `agents:` entry in `backend/config.yaml`.
Also used in logs and startup errors."""

SUMMARY_MAX_CHARS = 2000


class EchoAgent:
    async def invoke(self, ctx: InvocationContext) -> None:
        await ctx.emit_assistant(f"echo: {ctx.history[-1]['content']}")

    async def compact(self, previous_summary: str, messages: list[Message]) -> str:
        lines = [previous_summary] if previous_summary else []
        lines += [m["content"] for m in messages if m["role"] == "user"]
        return "\n".join(lines)[-SUMMARY_MAX_CHARS:]


def build_agent() -> Agent:
    return EchoAgent()
