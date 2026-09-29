"""Primary provider adapter (pipeline step 6 and [LLM-R]): Gemini through google-genai.

Parameters from config/llm.yaml: model `primary.model_id`, `thinking_level` MINIMAL for reasoning
"off" and LOW for the repair (luật 6), no temperature, `max_output_tokens`, a `timeout_ms` per call,
JSON output against the lowered schema (D-34), automatic function calling off (no tools).

Error mapping (D-35): timeout, 429, 5xx and network errors → `LlmTransientError` (E08); any other
API error (400, …), a finish reason other than STOP (MAX_TOKENS, SAFETY, …) or an answer that does not
validate → `LlmSchemaError` (E09) carrying the usage and the raw text.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from ..contracts import CallType, LlmUsage
from ..settings import LlmConfig
from .base import LlmSchemaError, LlmTransientError, Reasoning
from .schema import provider_schema
from .usage import from_gemini

THINKING = {"off": types.ThinkingLevel.MINIMAL, "low": types.ThinkingLevel.LOW}
STOP = types.FinishReason.STOP


class GeminiClient:
    def __init__(self, sdk: Any, cfg: LlmConfig) -> None:
        """`sdk`: a `google.genai.Client` (or a stand-in exposing `aio.models`)."""
        self._sdk = sdk
        self._cfg = cfg
        self.model_id = cfg.primary.model_id

    @classmethod
    def from_key(cls, api_key: str, cfg: LlmConfig) -> GeminiClient:
        return cls(genai.Client(api_key=api_key), cfg)

    def _config(self, system: str, schema: type[BaseModel], reasoning: Reasoning) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=provider_schema(schema),
            thinking_config=types.ThinkingConfig(thinking_level=THINKING[reasoning]),
            max_output_tokens=self._cfg.limits.max_output_tokens,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    async def generate_structured(
        self, *, system: str, user: str, schema: type[BaseModel], call_type: CallType, reasoning: Reasoning
    ) -> tuple[BaseModel, LlmUsage]:
        config = self._config(system, schema, reasoning)
        start = time.perf_counter()
        try:
            async with asyncio.timeout(self._cfg.limits.timeout_ms / 1000):
                resp = await self._sdk.aio.models.generate_content(model=self.model_id, contents=user, config=config)
        except TimeoutError as exc:
            raise LlmTransientError(f"gemini: timeout after {self._cfg.limits.timeout_ms} ms") from exc
        except errors.ServerError as exc:
            raise LlmTransientError(f"gemini: {exc.code} {exc.message}") from exc
        except errors.APIError as exc:
            if exc.code == 429:
                raise LlmTransientError(f"gemini: 429 {exc.message}") from exc
            raise LlmSchemaError(f"gemini: {exc.code} {exc.message}") from exc
        except (httpx.TransportError, ConnectionError) as exc:
            raise LlmTransientError(f"gemini: network error {exc}") from exc
        latency = int((time.perf_counter() - start) * 1000)
        finish = resp.candidates[0].finish_reason if resp.candidates else None
        finish_name = getattr(finish, "value", None) or str(finish)
        usage = from_gemini(resp.usage_metadata, self.model_id, call_type, latency, finish_name, self._cfg)
        text = resp.text
        if finish != STOP or not text:
            raise LlmSchemaError(f"gemini: finish reason {finish_name}", usage, text)
        try:
            return schema.model_validate_json(text), usage
        except ValidationError as exc:
            raise LlmSchemaError(
                f"gemini: answer does not match {schema.__name__}: {exc.error_count()} errors", usage, text
            ) from exc

    async def count_tokens(self, *, system: str, user: str) -> int:
        resp = await self._sdk.aio.models.count_tokens(model=self.model_id, contents=f"{system}\n\n{user}")
        return int(resp.total_tokens or 0)
