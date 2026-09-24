"""LLM access: the `LLMClient` protocol and its LiteLLM implementation."""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import litellm

from .contract import ToolCall

ToolChoice = Literal["auto", "none"]


class LLMTimeoutError(Exception):
    """The LLM call exceeded its deadline; the agent reports it as `AgentTimeoutError`."""


@dataclass(frozen=True)
class AssistantMessage:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)

    def to_openai(self) -> dict[str, Any]:
        """OpenAI chat-completions shape, suitable for appending to the conversation."""
        if not self.tool_calls:
            return {"role": "assistant", "content": self.content}
        return {
            "role": "assistant",
            "content": self.content or None,
            "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments_json}}
                for tc in self.tool_calls
            ],
        }


class LLMClient(Protocol):
    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], tool_choice: ToolChoice
    ) -> AssistantMessage:
        """One chat completion. Raises `LLMTimeoutError` on timeout; anything else is a hard failure."""
        ...


class LiteLLMClient:
    """`litellm.acompletion` against an OpenAI-compatible endpoint; credentials passed explicitly."""

    def __init__(self, *, model: str, api_base: str, api_key: str, timeout_s: float) -> None:
        self._model = f"openai/{model}"
        self._api_base = api_base
        self._api_key = api_key
        self._timeout_s = timeout_s

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], tool_choice: ToolChoice
    ) -> AssistantMessage:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "api_base": self._api_base,
            "api_key": self._api_key,
            "timeout": self._timeout_s,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        try:
            # The outer deadline also bounds LiteLLM's internal retries.
            async with asyncio.timeout(self._timeout_s):
                response = await litellm.acompletion(**kwargs)
        except (TimeoutError, litellm.Timeout) as exc:
            raise LLMTimeoutError(f"LLM call timed out after {self._timeout_s:g}s") from exc
        return _parse_response(response)


def _parse_response(response: Any) -> AssistantMessage:
    message = response.choices[0].message
    tool_calls: list[ToolCall] = []
    for raw in message.tool_calls or []:
        arguments = raw.function.arguments
        if arguments is None:
            arguments = "{}"
        elif not isinstance(arguments, str):
            arguments = json.dumps(arguments)
        tool_calls.append(
            ToolCall(
                # Some OpenAI-compatible servers omit ids; the protocol needs unique ones.
                id=raw.id or f"call_{secrets.token_hex(8)}",
                name=raw.function.name or "",
                arguments_json=arguments,
            )
        )
    return AssistantMessage(content=message.content or "", tool_calls=tool_calls)
