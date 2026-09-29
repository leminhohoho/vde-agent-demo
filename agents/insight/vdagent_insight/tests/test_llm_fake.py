"""`LlmClient` interface (spec §7.1) and the scripted `FakeLlmClient` used by every pipeline test."""

from __future__ import annotations

from decimal import Decimal

import pytest

from ..contracts import CallType, LlmInsightDraft, LlmUsage
from ..llm import FakeLlmClient, FakeReply, LlmClient, LlmSchemaError, LlmTransientError

DRAFT = {
    "selected": [{"candidate_ids": ["C1"], "template": "Căn {{unit}} tồn {{dom}}.", "slot_map": {"dom": "C1.dom"}}],
    "skipped": [],
}


def usage(call_type: CallType = "MAIN", cost: str | None = "0.001") -> LlmUsage:
    return LlmUsage(
        provider="gemini",
        model_id="gemini-3.5-flash-lite",
        call_type=call_type,
        input_tokens=100,
        cached_input_tokens=0,
        output_tokens=20,
        thinking_tokens=0,
        cost_usd=None if cost is None else Decimal(cost),
        latency_ms=5,
        finish_reason="STOP",
    )


async def call(client: LlmClient, call_type: CallType = "MAIN") -> tuple[object, LlmUsage]:
    return await client.generate_structured(
        system="sys",
        user="<data>…</data>",
        schema=LlmInsightDraft,
        call_type=call_type,
        reasoning="off",
    )


async def test_fake_returns_the_scripted_output_validated_against_the_schema_and_records_the_call() -> None:
    fake = FakeLlmClient([FakeReply(DRAFT, usage())])
    output, used = await call(fake)
    assert isinstance(output, LlmInsightDraft)
    assert output.selected[0].slot_map == {"dom": "C1.dom"}
    assert used.cost_usd == Decimal("0.001")
    (recorded,) = fake.calls
    assert (recorded.system, recorded.user, recorded.schema, recorded.call_type, recorded.reasoning) == (
        "sys",
        "<data>…</data>",
        LlmInsightDraft,
        "MAIN",
        "off",
    )


async def test_output_outside_the_schema_is_a_schema_error_that_still_carries_its_usage() -> None:
    fake = FakeLlmClient([FakeReply({"selected": [{"template": "x"}]}, usage(cost="0.002"))])
    with pytest.raises(LlmSchemaError) as info:
        await call(fake)
    assert info.value.usage is not None and info.value.usage.cost_usd == Decimal("0.002")


async def test_scripted_exceptions_are_raised_in_order() -> None:
    fake = FakeLlmClient([LlmTransientError("503"), FakeReply(DRAFT, usage("REPAIR"))])
    with pytest.raises(LlmTransientError):
        await call(fake)
    _, used = await call(fake, "REPAIR")
    assert used.call_type == "REPAIR"
    assert [c.call_type for c in fake.calls] == ["MAIN", "REPAIR"]


async def test_a_reply_without_usage_gets_a_zero_cost_usage_of_the_call_type() -> None:
    fake = FakeLlmClient([FakeReply(DRAFT)])
    _, used = await call(fake, "REPAIR")
    assert used.call_type == "REPAIR" and used.cost_usd == Decimal("0")


async def test_running_out_of_script_fails_loudly() -> None:
    fake = FakeLlmClient([])
    with pytest.raises(AssertionError, match="no scripted"):
        await call(fake)
