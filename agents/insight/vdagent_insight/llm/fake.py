"""`FakeLlmClient`: a scripted `LlmClient` for tests (TC-12→15, 18, 22, 27, 30, 31 use it).

Each call consumes the next scripted entry: a `FakeReply` (its output validated against the
requested schema, like a real adapter; failures become `LlmSchemaError`) or an exception to raise.
Every call is recorded in `calls`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ValidationError

from ..contracts import CallType, LlmUsage
from .base import LlmSchemaError, Reasoning


@dataclass(frozen=True)
class FakeReply:
    output: BaseModel | dict[str, Any]
    usage: LlmUsage | None = None
    """None → a zero-cost usage of the call's type."""


@dataclass(frozen=True)
class RecordedCall:
    system: str
    user: str
    schema: type[BaseModel]
    call_type: CallType
    reasoning: Reasoning


def zero_usage(call_type: CallType) -> LlmUsage:
    return LlmUsage(
        provider="gemini",
        model_id="fake",
        call_type=call_type,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        thinking_tokens=0,
        cost_usd=Decimal("0"),
        latency_ms=0,
        finish_reason="STOP",
    )


class FakeLlmClient:
    def __init__(self, script: Iterable[FakeReply | BaseException]) -> None:
        self._script = list(script)
        self.calls: list[RecordedCall] = []

    async def generate_structured(
        self, *, system: str, user: str, schema: type[BaseModel], call_type: CallType, reasoning: Reasoning
    ) -> tuple[BaseModel, LlmUsage]:
        self.calls.append(RecordedCall(system, user, schema, call_type, reasoning))
        if not self._script:
            raise AssertionError(f"FakeLlmClient: no scripted reply left for call {len(self.calls)} ({call_type})")
        entry = self._script.pop(0)
        if isinstance(entry, BaseException):
            raise entry
        usage = entry.usage or zero_usage(call_type)
        output = entry.output
        data = output.model_dump(mode="json") if isinstance(output, BaseModel) else output
        try:
            return schema.model_validate(data), usage
        except ValidationError as exc:
            raise LlmSchemaError(f"output does not match {schema.__name__}: {exc}", usage) from exc
