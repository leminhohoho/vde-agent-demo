"""The data pack sample (`fixtures/export_sample/`) is a faithful cut of the data pack."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from .conftest import DATAPACK_DIR, EXPORT_SAMPLE_DIR

UNIT_GRAIN = ("dim_unit_master", "fact_unit_inventory_snapshot", "dm_unit_friction_diagnostics", "unit_diagnostic_causes")


def read(folder: Path, name: str) -> tuple[list[str], list[dict[str, str]]]:
    with open(folder / f"{name}.csv", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def test_sample_holds_the_tc01_and_tc04_units() -> None:
    _, mart = read(EXPORT_SAMPLE_DIR, "dm_unit_friction_diagnostics")
    (tc01,) = [r for r in mart if r["unit_code"] == "SAPPHIRE1-16.231"]
    assert (tc01["unsold_days_dom"], tc01["price_spread_vs_peer_pct"], tc01["peer_count"]) == ("143", "19.82", "12")
    assert tc01["primary_cause_code"] == "OVERPRICED_VS_PEER" and tc01["is_peer_sample_constrained"] == "False"
    _, projects = read(EXPORT_SAMPLE_DIR, "dim_project_profile")
    (beverly,) = [p for p in projects if p["project_id"] == "PRJ-VHOP-BEVERLY"]
    assert beverly["is_sales_permit_issued"] == "False"
    assert any(r["zone_name"] == "The Beverly" and r["primary_cause_code"] == "LEGAL_PERMIT_BARRIER" for r in mart)


def test_every_sampled_unit_is_complete_across_unit_tables() -> None:
    _, units = read(EXPORT_SAMPLE_DIR, "dim_unit_master")
    _, inventory = read(EXPORT_SAMPLE_DIR, "fact_unit_inventory_snapshot")
    keys = {u["unit_key"] for u in units}
    assert {r["unit_key"] for r in inventory} == keys
    _, mart = read(EXPORT_SAMPLE_DIR, "dm_unit_friction_diagnostics")
    _, bridge = read(EXPORT_SAMPLE_DIR, "unit_diagnostic_causes")
    assert {r["diagnostic_id"] for r in bridge} == {r["diagnostic_id"] for r in mart}


@pytest.mark.datapack
@pytest.mark.parametrize(
    "name", [*UNIT_GRAIN, "snapshot_manifest", "semantic_config", "dim_project_profile", "fact_market_macro_monthly"]
)
def test_sample_rows_are_verbatim_rows_of_the_data_pack(name: str) -> None:
    sample_header, sample = read(EXPORT_SAMPLE_DIR, name)
    full_header, full = read(DATAPACK_DIR, name)
    assert sample_header == full_header
    full_rows = {tuple(r.values()) for r in full}
    assert all(tuple(r.values()) in full_rows for r in sample)
