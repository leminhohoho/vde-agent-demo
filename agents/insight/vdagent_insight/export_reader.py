"""ExportArtifactReader (D-77): the real-estate data pack (`export/*.csv`, DW 3.1.0) as input artifacts.

Dev/demo stand-in for the Data Agent until it produces artifacts itself (D-01, D-02): it reads the
CSVs once (in a worker thread, sdk R10), and for an analysis scope builds the four input artifacts
of the Insight contract, each with a deterministic id and the SHA-256 of its payload:

- `dataset`: the DW rows of the units in scope (project, zone, unit, inventory, mart, bridge);
- `dq`: missing rates it computes itself. A mart column counts as missing only on units whose
  bridge needs it (NULL elsewhere means "not applicable", data pack profiling); secondary inventory
  columns carry their missing rate among overdue and among sold units (MNAR); `data_as_of` is the
  snapshot date at 23:59 Asia/Ho_Chi_Minh (D-03); statuses follow the missing-rate tiers of config;
- `metric`: unit counts of the scope and the DOM of each overdue unit;
- `market_context`: the macro rows of the markets and segments in scope.

`catalog()` lists projects, zones and units for the free-text compatibility mode of the bridge.
`read(id)` returns an artifact prepared earlier by this reader. Pure data work, no DW query, no MCP.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .artifacts import ArtifactNotFound, content_hash
from .contracts import (
    AnalysisScope,
    CauseRow,
    DatasetPayload,
    DiagnosticRow,
    DqFieldResult,
    DqPayload,
    InputArtifact,
    InputArtifactType,
    InventoryRow,
    MacroRow,
    MarketContextPayload,
    MetricPayload,
    ProjectRow,
    UnitRow,
    ZoneRow,
)
from .gate import HUNDRED
from .settings import SemanticConfig

TABLES = ("dim_project_profile", "dim_zone_master", "dim_unit_master", "fact_unit_inventory_snapshot",
          "dm_unit_friction_diagnostics", "unit_diagnostic_causes")  # fmt: skip
INVENTORY_PRIMARY = ("unsold_days_dom", "inventory_status")
INVENTORY_SECONDARY = ("asking_price_vnd", "base_commission_pct", "subsidy_duration_mo")


@dataclass(frozen=True)
class Manifest:
    snapshot_id: str
    semantic_version: str
    snapshot_date: str


@dataclass(frozen=True)
class CatalogZone:
    zone_id: str
    zone_name: str
    project_id: str


@dataclass(frozen=True)
class CatalogUnit:
    unit_id: str
    unit_code: str
    zone_id: str
    project_id: str


@dataclass(frozen=True)
class CatalogProject:
    project_id: str
    project_name: str


@dataclass(frozen=True)
class Catalog:
    projects: tuple[CatalogProject, ...]
    zones: tuple[CatalogZone, ...]
    unit_by_code: dict[str, CatalogUnit]
    unit_by_id: dict[str, CatalogUnit]


@dataclass
class _Pack:
    manifest: Manifest
    projects: list[ProjectRow]
    zones: list[ZoneRow]
    units: list[UnitRow]
    inventory: dict[int, InventoryRow]
    mart: dict[int, DiagnosticRow]
    causes: dict[str, list[CauseRow]]
    macro: list[MacroRow]
    catalog: Catalog
    project_by_key: dict[int, ProjectRow] = field(default_factory=dict)
    zone_by_key: dict[int, ZoneRow] = field(default_factory=dict)


def _rows(folder: Path, name: str) -> list[dict[str, str]]:
    with open(folder / f"{name}.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _model[M: BaseModel](model: type[M], row: dict[str, str]) -> M:
    return model.model_validate({k: (row[k] if row.get(k, "") != "" else None) for k in model.model_fields})


def _load(folder: Path) -> _Pack:
    (m,) = _rows(folder, "snapshot_manifest")
    projects = [_model(ProjectRow, r) for r in _rows(folder, "dim_project_profile")]
    zones = [_model(ZoneRow, r) for r in _rows(folder, "dim_zone_master")]
    units = [_model(UnitRow, r) for r in _rows(folder, "dim_unit_master")]
    inventory = {r.unit_key: r for r in (_model(InventoryRow, x) for x in _rows(folder, "fact_unit_inventory_snapshot"))}
    mart = {r.unit_key: r for r in (_model(DiagnosticRow, x) for x in _rows(folder, "dm_unit_friction_diagnostics"))}
    causes: dict[str, list[CauseRow]] = defaultdict(list)
    for r in (_model(CauseRow, x) for x in _rows(folder, "unit_diagnostic_causes")):
        causes[r.diagnostic_id].append(r)
    macro = [_model(MacroRow, r) for r in _rows(folder, "fact_market_macro_monthly")]
    project_by_key = {p.project_key: p for p in projects}
    zone_by_key = {z.zone_key: z for z in zones}
    cat_units = [
        CatalogUnit(u.unit_id, u.unit_code, zone_by_key[u.zone_key].zone_id, project_by_key[u.project_key].project_id)
        for u in units
    ]
    catalog = Catalog(
        projects=tuple(CatalogProject(p.project_id, p.project_name) for p in projects),
        zones=tuple(CatalogZone(z.zone_id, z.zone_name, project_by_key[z.project_key].project_id) for z in zones),
        unit_by_code={u.unit_code: u for u in cat_units},
        unit_by_id={u.unit_id: u for u in cat_units},
    )
    return _Pack(
        manifest=Manifest(m["snapshot_id"], m["semantic_version"], m["snapshot_date"]),
        projects=projects, zones=zones, units=units, inventory=inventory, mart=mart, causes=causes,
        macro=macro, catalog=catalog, project_by_key=project_by_key, zone_by_key=zone_by_key,
    )  # fmt: skip


def _pct(missing: int, total: int) -> Decimal:
    return Decimal(missing) * HUNDRED / total if total else Decimal(0)


class ExportArtifactReader:
    def __init__(self, folder: Path, cfg: SemanticConfig) -> None:
        self._folder = folder
        self._cfg = cfg
        self._pack: _Pack | None = None
        self._lock = asyncio.Lock()
        self._prepared: dict[str, InputArtifact] = {}

    async def _data(self) -> _Pack:
        async with self._lock:
            if self._pack is None:
                self._pack = await asyncio.to_thread(_load, self._folder)
        return self._pack

    @property
    def cfg(self) -> SemanticConfig:
        return self._cfg

    async def manifest(self) -> Manifest:
        return (await self._data()).manifest

    async def catalog(self) -> Catalog:
        return (await self._data()).catalog

    async def read(self, artifact_id: str) -> InputArtifact:
        try:
            return self._prepared[artifact_id]
        except KeyError:
            raise ArtifactNotFound(f"artifact {artifact_id} not found") from None

    def _in_scope(self, pack: _Pack, s: AnalysisScope) -> list[UnitRow]:
        out = []
        for u in pack.units:
            project, zone = pack.project_by_key[u.project_key], pack.zone_by_key[u.zone_key]
            if s.unit_ids and u.unit_id not in s.unit_ids:
                continue
            if s.zone_ids and zone.zone_id not in s.zone_ids:
                continue
            if s.project_ids and project.project_id not in s.project_ids:
                continue
            out.append(u)
        return out

    def _dataset(self, pack: _Pack, units: list[UnitRow]) -> DatasetPayload:
        keys = [u.unit_key for u in units]
        project_keys = {u.project_key for u in units}
        zone_keys = {u.zone_key for u in units}
        mart = [pack.mart[k] for k in keys if k in pack.mart]
        return DatasetPayload(
            source_refs=[f"{t}@{pack.manifest.snapshot_id}" for t in TABLES],
            dim_project_profile=[p for p in pack.projects if p.project_key in project_keys],
            dim_zone_master=[z for z in pack.zones if z.zone_key in zone_keys],
            dim_unit_master=units,
            fact_unit_inventory_snapshot=[pack.inventory[k] for k in keys if k in pack.inventory],
            dm_unit_friction_diagnostics=mart,
            unit_diagnostic_causes=[
                c for d in mart for c in sorted(pack.causes.get(d.diagnostic_id, []), key=lambda c: c.severity_rank)
            ],
        )

    def _status(self, missing_pct: Decimal) -> str:
        tiers = self._cfg.params.missing_rate_tiers
        if missing_pct <= tiers.none_max:
            return "PASS"
        return "WARN" if missing_pct <= tiers.describe_only_max else "FAIL"

    def _dq(self, pack: _Pack, data: DatasetPayload) -> DqPayload:
        inv = data.fact_unit_inventory_snapshot
        threshold = self._cfg.params.overdue_threshold_days
        overdue = [r for r in inv if r.inventory_status == "AVAILABLE" and r.unsold_days_dom > threshold]
        sold = [r for r in inv if r.inventory_status == "SOLD"]
        fields: list[DqFieldResult] = []
        for name in (*INVENTORY_PRIMARY, *INVENTORY_SECONDARY):
            pct = _pct(sum(getattr(r, name) is None for r in inv), len(inv))
            primary = name in INVENTORY_PRIMARY
            extra: dict[str, Any] = {}
            if not primary:
                extra = {
                    "missing_rate_overdue_pct": _pct(sum(getattr(r, name) is None for r in overdue), len(overdue)),
                    "missing_rate_sold_pct": _pct(sum(getattr(r, name) is None for r in sold), len(sold)),
                }
            fields.append(DqFieldResult.model_validate({
                "table": "fact_unit_inventory_snapshot", "field": name, "status": self._status(pct), "is_primary": primary,
                "missing_rate_pct": pct, **extra,
            }))  # fmt: skip
        needed: dict[str, list[DiagnosticRow]] = defaultdict(list)
        for d in data.dm_unit_friction_diagnostics:
            for c in pack.causes.get(d.diagnostic_id, []):
                if c.cause_code in self._cfg.allowed_cause_codes:
                    for spec in self._cfg.cause(c.cause_code).required_evidence:
                        if spec.table == "dm_unit_friction_diagnostics":
                            needed[spec.field].append(d)
        for name in sorted(needed):
            rows = list({id(d): d for d in needed[name]}.values())
            pct = _pct(sum(getattr(d, name) is None for d in rows), len(rows))
            fields.append(DqFieldResult.model_validate({
                "table": "dm_unit_friction_diagnostics", "field": name, "status": self._status(pct), "is_primary": True,
                "missing_rate_pct": pct,
            }))  # fmt: skip
        worst = (
            "FAIL" if any(f.status == "FAIL" for f in fields) else "WARN" if any(f.status == "WARN" for f in fields) else "PASS"
        )
        return DqPayload.model_validate({
            "overall_status": worst, "snapshot_date": pack.manifest.snapshot_date,
            "data_as_of": f"{pack.manifest.snapshot_date}T23:59:00+07:00", "fields": fields,
        })  # fmt: skip

    def _metric(self, pack: _Pack, s: AnalysisScope, data: DatasetPayload) -> MetricPayload:
        threshold = self._cfg.params.overdue_threshold_days
        if s.zone_ids:
            z = next(z for z in data.dim_zone_master if z.zone_id == s.zone_ids[0]) if data.dim_zone_master else None
            subject = {"type": "zone", "id": s.zone_ids[0], "label": z.zone_name if z else s.zone_ids[0]}
        elif s.project_ids and data.dim_project_profile:
            p = data.dim_project_profile[0]
            subject = {"type": "project", "id": p.project_id, "label": p.project_name}
        else:
            subject = {"type": "market", "id": "MARKET", "label": "thị trường"}
        units = {u.unit_key: u for u in data.dim_unit_master}
        overdue = [
            r for r in data.fact_unit_inventory_snapshot if r.inventory_status == "AVAILABLE" and r.unsold_days_dom > threshold
        ]
        n = len(data.dim_unit_master)

        def count(metric_id: str, value: int) -> dict[str, Any]:
            return {
                "metric_id": metric_id,
                "calculation_ref": f"export.{metric_id}@1",
                "subject": subject,
                "value": str(value),
                "unit": "COUNT",
                "n": n,
            }

        metrics = [count("units_in_scope", n), count("overdue_units", len(overdue))]
        for r in overdue:
            u = units[r.unit_key]
            metrics.append(
                {
                    "metric_id": "unsold_days_dom",
                    "calculation_ref": "dw.unsold_days_dom@3.1.0",
                    "subject": {"type": "unit", "id": u.unit_id, "label": u.unit_code},
                    "value": str(r.unsold_days_dom),
                    "unit": "DAY",
                }
            )
        return MetricPayload.model_validate({"metrics": metrics})

    def _market(self, pack: _Pack, data: DatasetPayload) -> MarketContextPayload:
        markets = {(p.market_id, p.segment) for p in data.dim_project_profile}
        rows = [r for r in pack.macro if (r.market_id, r.segment) in markets]
        return MarketContextPayload(
            source_refs=[f"fact_market_macro_monthly@{pack.manifest.snapshot_id}"], fact_market_macro_monthly=rows
        )

    def _artifact(self, pack: _Pack, kind: InputArtifactType, scope_key: str, payload: BaseModel) -> InputArtifact:
        data = payload.model_dump(mode="json")
        art = InputArtifact(
            artifact_id=f"ART-EXPORT-{kind.upper()}-{scope_key}",
            artifact_type=kind,
            version=1,
            status="VALID",
            snapshot_id=pack.manifest.snapshot_id,
            semantic_config_version=pack.manifest.semantic_version,
            content_hash=content_hash(data),
            payload=data,
        )
        self._prepared[art.artifact_id] = art
        return art

    async def prepare(self, s: AnalysisScope) -> list[InputArtifact]:
        """The input artifacts of a scope, in the order metric, dq, dataset, market_context."""
        pack = await self._data()
        scope_key = hashlib.sha256(json.dumps(s.model_dump(), sort_keys=True).encode()).hexdigest()[:12]
        data = self._dataset(pack, self._in_scope(pack, s))
        return [
            self._artifact(pack, "metric", scope_key, self._metric(pack, s, data)),
            self._artifact(pack, "dq", scope_key, self._dq(pack, data)),
            self._artifact(pack, "dataset", scope_key, data),
            self._artifact(pack, "market_context", scope_key, self._market(pack, data)),
        ]
