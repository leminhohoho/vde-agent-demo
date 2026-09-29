"""Contracts (spec §3.3, §4.2, §6.5, §7.5, §9.5): Pydantic v2, extra="forbid", field/enum names as in the spec."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from ..contracts import (
    ArtifactEnvelope,
    Insight,
    InsightCandidate,
    InsightPayload,
    InsightRef,
    InsightTaskRequest,
    LlmInsightDraft,
    LlmUsage,
    MemoryContext,
    NumericBinding,
)

HASH = "a" * 64
RUN_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
TASK_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


def request_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "run_id": RUN_ID,
        "task_id": TASK_ID,
        "attempt": 1,
        "fencing_token": 1,
        "intent": "SLOW_MOVING_INVESTIGATION",
        "tasks": ["T1", "T6", "T7"],
        "question_normalized": "Vì sao căn A-05.03 bán chậm?",
        "analysis_scope": {"level": "UNIT", "unit_ids": ["U011"]},
        "snapshot_id": "SNAP-20260630-01",
        "semantic_config_version": "sem-2026-09-29",
        "user_context": {
            "user_id": "u_1",
            "role": "SALES_OPS",
            "authorized_scope": {"project_ids": ["PRJ-X"], "zone_ids": ["ZN-AQUA-01"]},
        },
        "input_artifact_refs": [
            {"artifact_id": "ART-M-1", "artifact_type": "metric", "version": 1, "status": "VALID", "content_hash": HASH}
        ],
    }
    data.update(overrides)
    return data


def binding(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "slot": "spread",
        "metric_ref": "ART-D-1#/dm_unit_friction_diagnostics/0/price_spread_vs_peer_pct",
        "value": "12.40",
        "unit": "PCT",
        "display": "12,4%",
    }
    data.update(overrides)
    return data


def insight_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "insight_id": "INS-001",
        "candidate_id": "C-T1-U011-OVERPRICED_VS_PEER",
        "insight_type": "ROOT_CAUSE_SIGNAL",
        "level": "UNIT",
        "subject": {"type": "unit", "id": "U011", "label": "A-05.03"},
        "cause_code": "OVERPRICED_VS_PEER",
        "claim": {
            "template": "Căn {{unit}} tồn {{dom}}; đơn giá/m² cao hơn trung vị peer {{spread}}.",
            "rendered_text": "Căn A-05.03 tồn 145 ngày; đơn giá/m² cao hơn trung vị peer 12,4%.",
            "numeric_bindings": [binding()],
        },
        "evidence_refs": [
            {"evidence_id": "EV-1", "artifact_id": "ART-D-1", "path": "/dm_unit_friction_diagnostics/0", "kind": "DIAGNOSTIC_ROW"}
        ],
        "lineage": {
            "source_refs": ["dm_unit_friction_diagnostics@SNAP-20260630-01"],
            "calculation_refs": ["price_spread_vs_peer_pct@dw"],
        },
        "severity_rank": 1,
        "attribution_score": "1.000",
        "confidence": {"level": "HIGH", "reasons": ["DQ_PASS"]},
        "materiality": "KEY",
        "eligible_for_conclusion": True,
        "recommendation": {
            "action_code": "TARGETED_PRICE_CORRECTION",
            "text": "Đề xuất xem xét điều chỉnh đơn giá niêm yết.",
            "is_suggestion": True,
            "requires_human_approval": True,
        },
        "limitations": [],
    }
    data.update(overrides)
    return data


# ---- InsightTaskRequest (§3.3) ----------------------------------------------------------------


def test_request_applies_the_spec_defaults() -> None:
    req = InsightTaskRequest.model_validate(request_data())
    assert req.analysis_scope.project_ids == [] and req.analysis_scope.zone_ids == []
    assert req.constraints.language == "vi"
    assert req.constraints.deadline_ms == 60000
    assert req.constraints.max_key_insights is None
    assert req.conversation_id is None and req.parent_insight_ref is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"unexpected": 1},
        {"run_id": "not-a-uuid"},
        {"tasks": []},
        {"tasks": ["T4"]},
        {"attempt": 0},
        {"intent": "FORECAST"},
        {"question_normalized": "x" * 1001},
        {"input_artifact_refs": []},
        {
            "input_artifact_refs": [
                {"artifact_id": "A", "artifact_type": "comparison", "version": 1, "status": "VALID", "content_hash": HASH}
            ]
        },
        {
            "input_artifact_refs": [
                {"artifact_id": "A", "artifact_type": "metric", "version": 1, "status": "INVALID", "content_hash": HASH}
            ]
        },
        {
            "input_artifact_refs": [
                {"artifact_id": "A", "artifact_type": "metric", "version": 1, "status": "VALID", "content_hash": "abc"}
            ]
        },
        {"constraints": {"language": "en"}},
        {"conversation_id": "conv-1"},
    ],
)
def test_request_rejects_input_outside_the_schema(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        InsightTaskRequest.model_validate(request_data(**overrides))


def test_request_parses_from_json_text() -> None:
    req = InsightTaskRequest.model_validate_json(json.dumps(request_data()))
    assert req.tasks == ["T1", "T6", "T7"]


# ---- NumericBinding: Decimal, never float -----------------------------------------------------


def test_numeric_binding_keeps_the_decimal_exactly_and_serialises_it_as_a_string() -> None:
    b = NumericBinding.model_validate(binding(value="12.40"))
    assert b.value == Decimal("12.40")
    assert b.model_dump(mode="json")["value"] == "12.40"


@pytest.mark.parametrize("value", [12.4, True, "twelve"])
def test_numeric_binding_rejects_floats_and_non_numbers(value: object) -> None:
    with pytest.raises(ValidationError):
        NumericBinding.model_validate(binding(value=value))


# ---- Insight / payload (§4.2) -----------------------------------------------------------------


def test_insight_example_round_trips_through_json() -> None:
    insight = Insight.model_validate(insight_data())
    again = Insight.model_validate_json(insight.model_dump_json())
    assert again == insight
    assert again.conflict_with == []
    assert again.attribution_score == Decimal("1.000")


@pytest.mark.parametrize("field", ["is_suggestion", "requires_human_approval"])
def test_recommendation_is_always_a_suggestion_needing_approval(field: str) -> None:
    data = insight_data()
    data["recommendation"][field] = False
    with pytest.raises(ValidationError):
        Insight.model_validate(data)


def test_payload_rejects_an_unknown_narrative_mode() -> None:
    payload = {
        "summary": {
            "headline_insight_ids": [],
            "coverage": {"units_in_scope": 20, "overdue_units": 1, "units_explained": 1},
            "narrative_mode": "FREEFORM",
        },
        "insights": [],
        "rejected_candidates": [],
        "chart_hints": [],
        "limitations": [],
    }
    with pytest.raises(ValidationError):
        InsightPayload.model_validate(payload)


def test_envelope_is_fixed_to_the_insight_v2_contract() -> None:
    fields = ArtifactEnvelope.model_fields
    assert fields["artifact_type"].default == "insight"
    assert fields["schema_version"].default == "insight.v2"


# ---- Internal schemas (§6.5) ------------------------------------------------------------------


def test_candidate_requires_decimal_priority_and_known_task() -> None:
    data: dict[str, Any] = {
        "candidate_id": "C1",
        "task": "T1",
        "insight_type": "ROOT_CAUSE_SIGNAL",
        "level": "UNIT",
        "subject": {"type": "unit", "id": "U011", "label": "A-05.03"},
        "slots": {"spread": binding()},
        "evidence_refs": ["EV-1"],
        "lineage": {"source_refs": [], "calculation_refs": []},
        "significant": False,
        "confidence": "HIGH",
        "dq_flags": [],
        "priority": "1.0",
    }
    assert InsightCandidate.model_validate(data).priority == Decimal("1.0")
    with pytest.raises(ValidationError):
        InsightCandidate.model_validate({**data, "priority": 1.0})
    with pytest.raises(ValidationError):
        InsightCandidate.model_validate({**data, "task": "T6"})


def draft_item(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"candidate_ids": ["C1"], "template": "Căn {{unit}} tồn {{dom}}.", "slot_map": {"dom": "C1.dom"}}
    data.update(overrides)
    return data


def test_llm_draft_enforces_its_limits() -> None:
    LlmInsightDraft.model_validate({"selected": [draft_item()], "skipped": []})
    for bad in (
        {"selected": [draft_item()] * 13, "skipped": []},
        {"selected": [draft_item(candidate_ids=[])], "skipped": []},
        {"selected": [draft_item(template="x" * 401)], "skipped": []},
        {"selected": [], "skipped": [{"candidate_id": "C2", "reason": "x" * 101}]},
        {"selected": [draft_item(cause_code="BAD_LOCATION")], "skipped": []},
    ):
        with pytest.raises(ValidationError):
            LlmInsightDraft.model_validate(bad)


def _types_in(schema: object) -> set[str]:
    found: set[str] = set()
    if isinstance(schema, dict):
        t = schema.get("type")
        if isinstance(t, str):
            found.add(t)
        elif isinstance(t, list):
            found.update(x for x in t if isinstance(x, str))
        for value in schema.values():
            found |= _types_in(value)
    elif isinstance(schema, list):
        for item in schema:
            found |= _types_in(item)
    return found


def test_llm_draft_schema_has_no_numeric_field_for_the_model_to_fill() -> None:
    types = _types_in(LlmInsightDraft.model_json_schema())
    assert "string" in types
    assert not types & {"number", "integer"}


def test_memory_context_empty_and_bounded() -> None:
    empty = MemoryContext.empty()
    assert empty.recent_refs == [] and empty.topic_summary == [] and empty.stale_refs_dropped == 0
    assert empty.user_pref.verbosity == "NORMAL" and empty.user_pref.show_recommendation is True
    ref = {
        "insight_id": "INS-1",
        "subject": {"type": "unit", "id": "U011", "label": "A-05.03"},
        "insight_type": "ROOT_CAUSE_SIGNAL",
        "level": "UNIT",
    }
    with pytest.raises(ValidationError):
        MemoryContext.model_validate({"recent_refs": [ref] * 21, "topic_summary": [], "user_pref": {}, "stale_refs_dropped": 0})


def test_insight_ref_carries_no_rendered_text_or_numbers() -> None:
    assert set(InsightRef.model_fields) == {
        "insight_id",
        "artifact_id",
        "subject",
        "insight_type",
        "cause_code",
        "level",
        "materiality",
    }


# ---- LlmUsage (§7.5) --------------------------------------------------------------------------


def usage_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "provider": "gemini",
        "model_id": "gemini-3.5-flash-lite",
        "call_type": "MAIN",
        "input_tokens": 10000,
        "cached_input_tokens": 3000,
        "output_tokens": 1200,
        "thinking_tokens": 800,
        "cost_usd": "0.00719",
        "latency_ms": 900,
        "finish_reason": "STOP",
    }
    data.update(overrides)
    return data


def test_llm_usage_cost_is_decimal_or_null_when_pricing_is_missing() -> None:
    assert LlmUsage.model_validate(usage_data()).cost_usd == Decimal("0.00719")
    assert LlmUsage.model_validate(usage_data(cost_usd=None)).cost_usd is None
    for bad in ({"cost_usd": 0.00719}, {"call_type": "EXTRA"}, {"provider": "anthropic"}):
        with pytest.raises(ValidationError):
            LlmUsage.model_validate(usage_data(**bad))
