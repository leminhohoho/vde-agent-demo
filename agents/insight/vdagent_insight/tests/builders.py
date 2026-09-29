"""Test-only builders for DW rows and artifact payloads (defaults = one healthy project/zone/unit).

Column names follow DW Schema v3.1.0 (see contracts.py "Input artifact payloads").
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from ..candidates.common import CandidateContext
from ..contracts import (
    AnalysisScope,
    CauseRow,
    DatasetPayload,
    DiagnosticRow,
    DqFieldResult,
    DqPayload,
    InputArtifact,
    InsightCandidate,
    InsightTaskRequest,
    InventoryRow,
    MacroRow,
    MarketContextPayload,
    MetricPayload,
    ProjectRow,
    UnitRow,
    ZoneRow,
)
from ..gate import run_gate
from ..settings import CONFIG_DIR, LlmConfig, SemanticConfig, load_llm_config, load_semantic_config
from ..view import DatasetView

SNAP = "SNAP-20260630-01"
DATE_KEY = 20260630


def semantic() -> SemanticConfig:
    return load_semantic_config(CONFIG_DIR / "semantic_insight.yaml")


def llm() -> LlmConfig:
    return load_llm_config(CONFIG_DIR / "llm.yaml")


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


def cause(i: int, code: str, rank: int = 1, score: str = "1.000", evidence_artifact_id: str | None = None) -> CauseRow:
    return CauseRow.model_validate(
        {
            "diagnostic_id": f"DIAG-{DATE_KEY}-U{i:03d}",
            "cause_code": code,
            "unit_key": 1000 + i,
            "snapshot_date_key": DATE_KEY,
            "severity_rank": rank,
            "attribution_score": score,
            "evidence_artifact_id": evidence_artifact_id,
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


AS_OF = datetime.fromisoformat("2026-07-01T00:00:00+07:00")
RUN_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
TASK_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
HASH = "a" * 64


def request(
    tasks: Sequence[str] = ("T1", "T6", "T7"), analysis_scope: AnalysisScope | None = None, **kw: Any
) -> InsightTaskRequest:
    s = analysis_scope or scope("PROJECT", project_ids=["PRJ-X"])
    data: dict[str, Any] = {
        "run_id": RUN_ID,
        "task_id": TASK_ID,
        "attempt": 1,
        "fencing_token": 1,
        "intent": "SLOW_MOVING_INVESTIGATION",
        "tasks": list(tasks),
        "question_normalized": "Vì sao căn bán chậm?",
        "analysis_scope": s.model_dump(),
        "snapshot_id": SNAP,
        "semantic_config_version": semantic().version,
        "user_context": {
            "user_id": "u_1",
            "role": "SALES_OPS",
            "authorized_scope": {"project_ids": ["PRJ-X"], "zone_ids": []},
        },
        "input_artifact_refs": [
            {"artifact_id": "ART-DATASET", "artifact_type": "dataset", "version": 1, "status": "VALID", "content_hash": HASH}
        ],
    }
    return InsightTaskRequest.model_validate({**data, **kw})


def context(
    data: DatasetPayload,
    dq_payload: DqPayload | None = None,
    tasks: Sequence[str] = ("T1", "T6", "T7"),
    analysis_scope: AnalysisScope | None = None,
    cfg: SemanticConfig | None = None,
    metric: MetricPayload | None = None,
    market_payload: MarketContextPayload | None = None,
    as_of: datetime = AS_OF,
    recent_subject_ids: frozenset[str] = frozenset(),
) -> CandidateContext:
    cfg = cfg or semantic()
    req = request(tasks, analysis_scope)
    view = DatasetView(data)
    gate = run_gate(view, dq_payload or dq(), req.analysis_scope, cfg, as_of)
    return CandidateContext(
        request=req,
        cfg=cfg,
        view=view,
        gate=gate,
        dataset_id="ART-DATASET",
        dq_id="ART-DQ",
        metric=metric,
        metric_id="ART-METRIC" if metric else None,
        market=market_payload,
        market_id="ART-MARKET" if market_payload else None,
        recent_subject_ids=recent_subject_ids,
        recent_subject_boost=llm().memory.recent_subject_boost,
    )


def with_params(cfg: SemanticConfig, **params: Any) -> SemanticConfig:
    return cfg.model_copy(update={"params": cfg.params.model_copy(update=params)})


def macro(
    date_key: int, rate: str = "8.50", absorption: str = "30.00", moi: str | None = "14.5", pir: str | None = "18.2", **kw: Any
) -> MacroRow:
    data: dict[str, Any] = {
        "macro_record_id": f"MAC-{date_key}-{kw.get('market_id', 'MKT-EAST-HCM')}-{kw.get('segment', 'MID_HIGH')}",
        "date_key": date_key,
        "market_id": "MKT-EAST-HCM",
        "segment": "MID_HIGH",
        "floating_mortgage_rate_pct": rate,
        "months_of_inventory_moi": moi,
        "absorption_rate_pct": absorption,
        "median_household_income_vnd": 300_000_000,
        "macro_price_to_income_ratio": pir,
    }
    return MacroRow.model_validate({**data, **kw})


def market(rows: Sequence[MacroRow]) -> MarketContextPayload:
    return MarketContextPayload(source_refs=[f"fact_market_macro_monthly@{SNAP}"], fact_market_macro_monthly=list(rows))


class DictReader:
    """An ArtifactReader over artifacts held in memory."""

    def __init__(self, artifacts: Sequence[InputArtifact]) -> None:
        self._by_id = {a.artifact_id: a for a in artifacts}

    async def read(self, artifact_id: str) -> InputArtifact:
        from ..artifacts import ArtifactNotFound

        try:
            return self._by_id[artifact_id]
        except KeyError:
            raise ArtifactNotFound(artifact_id) from None


def input_artifact(
    artifact_id: str, artifact_type: str, payload: Any, snapshot_id: str = SNAP, version: str | None = None
) -> InputArtifact:
    from ..artifacts import content_hash

    data = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
    return InputArtifact.model_validate(
        {
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "version": 1,
            "status": "VALID",
            "snapshot_id": snapshot_id,
            "semantic_config_version": version or semantic().version,
            "content_hash": content_hash(data),
            "payload": data,
        }
    )


def task(
    data: DatasetPayload,
    dq_payload: DqPayload | None = None,
    market_payload: MarketContextPayload | None = None,
    tasks: Sequence[str] = ("T1", "T6", "T7"),
    analysis_scope: AnalysisScope | None = None,
    **request_kw: Any,
) -> tuple[InsightTaskRequest, DictReader]:
    """A request and the reader of its artifacts (metric, dq, dataset[, market_context])."""
    arts = [
        input_artifact("ART-METRIC", "metric", {"metrics": []}),
        input_artifact("ART-DQ", "dq", dq_payload or dq()),
        input_artifact("ART-DATASET", "dataset", data),
    ]
    if market_payload is not None:
        arts.append(input_artifact("ART-MARKET", "market_context", market_payload))
    refs = [
        {
            "artifact_id": a.artifact_id,
            "artifact_type": a.artifact_type,
            "version": 1,
            "status": "VALID",
            "content_hash": a.content_hash,
        }
        for a in arts
    ]
    req = request(tasks, analysis_scope, input_artifact_refs=refs, **request_kw)
    return req, DictReader(arts)
