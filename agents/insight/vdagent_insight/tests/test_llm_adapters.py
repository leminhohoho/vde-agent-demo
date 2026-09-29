"""Provider adapters (step 6) against mocked SDK clients: parameters sent, usage, error mapping (D-35).

No network: the SDK client objects are stand-ins with the same call shape.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from ..contracts import CallType, LlmInsightDraft
from ..llm import LlmSchemaError, LlmTransientError, Reasoning
from ..llm.gemini_client import GeminiClient
from ..llm.openai_client import OpenAIClient
from .builders import llm

CFG = llm()
DRAFT = {
    "selected": [
        {
            "candidate_ids": ["C1"],
            "template": "Căn {{unit}}.",
            "slots": [{"slot": "unit", "ref": "C1.unit"}],
            "limitation_text": None,
            "recommendation_text": None,
        }
    ],
    "skipped": [],
}


# ---- Gemini -------------------------------------------------------------------------------------------


class FakeGemini:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._generate, count_tokens=self._count))

    async def _generate(self, **kw: Any) -> Any:
        self.calls.append(kw)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if self.outcome == "hang":
            await asyncio.sleep(10)
        return self.outcome

    async def _count(self, **kw: Any) -> Any:
        self.calls.append(kw)
        return SimpleNamespace(total_tokens=1234)


def gemini_response(text: str | None, finish: genai_types.FinishReason = genai_types.FinishReason.STOP) -> SimpleNamespace:
    meta = SimpleNamespace(
        prompt_token_count=900, cached_content_token_count=300, candidates_token_count=120, thoughts_token_count=0
    )
    return SimpleNamespace(text=text, usage_metadata=meta, candidates=[SimpleNamespace(finish_reason=finish)])


async def gemini_call(client: GeminiClient, reasoning: Reasoning = "off", call_type: CallType = "MAIN") -> Any:
    return await client.generate_structured(
        system="SYS", user="USER", schema=LlmInsightDraft, call_type=call_type, reasoning=reasoning
    )


async def test_gemini_sends_the_config_parameters_and_normalises_usage() -> None:
    fake = FakeGemini(gemini_response(json.dumps(DRAFT)))
    draft, usage = await gemini_call(GeminiClient(fake, CFG))
    assert isinstance(draft, LlmInsightDraft) and draft.selected[0].template == "Căn {{unit}}."
    (call,) = fake.calls
    config = call["config"]
    assert call["model"] == "gemini-3.5-flash-lite" and call["contents"] == "USER"
    assert config.system_instruction == "SYS" and config.response_mime_type == "application/json"
    assert config.response_json_schema["additionalProperties"] is False
    assert config.thinking_config.thinking_level == genai_types.ThinkingLevel.MINIMAL
    assert config.max_output_tokens == 2500 and config.automatic_function_calling.disable is True
    assert config.temperature is None
    assert (usage.provider, usage.call_type, usage.input_tokens, usage.cached_input_tokens, usage.finish_reason) == (
        "gemini",
        "MAIN",
        900,
        300,
        "STOP",
    )
    assert usage.cost_usd is not None


async def test_gemini_repair_thinks_at_low() -> None:
    fake = FakeGemini(gemini_response(json.dumps(DRAFT)))
    _, usage = await gemini_call(GeminiClient(fake, CFG), reasoning="low", call_type="REPAIR")
    assert fake.calls[0]["config"].thinking_config.thinking_level == genai_types.ThinkingLevel.LOW
    assert usage.call_type == "REPAIR"


@pytest.mark.parametrize(
    "outcome",
    [
        genai_errors.ServerError(503, {"error": {"message": "overloaded"}}),
        genai_errors.ClientError(429, {"error": {"message": "quota"}}),
        httpx.ConnectError("down"),
        "hang",
    ],
)
async def test_gemini_transient_failures_are_e08(outcome: Any) -> None:
    cfg = CFG.model_copy(update={"limits": CFG.limits.model_copy(update={"timeout_ms": 50})})
    with pytest.raises(LlmTransientError):
        await gemini_call(GeminiClient(FakeGemini(outcome), cfg))


@pytest.mark.parametrize(
    "outcome",
    [
        genai_errors.ClientError(400, {"error": {"message": "bad request"}}),
        gemini_response('{"selected": [', genai_types.FinishReason.MAX_TOKENS),
        gemini_response(None, genai_types.FinishReason.SAFETY),
        gemini_response('{"selected": "nope"}'),
    ],
)
async def test_gemini_bad_answers_are_e09_with_their_usage(outcome: Any) -> None:
    with pytest.raises(LlmSchemaError) as info:
        await gemini_call(GeminiClient(FakeGemini(outcome), CFG))
    if not isinstance(outcome, BaseException):
        assert info.value.usage is not None and info.value.usage.input_tokens == 900
        assert info.value.raw == outcome.text


async def test_gemini_counts_tokens_with_the_provider() -> None:
    fake = FakeGemini(None)
    assert await GeminiClient(fake, CFG).count_tokens(system="SYS", user="USER") == 1234


# ---- OpenAI -------------------------------------------------------------------------------------------


REQ = httpx.Request("POST", "https://api.openai.com/v1/responses")


class FakeOpenAI:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []
        self.responses = SimpleNamespace(create=self._create, input_tokens=SimpleNamespace(count=self._count))

    async def _create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def _count(self, **kw: Any) -> Any:
        self.calls.append(kw)
        return SimpleNamespace(input_tokens=777)


def openai_response(text: str, status: str = "completed", reason: str | None = None) -> SimpleNamespace:
    usage = SimpleNamespace(
        input_tokens=800,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens=150,
        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )
    details = SimpleNamespace(reason=reason) if reason else None
    return SimpleNamespace(output_text=text, usage=usage, status=status, incomplete_details=details, error=None)


async def openai_call(client: OpenAIClient, reasoning: Reasoning = "off", call_type: CallType = "MAIN") -> Any:
    return await client.generate_structured(
        system="SYS", user="USER", schema=LlmInsightDraft, call_type=call_type, reasoning=reasoning
    )


async def test_openai_sends_strict_json_schema_and_no_reasoning() -> None:
    fake = FakeOpenAI(openai_response(json.dumps(DRAFT)))
    draft, usage = await openai_call(OpenAIClient(fake, CFG))
    assert isinstance(draft, LlmInsightDraft)
    (call,) = fake.calls
    assert (call["model"], call["instructions"], call["input"], call["max_output_tokens"]) == ("gpt-6-luna", "SYS", "USER", 2500)
    fmt = call["text"]["format"]
    assert (fmt["type"], fmt["strict"], fmt["name"]) == ("json_schema", True, "LlmInsightDraft")
    assert call["reasoning"] == {"effort": "none"}
    assert (usage.provider, usage.output_tokens, usage.finish_reason) == ("openai", 150, "completed")


async def test_openai_repair_reasons_at_low() -> None:
    fake = FakeOpenAI(openai_response(json.dumps(DRAFT)))
    await openai_call(OpenAIClient(fake, CFG), reasoning="low", call_type="REPAIR")
    assert fake.calls[0]["reasoning"] == {"effort": "low"}


@pytest.mark.parametrize(
    "outcome",
    [
        openai.RateLimitError("rate", response=httpx.Response(429, request=REQ), body=None),
        openai.InternalServerError("down", response=httpx.Response(503, request=REQ), body=None),
        openai.APITimeoutError(request=REQ),
        openai.APIConnectionError(request=REQ),
    ],
)
async def test_openai_transient_failures_are_e08(outcome: Any) -> None:
    with pytest.raises(LlmTransientError):
        await openai_call(OpenAIClient(FakeOpenAI(outcome), CFG))


@pytest.mark.parametrize(
    "outcome",
    [
        openai.BadRequestError("bad", response=httpx.Response(400, request=REQ), body=None),
        openai_response('{"selected": [', status="incomplete", reason="max_output_tokens"),
        openai_response('{"selected": 3}'),
    ],
)
async def test_openai_bad_answers_are_e09(outcome: Any) -> None:
    with pytest.raises(LlmSchemaError) as info:
        await openai_call(OpenAIClient(FakeOpenAI(outcome), CFG))
    if not isinstance(outcome, BaseException):
        assert info.value.usage is not None and info.value.raw == outcome.output_text


async def test_openai_counts_tokens_with_the_provider() -> None:
    fake = FakeOpenAI(None)
    assert await OpenAIClient(fake, CFG).count_tokens(system="SYS", user="USER") == 777
    assert fake.calls[0] == {"model": "gpt-6-luna", "instructions": "SYS", "input": "USER"}
