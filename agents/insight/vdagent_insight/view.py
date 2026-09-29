"""Read-only view over a `dataset` artifact payload, shared by the gate (step 3) and the candidate
engine (step 4). Pure: indexes the DW rows once, joins them per unit, selects units by scope.

BR-01 lives here: a unit is overdue only when its **inventory** row says AVAILABLE and
`unsold_days_dom` > the threshold. Presence in the diagnostic mart proves nothing.

Row indexes are kept so evidence can point at `/<table>/<index>/<field>` of the artifact.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .contracts import (
    AnalysisScope,
    CauseRow,
    DatasetPayload,
    DiagnosticRow,
    InventoryRow,
    ProjectRow,
    UnitRow,
    ZoneRow,
)


@dataclass(frozen=True)
class UnitView:
    unit: UnitRow
    unit_index: int
    inventory: InventoryRow | None
    inventory_index: int | None
    diagnostic: DiagnosticRow | None
    diagnostic_index: int | None
    causes: tuple[tuple[CauseRow, int], ...]
    """Bridge rows of this unit's diagnostic with their row index, by `severity_rank` then `cause_code`."""
    zone: ZoneRow | None
    project: ProjectRow | None


def json_path(table: str, index: int, field: str | None = None) -> str:
    return f"/{table}/{index}" + (f"/{field}" if field else "")


class DatasetView:
    def __init__(self, dataset: DatasetPayload) -> None:
        self.dataset = dataset
        projects = {p.project_key: p for p in dataset.dim_project_profile}
        zones = {z.zone_key: z for z in dataset.dim_zone_master}
        inventory = {r.unit_key: (r, i) for i, r in enumerate(dataset.fact_unit_inventory_snapshot)}
        diagnostics = {r.unit_key: (r, i) for i, r in enumerate(dataset.dm_unit_friction_diagnostics)}
        causes: dict[str, list[tuple[CauseRow, int]]] = defaultdict(list)
        for i, row in enumerate(dataset.unit_diagnostic_causes):
            causes[row.diagnostic_id].append((row, i))
        views: list[UnitView] = []
        for i, u in enumerate(dataset.dim_unit_master):
            inv = inventory.get(u.unit_key)
            diag = diagnostics.get(u.unit_key)
            rows = causes.get(diag[0].diagnostic_id, []) if diag else []
            views.append(
                UnitView(
                    unit=u,
                    unit_index=i,
                    inventory=inv[0] if inv else None,
                    inventory_index=inv[1] if inv else None,
                    diagnostic=diag[0] if diag else None,
                    diagnostic_index=diag[1] if diag else None,
                    causes=tuple(sorted(rows, key=lambda r: (r[0].severity_rank, r[0].cause_code))),
                    zone=zones.get(u.zone_key),
                    project=projects.get(u.project_key),
                )
            )
        self.units = tuple(sorted(views, key=lambda v: v.unit.unit_key))
        self.projects = tuple(sorted(dataset.dim_project_profile, key=lambda p: p.project_key))
        self.zones = tuple(sorted(dataset.dim_zone_master, key=lambda z: z.zone_key))

    def project_index(self, project_key: int) -> int | None:
        for i, p in enumerate(self.dataset.dim_project_profile):
            if p.project_key == project_key:
                return i
        return None

    def units_in_scope(self, scope: AnalysisScope) -> list[UnitView]:
        """Units matching every non-empty id list of the scope (all units for an empty scope)."""
        return [
            v
            for v in self.units
            if (not scope.unit_ids or v.unit.unit_id in scope.unit_ids)
            and (not scope.zone_ids or (v.zone is not None and v.zone.zone_id in scope.zone_ids))
            and (not scope.project_ids or (v.project is not None and v.project.project_id in scope.project_ids))
        ]

    @staticmethod
    def is_overdue(view: UnitView, threshold_days: int) -> bool:
        """BR-01, from the inventory snapshot only."""
        inv = view.inventory
        return inv is not None and inv.inventory_status == "AVAILABLE" and inv.unsold_days_dom > threshold_days
