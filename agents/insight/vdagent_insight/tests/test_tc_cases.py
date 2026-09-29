"""Spec §11 test cases for phase P1 (gate + candidates), on the fixed mock snapshots in fixtures/.

TC-01→TC-07 and TC-21→TC-23. What P1 can decide is checked here (candidates, numbers, evidence,
flags, confidence, significant); KEY/status/narrative belong to later phases.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from ..artifacts import FixtureArtifactReader, content_hash
from ..candidates import CandidateBatch, CandidateContext, build_context, generate_candidates
from ..candidates.t2_distribution import cause_distribution
from ..contracts import InputArtifact, InsightCandidate, InsightTaskRequest
from ..settings import CONFIG_DIR, SemanticConfig, SemanticConfigRegistry

FIXTURES = Path(__file__).parent / "fixtures"
AS_OF = datetime.fromisoformat("2026-07-01T00:00:00+07:00")
CASES = ["tc01", "tc02", "tc03", "tc04", "tc05", "tc06", "tc07", "tc21", "tc22", "tc23"]
MAX_CANDIDATES = 40


async def load(case: str) -> tuple[InsightTaskRequest, list[InputArtifact], SemanticConfig]:
    folder = FIXTURES / case
    request = InsightTaskRequest.model_validate_json((folder / "request.json").read_text(encoding="utf-8"))
    reader = FixtureArtifactReader(folder / "artifacts")
    artifacts = [await reader.read(ref.artifact_id) for ref in request.input_artifact_refs]
    config_dir = folder / "config" if (folder / "config").is_dir() else CONFIG_DIR
    return request, artifacts, SemanticConfigRegistry(config_dir).get(request.semantic_config_version)


async def run(case: str) -> tuple[CandidateContext, CandidateBatch]:
    request, artifacts, cfg = await load(case)
    ctx = build_context(request, artifacts, cfg, AS_OF)
    return ctx, generate_candidates(ctx, MAX_CANDIDATES)


def by_id(batch: CandidateBatch) -> dict[str, InsightCandidate]:
    return {c.candidate_id: c for c in batch.candidates}


def root_causes(batch: CandidateBatch) -> list[InsightCandidate]:
    return [c for c in batch.candidates if c.insight_type == "ROOT_CAUSE_SIGNAL"]


def resolve(ref: str, artifacts: list[InputArtifact]) -> Any:
    artifact_id, _, pointer = ref.partition("#")
    (artifact,) = [a for a in artifacts if a.artifact_id == artifact_id]
    node: Any = artifact.payload
    for part in pointer.strip("/").split("/"):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


# ---- every case --------------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
async def test_fixture_hashes_match_their_request(case: str) -> None:
    request, artifacts, _ = await load(case)
    for ref, artifact in zip(request.input_artifact_refs, artifacts, strict=True):
        assert artifact.content_hash == ref.content_hash
        assert (artifact.snapshot_id, artifact.semantic_config_version) == (request.snapshot_id, request.semantic_config_version)


@pytest.mark.parametrize("case", CASES)
async def test_every_bound_number_is_the_artifact_value(case: str) -> None:
    """Numeric accuracy (§11.1): a binding that points into an input artifact carries exactly its value."""
    request, artifacts, cfg = await load(case)
    batch = generate_candidates(build_context(request, artifacts, cfg, AS_OF), MAX_CANDIDATES)
    ids = {a.artifact_id for a in artifacts}
    for c in batch.candidates:
        for slot in c.slots.values():
            if slot.metric_ref.split("#")[0] in ids:
                assert Decimal(str(resolve(slot.metric_ref, artifacts))) == slot.value, (c.candidate_id, slot.slot)
        for ref in c.evidence_refs:
            if ref.split("#")[0] in ids:
                resolve(ref, artifacts)


@pytest.mark.parametrize("case", CASES)
async def test_every_case_is_deterministic(case: str) -> None:
    _, first = await run(case)
    _, second = await run(case)
    assert content_hash([first.candidates, first.rejected]) == content_hash([second.candidates, second.rejected])


# ---- TC-01 → TC-07 -----------------------------------------------------------------------------


async def test_tc01_happy_path() -> None:
    _, batch = await run("tc01")
    (c,) = root_causes(batch)
    assert (c.cause_code, c.subject.label) == ("OVERPRICED_VS_PEER", "A-05.03")
    assert (c.slots["dom"].value, c.slots["spread"].value) == (Decimal(145), Decimal("12.40"))
    assert c.evidence_refs and c.action_code == "TARGETED_PRICE_CORRECTION" and c.confidence == "HIGH"


async def test_tc02_three_causes_in_rank_order() -> None:
    ctx, batch = await run("tc02")
    found = root_causes(batch)
    assert [(c.severity_rank, c.cause_code) for c in found] == [
        (1, "OVERPRICED_VS_PEER"),
        (2, "LOW_SALES_INCENTIVE"),
        (3, "DEEP_FUNNEL_DROP_OFF"),
    ]
    (unit,) = ctx.view.units_in_scope(ctx.request.analysis_scope)
    assert unit.diagnostic is not None and found[0].cause_code == unit.diagnostic.primary_cause_code
    assert sum(c.attribution_score or 0 for c in found) == Decimal("1.000")
    assert all("CONFLICT" not in c.dq_flags for c in found)


async def test_tc03_zone_distribution_by_both_methods() -> None:
    ctx, batch = await run("tc03")
    shares = cause_distribution(ctx, ctx.gate.scope("ZONE", "ZN-AQUA-01"))
    assert sum(s.weighted_share_pct for s in shares) == Decimal(100)
    # every T2 candidate has the same priority (spec 6.4), so rank them by their weighted share
    distribution = sorted(
        (c for c in batch.candidates if c.insight_type == "CAUSE_DISTRIBUTION"),
        key=lambda c: -c.slots["weighted_share"].value,
    )
    assert [(c.cause_code, c.slots["weighted_share"].value) for c in distribution] == [
        ("OVERPRICED_VS_PEER", Decimal(65)),
        ("LOW_SALES_INCENTIVE", Decimal(25)),
        ("DEEP_FUNNEL_DROP_OFF", Decimal(10)),
    ]
    for c in distribution:
        assert {"weighted_share", "unit_share", "units_with_cause", "units_diagnosed"} <= set(c.slots)
    assert distribution[0].slots["overdue_units"].value == 40


async def test_tc04_legal_barrier_at_project_level_first() -> None:
    _, batch = await run("tc04")
    first = batch.candidates[0]
    assert (first.cause_code, first.level, first.subject.id) == ("LEGAL_PERMIT_BARRIER", "PROJECT", "PRJ-X")
    assert first.action_code == "EXPEDITE_LEGAL_PROCEDURES" and first.slots["overdue_units"].value == 3
    assert [c for c in root_causes(batch) if c.level == "UNIT"] == []


async def test_tc05_no_diagnosis_at_the_boundary_or_for_sold_and_booked_units() -> None:
    _, batch = await run("tc05")
    assert root_causes(batch) == []


async def test_tc06_threshold_follows_the_semantic_config_version() -> None:
    request, artifacts, cfg = await load("tc06")
    assert cfg.version == request.semantic_config_version == "sem-tc06-60"
    assert cfg.params.overdue_threshold_days == 60
    (c,) = root_causes(generate_candidates(build_context(request, artifacts, cfg, AS_OF), MAX_CANDIDATES))
    assert c.slots["dom"].value == 75
    shipped = SemanticConfigRegistry(CONFIG_DIR).get(SemanticConfigRegistry(CONFIG_DIR).versions()[0])
    assert root_causes(generate_candidates(build_context(request, artifacts, shipped, AS_OF), MAX_CANDIDATES)) == []


async def test_tc07_no_overdue_unit_is_an_answer_not_a_failure() -> None:
    _, batch = await run("tc07")
    assert root_causes(batch) == []
    (c,) = batch.candidates
    assert (c.candidate_id, c.insight_type, c.slots["units_in_scope"].value) == ("C-T7-NO_OVERDUE_UNITS", "DATA_LIMITATION", 20)


# ---- TC-21 → TC-23 -----------------------------------------------------------------------------


async def test_tc21_partial_coverage_states_28_of_40_and_downgrades() -> None:
    _, batch = await run("tc21")
    found = by_id(batch)
    c = found["C-T2-ZN-AQUA-01-OVERPRICED_VS_PEER"]
    assert (c.slots["units_diagnosed"].display, c.slots["overdue_units"].display) == ("28 căn", "40 căn")
    assert c.coverage == Decimal(70) and c.confidence == "MEDIUM" and "PARTIAL_COVERAGE" in c.dq_flags
    assert found["C-T7-PARTIAL_COVERAGE-ZN-AQUA-01"].dq_flags == ["PARTIAL_COVERAGE"]


async def test_tc22_overlapping_groups_are_not_significant() -> None:
    _, batch = await run("tc22")
    found = by_id(batch)
    for group in ("W", "E"):
        c = found[f"C-T3-balcony_orientation-{group}"]
        assert c.slots["group_units"].value == 12 and c.significant is False


async def test_tc23_excluded_field_and_missing_not_at_random() -> None:
    _, batch = await run("tc23")
    found = by_id(batch)
    (c,) = root_causes(batch)
    assert c.cause_code == "LOW_SALES_INCENTIVE" and "spiff" not in c.slots
    limitation = found["C-T7-DQ-fact_unit_inventory_snapshot.spiff_bonus_vnd"]
    assert limitation.dq_flags == ["FIELD_EXCLUDED", "MISSING_NOT_RANDOM"]
    assert (limitation.slots["missing_rate"].value, limitation.slots["mnar_gap"].value) == (Decimal(45), Decimal(25))
