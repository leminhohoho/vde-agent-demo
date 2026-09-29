"""DatasetView: scope selection and BR-01 (overdue = AVAILABLE and DOM > threshold, from inventory)."""

from __future__ import annotations

from ..view import DatasetView
from .builders import cause, dataset, diagnostic, inventory, scope, unit, zone


def two_zone_dataset() -> DatasetView:
    zones = [zone(), zone(zone_key=2, zone_id="ZN-AQUA-02", zone_name="Tòa Aqua 2")]
    units = [unit(1), unit(2), unit(3, zone_key=2)]
    inv = [inventory(1, 100), inventory(2, 50), inventory(3, 200, zone_key=2)]
    return DatasetView(dataset(units, inv, zones=zones))


def test_scope_filters_by_unit_zone_and_project_ids() -> None:
    view = two_zone_dataset()
    assert [u.unit.unit_id for u in view.units_in_scope(scope("UNIT", unit_ids=["U002"]))] == ["U002"]
    assert [u.unit.unit_id for u in view.units_in_scope(scope("ZONE", zone_ids=["ZN-AQUA-02"]))] == ["U003"]
    assert [u.unit.unit_id for u in view.units_in_scope(scope("PROJECT", project_ids=["PRJ-X"]))] == ["U001", "U002", "U003"]
    assert [u.unit.unit_id for u in view.units_in_scope(scope("MARKET"))] == ["U001", "U002", "U003"]
    assert view.units_in_scope(scope("PROJECT", project_ids=["PRJ-OTHER"])) == []


def test_br01_overdue_only_when_available_and_dom_strictly_above_the_threshold() -> None:
    units = [unit(i) for i in range(1, 7)]
    inv = [
        inventory(1, 91),
        inventory(2, 90),
        inventory(3, 150, status="SOLD"),
        inventory(4, 120, status="BOOKED"),
        inventory(5, 89),
    ]  # unit 6 has no inventory row
    diags = [diagnostic(i, 150) for i in range(1, 7)]
    view = DatasetView(dataset(units, inv, diags))
    overdue = [u.unit.unit_id for u in view.units_in_scope(scope("MARKET")) if view.is_overdue(u, 90)]
    assert overdue == ["U001"]
    assert [u.unit.unit_id for u in view.units_in_scope(scope("MARKET")) if view.is_overdue(u, 60)] == ["U001", "U002", "U005"]


def test_unit_view_joins_mart_and_bridge_sorted_by_rank_with_their_row_indexes() -> None:
    units = [unit(1), unit(2)]
    inv = [inventory(1, 120), inventory(2, 130)]
    diags = [diagnostic(2, 130), diagnostic(1, 120)]
    causes = [cause(1, "LOW_SALES_INCENTIVE", 2, "0.4"), cause(2, "OVERPRICED_VS_PEER"), cause(1, "OVERPRICED_VS_PEER", 1, "0.6")]
    view = DatasetView(dataset(units, inv, diags, causes))
    (u1,) = view.units_in_scope(scope("UNIT", unit_ids=["U001"]))
    assert u1.diagnostic is not None and u1.diagnostic_index == 1
    assert u1.inventory is not None and u1.inventory_index == 0
    assert [(c.cause_code, i) for c, i in u1.causes] == [("OVERPRICED_VS_PEER", 2), ("LOW_SALES_INCENTIVE", 0)]
    assert u1.zone is not None and u1.zone.zone_id == "ZN-AQUA-01"
    assert u1.project is not None and u1.project.project_id == "PRJ-X"
