"""Candidate Engine entry point (steps 3–5 part 1–2): gate → T1/T2/T3/T5/T7 → priority cut."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from ..artifacts import FixtureArtifactReader, content_hash
from ..candidates import build_context, generate_candidates
from ..contracts import InputArtifact, InsightTaskRequest
from .builders import cause, context, dataset, diagnostic, dq, inventory, semantic, unit

FIXTURES = Path(__file__).parent / "fixtures"
AS_OF = datetime.fromisoformat("2026-07-01T00:00:00+07:00")


async def load(case: str) -> tuple[InsightTaskRequest, list[InputArtifact]]:
    folder = FIXTURES / case
    request = InsightTaskRequest.model_validate_json((folder / "request.json").read_text(encoding="utf-8"))
    reader = FixtureArtifactReader(folder / "artifacts")
    return request, [await reader.read(ref.artifact_id) for ref in request.input_artifact_refs]


async def test_tc01_yields_the_target_unit_causes_in_rank_order() -> None:
    request, artifacts = await load("tc01")
    batch = generate_candidates(build_context(request, artifacts, semantic(), AS_OF), max_candidates=40)
    assert [c.candidate_id for c in batch.candidates] == [
        "C-T1-U00231-1-OVERPRICED_VS_PEER",
        "C-T1-U00231-2-LOW_SALES_INCENTIVE",
    ]
    c = batch.candidates[0]
    assert c.slots["dom"].metric_ref.startswith("ART-DATASET-TC01#/fact_unit_inventory_snapshot/")
    assert c.confidence == "HIGH" and batch.rejected == []


async def test_the_engine_is_deterministic() -> None:
    request, artifacts = await load("tc01")
    first = generate_candidates(build_context(request, artifacts, semantic(), AS_OF), max_candidates=40)
    request, artifacts = await load("tc01")
    second = generate_candidates(build_context(request, artifacts, semantic(), AS_OF), max_candidates=40)
    assert content_hash(first.candidates) == content_hash(second.candidates)
    assert content_hash(first.rejected) == content_hash(second.rejected)


def test_budget_cut_rejects_the_lowest_priorities_but_keeps_t7() -> None:
    units = [unit(i) for i in (1, 2, 3)]
    data = dataset(
        units,
        [inventory(i, 120) for i in (1, 2, 3)],
        [diagnostic(i, 120) for i in (1, 2, 3)],
        [cause(i, "OVERPRICED_VS_PEER") for i in (1, 2, 3)],
    )
    ctx = context(data, dq(data_as_of="2026-06-27T23:00:00+07:00"))
    everything = generate_candidates(ctx, max_candidates=100)
    assert [c.task for c in everything.candidates] == ["T1", "T1", "T1", "T7"]
    cut = generate_candidates(ctx, max_candidates=2)
    assert [c.candidate_id for c in cut.candidates] == ["C-T1-U001-1-OVERPRICED_VS_PEER", "C-T7-STALE_SNAPSHOT"]
    assert [(r.candidate_id, r.reason_code) for r in cut.rejected] == [
        ("C-T1-U002-1-OVERPRICED_VS_PEER", "CONTEXT_BUDGET"),
        ("C-T1-U003-1-OVERPRICED_VS_PEER", "CONTEXT_BUDGET"),
    ]


async def test_memory_subjects_raise_their_priority() -> None:
    request, artifacts = await load("tc01")
    plain = generate_candidates(build_context(request, artifacts, semantic(), AS_OF), max_candidates=40)
    boosted = generate_candidates(
        build_context(
            request, artifacts, semantic(), AS_OF, recent_subject_ids=frozenset({"U00231"}), recent_subject_boost=Decimal(1)
        ),
        max_candidates=40,
    )
    assert boosted.candidates[0].priority == plain.candidates[0].priority + 1


async def test_a_missing_dq_artifact_is_refused() -> None:
    request, artifacts = await load("tc01")
    without_dq = [a for a in artifacts if a.artifact_type != "dq"]
    with pytest.raises(ValueError, match="E02"):
        build_context(request, without_dq, semantic(), AS_OF)
