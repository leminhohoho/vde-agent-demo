"""The LLM steps of a task (5 → 6 → 7 → [LLM-R] → 8): TC-18, TC-27, repair routing and warnings."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

import pytest

from ..candidates import CandidateContext, generate_candidates
from ..contracts import CallType, InsightCandidate, LlmUsage, MemoryContext
from ..llm import FakeLlmClient, FakeReply, LlmSchemaError, LlmTransientError
from ..llm.steps import LlmProviders, run_llm_steps
from ..llm.usage import task_cost
from .builders import cause, context, dataset, diagnostic, dq, inventory, llm, unit

LLM = llm()


def setting() -> tuple[CandidateContext, list[InsightCandidate]]:
    data = dataset(
        [unit(11, unit_code="SAPPHIRE1-16.231")], [inventory(11, 145)], [diagnostic(11, 145)], [cause(11, "OVERPRICED_VS_PEER")]
    )
    ctx = context(data, dq(data_as_of="2026-06-27T23:00:00+07:00"))  # stale → one T7 limitation too
    return ctx, generate_candidates(ctx, 40).candidates


def usage(call_type: CallType = "MAIN", cost: str = "0.001", thinking: int = 0) -> LlmUsage:
    return LlmUsage(
        provider="gemini", model_id="gemini-3.5-flash-lite", call_type=call_type, input_tokens=100, cached_input_tokens=0,
        output_tokens=20, thinking_tokens=thinking, cost_usd=Decimal(cost), latency_ms=5, finish_reason="STOP",
    )  # fmt: skip


def draft(cid: str, template: str = "Căn {{unit}} đã tồn {{dom}}; có khả năng liên quan tới {{cause_label}}.") -> dict[str, Any]:
    slots = [s for s in ("unit", "dom", "cause_label") if "{{" + s + "}}" in template]
    return {
        "selected": [{"candidate_ids": [cid], "template": template, "slots": [{"slot": s, "ref": f"{cid}.{s}"} for s in slots]}],
        "skipped": [],
    }


async def no_sleep(_: float) -> None:
    return None


async def steps(
    ctx: CandidateContext, cands: list[InsightCandidate], primary: FakeLlmClient, fallback: FakeLlmClient | None = None
) -> Any:
    return await run_llm_steps(ctx, cands, LlmProviders(primary, fallback), LLM, MemoryContext.empty(), sleep=no_sleep)


async def test_a_clean_answer_needs_one_call_and_no_repair() -> None:
    ctx, cands = setting()
    primary = FakeLlmClient([FakeReply(draft("c1"), usage())])
    result = await steps(ctx, cands, primary)
    assert (result.repaired, result.main_error, result.narration.narrative_mode) == (False, None, "LLM")
    assert result.narration.items[0].candidate_ids == (cands[0].candidate_id,)  # alias mapped back
    assert [c.call_type for c in primary.calls] == ["MAIN"] and primary.calls[0].reasoning == "off"
    assert "<data>" in primary.calls[0].user and primary.calls[0].system.startswith("<!-- prompt_version")
    assert [u.call_type for u in result.usages] == ["MAIN"]
    assert [i.source for i in result.narration.items] == ["LLM", "TEMPLATE"]  # the ignored T7 is kept


async def test_a_violation_is_repaired_once_with_the_coded_errors() -> None:
    ctx, cands = setting()
    cid = "c1"
    bad = draft(cid, "Căn {{unit}} cao hơn peer 20%, liên quan tới {{cause_label}}.")
    primary = FakeLlmClient([FakeReply(bad, usage()), FakeReply(draft(cid), usage("REPAIR"))])
    result = await steps(ctx, cands, primary)
    _, repair = primary.calls
    assert (repair.call_type, repair.reasoning) == ("REPAIR", "low")
    assert "E10" in repair.user and "20%" in repair.user and cid in repair.user
    assert result.repaired and result.narration.narrative_mode == "LLM"
    assert [u.call_type for u in result.usages] == ["MAIN", "REPAIR"]


async def test_tc27_truncated_json_is_repaired_then_templated_and_both_calls_cost() -> None:
    ctx, cands = setting()
    truncated = LlmSchemaError("finish MAX_TOKENS", usage("MAIN", "0.004"), raw='{"selected": [{"candidate_ids": ["C')
    again = LlmSchemaError("finish MAX_TOKENS", usage("REPAIR", "0.006"), raw='{"selected": [')
    primary = FakeLlmClient([truncated, again])
    result = await steps(ctx, cands, primary)
    assert result.main_error == "E09" and result.repaired is False
    assert "E09" in primary.calls[1].user and '{"selected": [{"candidate_ids": ["C' in primary.calls[1].user
    assert result.narration.narrative_mode == "TEMPLATE"
    assert task_cost(result.usages) == Decimal("0.010")


async def test_tc18_all_providers_down_means_template_without_repair() -> None:
    ctx, cands = setting()
    primary = FakeLlmClient([LlmTransientError("timeout"), LlmTransientError("timeout")])
    fallback = FakeLlmClient([LlmTransientError("503")])
    result = await steps(ctx, cands, primary, fallback)
    assert result.main_error == "E08" and result.repaired is False
    assert result.narration.narrative_mode == "TEMPLATE" and all(i.source == "TEMPLATE" for i in result.narration.items)
    assert (len(primary.calls), len(fallback.calls)) == (2, 1)


async def test_the_repair_goes_to_the_provider_that_answered() -> None:
    ctx, cands = setting()
    cid = "c1"
    primary = FakeLlmClient([LlmTransientError("x"), LlmTransientError("x")])
    bad = draft(cid, "Căn {{unit}} chắc chắn do {{cause_label}}.")
    fallback = FakeLlmClient([FakeReply(bad, usage()), FakeReply(draft(cid), usage("REPAIR"))])
    result = await steps(ctx, cands, primary, fallback)
    assert [c.call_type for c in fallback.calls] == ["MAIN", "REPAIR"] and len(primary.calls) == 2
    assert result.repaired


async def test_hidden_thinking_is_logged_as_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    ctx, cands = setting()
    primary = FakeLlmClient([FakeReply(draft("c1"), usage(thinking=800))])
    with caplog.at_level(logging.WARNING):
        result = await steps(ctx, cands, primary)
    assert result.warnings == ["HIDDEN_THINKING"]
    assert any("HIDDEN_THINKING" in r.getMessage() for r in caplog.records)


async def test_raw_outputs_are_kept_for_replay() -> None:
    ctx, cands = setting()
    d = draft("c1")
    result = await steps(ctx, cands, FakeLlmClient([FakeReply(d, usage())]))
    assert [json.loads(r) for r in result.raw_outputs] == [
        d | {"selected": [d["selected"][0] | {"limitation_text": None, "recommendation_text": None}]}
    ]
