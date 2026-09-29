"""Mock snapshot for TC-01 (spec §11.2): 1 project, 20 units, 1 overdue unit A-05.03 (DOM 145,
OVERPRICED_VS_PEER rank 1, score 1.000). Column names follow DW Schema v3.1.0.

TODO(data-agent-contract): the payload shapes of `metric`, `dq`, `dataset` are ours until the Data
Agent spec defines them.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from ..artifacts import FixtureArtifactReader
from ..contracts import DatasetPayload, DqPayload, InputArtifact, InsightTaskRequest, MetricPayload
from ..settings import CONFIG_DIR, load_semantic_config

TC01 = Path(__file__).parent / "fixtures" / "tc01"


def tc01_request() -> InsightTaskRequest:
    return InsightTaskRequest.model_validate_json((TC01 / "request.json").read_text(encoding="utf-8"))


async def tc01_artifact(artifact_type: str) -> InputArtifact:
    reader = FixtureArtifactReader(TC01 / "artifacts")
    (ref,) = [r for r in tc01_request().input_artifact_refs if r.artifact_type == artifact_type]
    return await reader.read(ref.artifact_id)


async def test_every_ref_resolves_with_its_hash_snapshot_and_config_version() -> None:
    req = tc01_request()
    reader = FixtureArtifactReader(TC01 / "artifacts")
    assert req.semantic_config_version == load_semantic_config(CONFIG_DIR / "semantic_insight.yaml").version
    types = set()
    for ref in req.input_artifact_refs:
        art = await reader.read(ref.artifact_id)
        assert art.content_hash == ref.content_hash, ref.artifact_id
        assert (art.artifact_type, art.version, art.status) == (ref.artifact_type, ref.version, ref.status)
        assert (art.snapshot_id, art.semantic_config_version) == (req.snapshot_id, req.semantic_config_version)
        types.add(art.artifact_type)
    assert types == {"metric", "dq", "dataset"}


async def test_dataset_has_one_project_twenty_units_and_exactly_one_overdue_unit() -> None:
    dataset = DatasetPayload.model_validate((await tc01_artifact("dataset")).payload)
    threshold = load_semantic_config(CONFIG_DIR / "semantic_insight.yaml").params.overdue_threshold_days
    assert len(dataset.dim_project_profile) == 1
    assert len(dataset.dim_unit_master) == len(dataset.fact_unit_inventory_snapshot) == 20
    for row in dataset.fact_unit_inventory_snapshot:
        assert row.is_overdue_flag == (row.inventory_status == "AVAILABLE" and row.unsold_days_dom > threshold)
    (overdue,) = [r for r in dataset.fact_unit_inventory_snapshot if r.is_overdue_flag]
    (unit,) = [u for u in dataset.dim_unit_master if u.unit_key == overdue.unit_key]
    assert (unit.unit_id, unit.unit_code, overdue.unsold_days_dom) == ("U011", "A-05.03", 145)
    statuses = {r.inventory_status for r in dataset.fact_unit_inventory_snapshot}
    assert statuses == {"AVAILABLE", "BOOKED", "SOLD"}


async def test_the_overdue_unit_is_diagnosed_overpriced_vs_peer_only() -> None:
    dataset = DatasetPayload.model_validate((await tc01_artifact("dataset")).payload)
    (diag,) = dataset.dm_unit_friction_diagnostics
    assert (diag.unit_code, diag.unsold_days_dom, diag.primary_cause_code) == ("A-05.03", 145, "OVERPRICED_VS_PEER")
    assert diag.recommended_action == "TARGETED_PRICE_CORRECTION"
    assert diag.price_spread_vs_peer_pct == Decimal("12.40")
    assert diag.physical_defect_penalty == 5 and diag.is_peer_sample_constrained is False and diag.peer_count >= 10
    (cause,) = dataset.unit_diagnostic_causes
    assert (cause.diagnostic_id, cause.cause_code, cause.severity_rank) == (diag.diagnostic_id, "OVERPRICED_VS_PEER", 1)
    assert cause.attribution_score == Decimal("1.000")


async def test_dq_passes_and_metrics_back_the_claim_numbers() -> None:
    dq = DqPayload.model_validate((await tc01_artifact("dq")).payload)
    assert dq.overall_status == "PASS" and all(f.status == "PASS" for f in dq.fields)
    metrics = MetricPayload.model_validate((await tc01_artifact("metric")).payload)
    dom = [m for m in metrics.metrics if m.metric_id == "unsold_days_dom" and m.subject.id == "U011"]
    assert [m.value for m in dom] == [Decimal("145")]
