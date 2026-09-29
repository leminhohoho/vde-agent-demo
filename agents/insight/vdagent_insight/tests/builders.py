"""Test-only builders for DW rows and artifact payloads (defaults = one healthy project/zone/unit).

Column names follow DW Schema v3.1.0 (see contracts.py "Input artifact payloads").
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..contracts import (
    AnalysisScope,
    CauseRow,
    DatasetPayload,
    DiagnosticRow,
    DqFieldResult,
    DqPayload,
    InsightCandidate,
    InventoryRow,
    ProjectRow,
    UnitRow,
    ZoneRow,
)
from ..settings import CONFIG_DIR, SemanticConfig, load_semantic_config

SNAP = "SNAP-20260630-01"
DATE_KEY = 20260630


def semantic() -> SemanticConfig:
    return load_semantic_config(CONFIG_DIR / "semantic_insight.yaml")


def project(**kw: Any) -> ProjectRow:
    data: dict[str, Any] = {
        "project_key": 1,
        "project_id": "PRJ-X",
        "project_name": "Dự án X",
        "market_id": "MKT-EAST-HCM",
        "segment": "MID_HIGH",
        "is_sales_permit_issued": True,
        "is_bank_guarantee_issued": True,
    }
    return ProjectRow.model_validate({**data, **kw})


def zone(**kw: Any) -> ZoneRow:
    data: dict[str, Any] = {"zone_key": 1, "zone_id": "ZN-AQUA-01", "project_key": 1, "zone_name": "Tòa Aqua 1"}
    return ZoneRow.model_validate({**data, **kw})


def unit(i: int, **kw: Any) -> UnitRow:
    data: dict[str, Any] = {
        "unit_key": 1000 + i,
        "unit_id": f"U{i:03d}",
        "unit_code": f"A-{i:02d}.01",
        "project_key": 1,
        "zone_key": 1,
        "unit_type": "2PN",
        "net_area_m2": "70.00",
        "floor_number": 5,
        "floor_band": "LOW",
        "balcony_orientation": "E",
        "view_primary_type": "PARK",
    }
    return UnitRow.model_validate({**data, **kw})


def inventory(i: int, dom: int, status: str = "AVAILABLE", threshold: int = 90, **kw: Any) -> InventoryRow:
    data: dict[str, Any] = {
        "snapshot_date_key": DATE_KEY,
        "unit_key": 1000 + i,
        "project_key": 1,
        "zone_key": 1,
        "channel_key": 1,
        "launch_batch_id": "BATCH-01",
        "inventory_status": status,
        "unsold_days_dom": dom,
        "is_overdue_flag": status == "AVAILABLE" and dom > threshold,
        "asking_price_vnd": 4_200_000_000,
        "asking_price_per_m2": 60_000_000,
        "subsidy_duration_mo": 12,
        "base_commission_pct": "2.00",
        "spiff_bonus_vnd": 50_000_000,
    }
    return InventoryRow.model_validate({**data, **kw})


def diagnostic(i: int, dom: int, primary: str = "OVERPRICED_VS_PEER", action: str | None = None, **kw: Any) -> DiagnosticRow:
    cfg = semantic()
    data: dict[str, Any] = {
        "diagnostic_id": f"DIAG-{DATE_KEY}-U{i:03d}",
        "snapshot_date_key": DATE_KEY,
        "unit_key": 1000 + i,
        "unit_code": f"A-{i:02d}.01",
        "project_name": "Dự án X",
        "zone_name": "Tòa Aqua 1",
        "unsold_days_dom": dom,
        "price_spread_vs_peer_pct": "12.40",
        "ticket_size_vs_income_ratio": "14.5",
        "physical_defect_penalty": 5,
        "thermal_view_penalty": 10,
        "secondary_price_gap_pct": "3.10",
        "funnel_dropoff_rate_pct": "20.00",
        "primary_cause_code": primary,
        "recommended_action": action or cfg.cause_action_mapping.get(primary, "UNKNOWN_ACTION"),
        "is_peer_sample_constrained": False,
        "peer_count": 12,
    }
    return DiagnosticRow.model_validate({**data, **kw})


def cause(i: int, code: str, rank: int = 1, score: str = "1.000") -> CauseRow:
    return CauseRow.model_validate(
        {
            "diagnostic_id": f"DIAG-{DATE_KEY}-U{i:03d}",
            "cause_code": code,
            "unit_key": 1000 + i,
            "snapshot_date_key": DATE_KEY,
            "severity_rank": rank,
            "attribution_score": score,
        }
    )


def dataset(
    units: list[UnitRow],
    inventory_rows: list[InventoryRow],
    diagnostics: Sequence[DiagnosticRow] = (),
    causes: Sequence[CauseRow] = (),
    projects: list[ProjectRow] | None = None,
    zones: list[ZoneRow] | None = None,
) -> DatasetPayload:
    return DatasetPayload(
        source_refs=[f"dm_unit_friction_diagnostics@{SNAP}"],
        dim_project_profile=projects if projects is not None else [project()],
        dim_zone_master=zones if zones is not None else [zone()],
        dim_unit_master=units,
        fact_unit_inventory_snapshot=inventory_rows,
        dm_unit_friction_diagnostics=list(diagnostics),
        unit_diagnostic_causes=list(causes),
    )


def dq_field(table: str, field: str, status: str = "PASS", primary: bool = True, missing: str = "0", **kw: Any) -> DqFieldResult:
    data: dict[str, Any] = {"table": table, "field": field, "status": status, "is_primary": primary, "missing_rate_pct": missing}
    return DqFieldResult.model_validate({**data, **kw})


def dq(fields: Sequence[DqFieldResult] = (), status: str = "PASS", data_as_of: str = "2026-06-30T23:00:00+07:00") -> DqPayload:
    return DqPayload.model_validate(
        {"overall_status": status, "snapshot_date": "2026-06-30", "data_as_of": data_as_of, "fields": list(fields)}
    )


def scope(level: str = "PROJECT", **ids: list[str]) -> AnalysisScope:
    return AnalysisScope.model_validate({"level": level, **ids})


def candidate(candidate_id: str, task: str = "T1", priority: str = "0.5", subject_id: str = "U001") -> InsightCandidate:
    insight_type = {
        "T1": "ROOT_CAUSE_SIGNAL",
        "T2": "CAUSE_DISTRIBUTION",
        "T3": "PATTERN",
        "T5": "MARKET_CONTEXT",
        "T7": "DATA_LIMITATION",
    }
    return InsightCandidate.model_validate(
        {
            "candidate_id": candidate_id,
            "task": task,
            "insight_type": insight_type[task],
            "level": "UNIT",
            "subject": {"type": "unit", "id": subject_id, "label": subject_id},
            "slots": {},
            "evidence_refs": [],
            "lineage": {"source_refs": [], "calculation_refs": []},
            "significant": False,
            "confidence": "HIGH",
            "dq_flags": [],
            "priority": priority,
        }
    )
