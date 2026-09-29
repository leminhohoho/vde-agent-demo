"""Fixture for TC-01 (spec §11.2), built from the data pack sample: the 61 sampled units of
The Sapphire 1, target SAPPHIRE1-16.231 (overdue, OVERPRICED_VS_PEER rank 1 with 0.60 and
LOW_SALES_INCENTIVE rank 2 with 0.400, 12 unconstrained peers). Column names follow DW v3.1.0.

TODO(data-agent-contract): the payload shapes of `metric` and `dq` are ours until the Data Agent
spec defines them.
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
    assert req.semantic_config_version == load_semantic_config(CONFIG_DIR / "semantic_insight.yaml").version == "3.1.0"
    types = set()
    for ref in req.input_artifact_refs:
        art = await reader.read(ref.artifact_id)
        assert art.content_hash == ref.content_hash, ref.artifact_id
        assert (art.artifact_type, art.version, art.status) == (ref.artifact_type, ref.version, ref.status)
        assert (art.snapshot_id, art.semantic_config_version) == (req.snapshot_id, req.semantic_config_version)
        types.add(art.artifact_type)
    assert types == {"metric", "dq", "dataset"}


async def test_dataset_is_one_tower_with_the_target_unit_overdue() -> None:
    dataset = DatasetPayload.model_validate((await tc01_artifact("dataset")).payload)
    threshold = load_semantic_config(CONFIG_DIR / "semantic_insight.yaml").params.overdue_threshold_days
    assert [p.project_id for p in dataset.dim_project_profile] == ["PRJ-VHOP"]
    assert [z.zone_id for z in dataset.dim_zone_master] == ["ZN-SAPPHIRE1"]
    assert len(dataset.dim_unit_master) == len(dataset.fact_unit_inventory_snapshot) == 61
    for row in dataset.fact_unit_inventory_snapshot:
        assert row.is_overdue_flag == (row.inventory_status == "AVAILABLE" and row.unsold_days_dom > threshold)
    (unit,) = [u for u in dataset.dim_unit_master if u.unit_code == "SAPPHIRE1-16.231"]
    (inv,) = [r for r in dataset.fact_unit_inventory_snapshot if r.unit_key == unit.unit_key]
    assert (unit.unit_id, inv.inventory_status, inv.unsold_days_dom) == ("U00231", "AVAILABLE", 143)


async def test_the_target_unit_is_diagnosed_overpriced_first() -> None:
    dataset = DatasetPayload.model_validate((await tc01_artifact("dataset")).payload)
    (diag,) = [d for d in dataset.dm_unit_friction_diagnostics if d.unit_code == "SAPPHIRE1-16.231"]
    assert (diag.unsold_days_dom, diag.primary_cause_code, diag.recommended_action) == (
        143,
        "OVERPRICED_VS_PEER",
        "TARGETED_PRICE_CORRECTION",
    )
    assert diag.price_spread_vs_peer_pct == Decimal("19.82")
    assert (diag.peer_count, diag.is_peer_sample_constrained) == (12, False)
    causes = sorted(
        (c for c in dataset.unit_diagnostic_causes if c.diagnostic_id == diag.diagnostic_id), key=lambda c: c.severity_rank
    )
    assert [(c.cause_code, c.severity_rank, c.attribution_score) for c in causes] == [
        ("OVERPRICED_VS_PEER", 1, Decimal("0.60")),
        ("LOW_SALES_INCENTIVE", 2, Decimal("0.400")),
    ]


async def test_dq_passes_and_metrics_back_the_claim_numbers() -> None:
    dq = DqPayload.model_validate((await tc01_artifact("dq")).payload)
    assert dq.overall_status == "PASS" and all(f.status == "PASS" for f in dq.fields)
    metrics = MetricPayload.model_validate((await tc01_artifact("metric")).payload)
    dom = [m for m in metrics.metrics if m.metric_id == "unsold_days_dom" and m.subject.id == "U00231"]
    assert [m.value for m in dom] == [Decimal("143")]
