"""`LlmClient`: the one interface the pipeline calls a model through (spec §7.1; steps 6 and [LLM-R]).

Adapters call the provider SDKs directly (`google-genai`, `openai` Responses; phase P3): no
LangChain, no tool calling. One call = one structured output validated against `schema`, plus its
normalised `LlmUsage`. `async` because the Backend's event loop must never block (sdk R10;
docs/OPEN_QUESTIONS.md Q5).

Errors carry the usage of the failed call when there was one, so the task cost counts it (TC-27).
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel

from ..contracts import CallType, LlmUsage

Reasoning = Literal["off", "low"]
"""`off` for [LLM-1] and MEMORY jobs; `low` only for [LLM-R] (luật 6)."""


class LlmError(Exception):
    def __init__(self, message: str, usage: LlmUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


class LlmTransientError(LlmError):
    """E08: timeout, 429, 5xx: retry once, then the fallback provider, then TEMPLATE."""


class LlmSchemaError(LlmError):
    """E09: output not parseable into the schema (truncated by the token limit included)."""


class LlmClient(Protocol):
    async def generate_structured(
        self, *, system: str, user: str, schema: type[BaseModel], call_type: CallType, reasoning: Reasoning
    ) -> tuple[BaseModel, LlmUsage]: ...
