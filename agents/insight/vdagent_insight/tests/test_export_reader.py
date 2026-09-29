"""ExportArtifactReader (D-77): the data pack CSVs as metric / dq / dataset / market_context artifacts."""

from __future__ import annotations

import pytest

from ..artifacts import ArtifactNotFound, content_hash
from ..contracts import DatasetPayload, DqPayload, MarketContextPayload, MetricPayload
from ..export_reader import ExportArtifactReader
from .builders import scope, semantic
from .conftest import DATAPACK_DIR, EXPORT_SAMPLE_DIR


def reader() -> ExportArtifactReader:
    return ExportArtifactReader(EXPORT_SAMPLE_DIR, semantic())


async def test_the_manifest_and_catalogue_come_from_the_pack() -> None:
    r = reader()
    manifest = await r.manifest()
    assert (manifest.snapshot_id, manifest.semantic_version, manifest.snapshot_date) == (
        "SNAP-20260630-01",
        "3.1.0",
        "2026-06-30",
    )
    catalog = await r.catalog()
    assert {p.project_id for p in catalog.projects} == {"PRJ-VHOP", "PRJ-VHOP-BEVERLY"}
    assert any(z.zone_id == "ZN-SAPPHIRE1" and z.zone_name == "The Sapphire 1" for z in catalog.zones)
    assert catalog.unit_by_code["SAPPHIRE1-16.231"].unit_id == "U00231"


async def test_a_unit_scope_gives_four_hashed_artifacts_of_that_unit() -> None:
    r = reader()
    arts = await r.prepare(scope("UNIT", unit_ids=["U00231"]))
    assert [a.artifact_type for a in arts] == ["metric", "dq", "dataset", "market_context"]
    for a in arts:
        assert (a.snapshot_id, a.semantic_config_version, a.status, a.version) == ("SNAP-20260630-01", "3.1.0", "VALID", 1)
        assert a.content_hash == content_hash(a.payload)
        assert await r.read(a.artifact_id) == a
    dataset = DatasetPayload.model_validate(arts[2].payload)
    assert [u.unit_code for u in dataset.dim_unit_master] == ["SAPPHIRE1-16.231"]
    assert [d.primary_cause_code for d in dataset.dm_unit_friction_diagnostics] == ["OVERPRICED_VS_PEER"]
    assert [p.project_id for p in dataset.dim_project_profile] == ["PRJ-VHOP"]
    assert sorted(c.cause_code for c in dataset.unit_diagnostic_causes) == ["LOW_SALES_INCENTIVE", "OVERPRICED_VS_PEER"]


async def test_the_same_scope_gives_the_same_ids_and_hashes() -> None:
    a = await reader().prepare(scope("ZONE", zone_ids=["ZN-SAPPHIRE1"]))
    b = await reader().prepare(scope("ZONE", zone_ids=["ZN-SAPPHIRE1"]))
    assert [(x.artifact_id, x.content_hash) for x in a] == [(y.artifact_id, y.content_hash) for y in b]


async def test_zone_scope_dq_metric_and_market() -> None:
    arts = {a.artifact_type: a for a in await reader().prepare(scope("ZONE", zone_ids=["ZN-SAPPHIRE1"]))}
    dataset = DatasetPayload.model_validate(arts["dataset"].payload)
    assert len(dataset.dim_unit_master) == 61 and {z.zone_id for z in dataset.dim_zone_master} == {"ZN-SAPPHIRE1"}
    dq = DqPayload.model_validate(arts["dq"].payload)
    assert dq.data_as_of == "2026-06-30T23:59:00+07:00"
    fields = {f"{f.table}.{f.field}": f for f in dq.fields}
    assert fields["fact_unit_inventory_snapshot.unsold_days_dom"].is_primary
    spread = fields["dm_unit_friction_diagnostics.price_spread_vs_peer_pct"]
    assert spread.missing_rate_pct == 0 and spread.status == "PASS"  # NULL only where no cause needs it
    metric = MetricPayload.model_validate(arts["metric"].payload)
    counts = {m.metric_id: m.value for m in metric.metrics if m.subject.type == "zone"}
    overdue = sum(1 for r in dataset.fact_unit_inventory_snapshot if r.is_overdue_flag)
    assert counts == {"units_in_scope": 61, "overdue_units": overdue}
    market = MarketContextPayload.model_validate(arts["market_context"].payload)
    assert len(market.fact_market_macro_monthly) == 12


async def test_unknown_ids_are_not_found() -> None:
    with pytest.raises(ArtifactNotFound):
        await reader().read("ART-EXPORT-DATASET-nope")


@pytest.mark.datapack
async def test_the_full_pack_holds_every_diagnosed_unit() -> None:
    r = ExportArtifactReader(DATAPACK_DIR, semantic())
    arts = {a.artifact_type: a for a in await r.prepare(scope("MARKET"))}
    dataset = DatasetPayload.model_validate(arts["dataset"].payload)
    assert (len(dataset.dim_unit_master), len(dataset.dm_unit_friction_diagnostics)) == (3000, 1139)
    assert len((await r.catalog()).zones) == 9
