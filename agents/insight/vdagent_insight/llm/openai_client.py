"""Fallback provider adapter (pipeline step 6 and [LLM-R]): OpenAI Responses API.

Parameters from config/llm.yaml: model `fallback.model_id`, `reasoning.effort` = `fallback.reasoning_effort`
("none") for reasoning "off" and `repair.reasoning` ("low") for the repair, `max_output_tokens`,
`timeout_ms`, `text.format` = json_schema strict with the lowered schema (D-34). `OPENAI_BASE_URL`
is honoured when set (D-30: empty → api.openai.com).

Error mapping (D-35): timeout, connection errors, 429 and 5xx → `LlmTransientError` (E08); other
status errors (400, …), an incomplete answer (token limit, content filter) or an answer that does
not validate → `LlmSchemaError` (E09) carrying the usage and the raw text.
"""

from __future__ import annotations

import time
from typing import Any

import openai
from pydantic import BaseModel, ValidationError

from ..contracts import CallType, LlmUsage
from ..settings import LlmConfig
from .base import LlmSchemaError, LlmTransientError, Reasoning
from .schema import provider_schema
from .usage import from_openai


class OpenAIClient:
    def __init__(self, sdk: Any, cfg: LlmConfig) -> None:
        """`sdk`: an `openai.AsyncOpenAI` (or a stand-in exposing `responses`)."""
        self._sdk = sdk
        self._cfg = cfg
        self.model_id = cfg.fallback.model_id

    @classmethod
    def from_key(cls, api_key: str, cfg: LlmConfig, base_url: str | None = None) -> OpenAIClient:
        timeout = cfg.limits.timeout_ms / 1000
        return cls(openai.AsyncOpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout, max_retries=0), cfg)

    def _effort(self, reasoning: Reasoning) -> str:
        return self._cfg.fallback.reasoning_effort if reasoning == "off" else self._cfg.repair.reasoning

    async def generate_structured(
        self, *, system: str, user: str, schema: type[BaseModel], call_type: CallType, reasoning: Reasoning
    ) -> tuple[BaseModel, LlmUsage]:
        text_format = {"type": "json_schema", "name": schema.__name__, "schema": provider_schema(schema), "strict": True}
        start = time.perf_counter()
        try:
            resp = await self._sdk.responses.create(
                model=self.model_id,
                instructions=system,
                input=user,
                text={"format": text_format},
                reasoning={"effort": self._effort(reasoning)},
                max_output_tokens=self._cfg.limits.max_output_tokens,
            )
        except (openai.APITimeoutError, openai.APIConnectionError, TimeoutError) as exc:
            raise LlmTransientError(f"openai: {type(exc).__name__}") from exc
        except openai.APIStatusError as exc:
            if exc.status_code == 429 or exc.status_code >= 500:
                raise LlmTransientError(f"openai: {exc.status_code}") from exc
            raise LlmSchemaError(f"openai: {exc.status_code} {exc.message}") from exc
        latency = int((time.perf_counter() - start) * 1000)
        usage = from_openai(resp.usage, self.model_id, call_type, latency, str(resp.status), self._cfg)
        text = resp.output_text
        if resp.status != "completed" or resp.error:
            reason = getattr(resp.incomplete_details, "reason", None) or getattr(resp.error, "code", None)
            raise LlmSchemaError(f"openai: status {resp.status} ({reason})", usage, text)
        try:
            return schema.model_validate_json(text), usage
        except ValidationError as exc:
            raise LlmSchemaError(
                f"openai: answer does not match {schema.__name__}: {exc.error_count()} errors", usage, text
            ) from exc

    async def count_tokens(self, *, system: str, user: str) -> int:
        resp = await self._sdk.responses.input_tokens.count(model=self.model_id, instructions=system, input=user)
        return int(resp.input_tokens)
