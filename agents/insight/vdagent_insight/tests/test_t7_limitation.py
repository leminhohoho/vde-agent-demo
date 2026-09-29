"""T7 Limitation Reporting (spec §6.2, §5.5, E05–E07, E14): DATA_LIMITATION and CONFLICT candidates."""

from __future__ import annotations

from decimal import Decimal

from ..candidates.t7_limitation import t7_candidates
from ..contracts import DatasetPayload, InsightCandidate, MetricPayload
from .builders import (
    cause,
    context,
    dataset,
    diagnostic,
    dq,
    dq_field,
    inventory,
    project,
    scope,
    unit,
)

TASKS = ("T1", "T2", "T6", "T7")
UNIT_TASKS = ("T1", "T6", "T7")


def by_id(cands: list[InsightCandidate]) -> dict[str, InsightCandidate]:
    return {c.candidate_id: c for c in cands}


def healthy() -> DatasetPayload:
    return dataset([unit(11)], [inventory(11, 145)], [diagnostic(11, 145)], [cause(11, "OVERPRICED_VS_PEER")])


def test_a_healthy_unit_needs_no_limitation() -> None:
    assert t7_candidates(context(healthy(), tasks=UNIT_TASKS)).candidates == []


def test_tc23_excluded_secondary_field_and_mnar_are_reported() -> None:
    field = dq_field(
        "fact_unit_inventory_snapshot",
        "spiff_bonus_vnd",
        "WARN",
        primary=False,
        missing="45",
        missing_rate_overdue_pct="30",
        missing_rate_sold_pct="5",
    )
    ctx = context(healthy(), dq([field]), tasks=TASKS)
    c = by_id(t7_candidates(ctx).candidates)["C-T7-DQ-fact_unit_inventory_snapshot-spiff_bonus_vnd"]
    assert (c.task, c.insight_type) == ("T7", "DATA_LIMITATION")
    assert c.dq_flags == ["FIELD_EXCLUDED", "MISSING_NOT_RANDOM"]
    assert (c.slots["missing_rate"].value, c.slots["mnar_gap"].value) == (Decimal(45), Decimal(25))
    assert c.slots["missing_rate"].metric_ref == "ART-DQ#/fields/0/missing_rate_pct"
    assert (c.subject.type, c.subject.id) == ("field", "fact_unit_inventory_snapshot.spiff_bonus_vnd")
    assert c.priority == Decimal(0) and c.cause_code is None and c.action_code is None


def test_stale_data_is_a_limitation() -> None:
    ctx = context(healthy(), dq(data_as_of="2026-06-27T23:00:00+07:00"), tasks=TASKS)
    c = by_id(t7_candidates(ctx).candidates)["C-T7-STALE_SNAPSHOT"]
    assert c.dq_flags == ["STALE_SNAPSHOT"] and c.slots["data_age_hours"].value == Decimal(73)


def test_tc21_partial_zone_coverage_is_a_limitation_with_its_denominator() -> None:
    units = [unit(i) for i in range(1, 41)]
    inv = [inventory(i, 100 + i) for i in range(1, 41)]
    data = dataset(
        units, inv, [diagnostic(i, 100 + i) for i in range(1, 29)], [cause(i, "OVERPRICED_VS_PEER") for i in range(1, 29)]
    )
    ctx = context(data, tasks=TASKS, analysis_scope=scope("ZONE", zone_ids=["ZN-AQUA-01"]))
    c = by_id(t7_candidates(ctx).candidates)["C-T7-PARTIAL_COVERAGE-ZN-AQUA-01"]
    assert c.level == "ZONE" and c.dq_flags == ["PARTIAL_COVERAGE"]
    assert (c.slots["units_valid"].display, c.slots["overdue_units"].display, c.slots["coverage"].display) == (
        "28 căn",
        "40 căn",
        "70%",
    )


def peer_problems(extra: int = 0) -> DatasetPayload:
    """U001 constrained (3 peers), U002 without peer data, then `extra` more constrained units."""
    ids = [1, 2, *range(3, 3 + extra)]
    diags = [diagnostic(1, 120, is_peer_sample_constrained=True, peer_count=3), diagnostic(2, 130, price_spread_vs_peer_pct=None)]
    diags += [diagnostic(i, 140, is_peer_sample_constrained=True, peer_count=2) for i in ids[2:]]
    return dataset(
        [unit(i) for i in ids],
        [inventory(1, 120), inventory(2, 130), *(inventory(i, 140) for i in ids[2:])],
        diags,
        [cause(i, "OVERPRICED_VS_PEER") for i in ids],
    )


def test_constrained_or_missing_peer_data_is_reported_per_unit_in_a_unit_scope() -> None:
    unit_scope = scope("UNIT", unit_ids=["U001", "U002"])
    found = by_id(t7_candidates(context(peer_problems(), tasks=UNIT_TASKS, analysis_scope=unit_scope)).candidates)
    constrained = found["C-T7-PEER_SAMPLE_CONSTRAINED-U001"]
    assert constrained.dq_flags == ["PEER_SAMPLE_CONSTRAINED"] and constrained.slots["peers"].value == 3
    assert found["C-T7-PEER_DATA_MISSING-U002"].dq_flags == ["PEER_DATA_MISSING"]


def test_tc07_no_overdue_unit_in_scope() -> None:
    data = dataset([unit(1), unit(2)], [inventory(1, 40), inventory(2, 200, "SOLD")])
    (c,) = t7_candidates(context(data, tasks=TASKS)).candidates
    assert c.candidate_id == "C-T7-NO_OVERDUE_UNITS"
    assert (c.insight_type, c.dq_flags, c.slots["units_in_scope"].value) == ("DATA_LIMITATION", ["NO_OVERDUE_UNITS"], 2)
    assert (c.subject.type, c.subject.id) == ("project", "PRJ-X")


def test_tc17_mart_and_bridge_disagreeing_is_a_conflict() -> None:
    data = dataset([unit(11)], [inventory(11, 145)], [diagnostic(11, 145)], [cause(11, "LOW_SALES_INCENTIVE", 1, "0.900")])
    c = by_id(t7_candidates(context(data, tasks=TASKS)).candidates)["C-T7-CONFLICT-U011"]
    assert c.insight_type == "CONFLICT"
    assert c.dq_flags == ["PRIMARY_CAUSE_MISMATCH", "ATTRIBUTION_SUM_MISMATCH"]
    assert "ART-DATASET#/dm_unit_friction_diagnostics/0" in c.evidence_refs


def test_mart_action_differing_from_the_mapping_is_a_conflict() -> None:
    data = dataset(
        [unit(11)], [inventory(11, 145)], [diagnostic(11, 145, action="SALES_PITCH_AUDIT")], [cause(11, "OVERPRICED_VS_PEER")]
    )
    c = by_id(t7_candidates(context(data, tasks=TASKS)).candidates)["C-T7-CONFLICT-U011"]
    assert c.dq_flags == ["ACTION_CODE_MISMATCH"]


def test_two_sources_disagreeing_beyond_the_tolerance_is_a_conflict() -> None:
    far = dataset([unit(11)], [inventory(11, 150)], [diagnostic(11, 145)], [cause(11, "OVERPRICED_VS_PEER")])
    c = by_id(t7_candidates(context(far, tasks=TASKS)).candidates)["C-T7-CONFLICT-U011"]
    assert c.dq_flags == ["SOURCE_MISMATCH"]
    near = dataset([unit(11)], [inventory(11, 1000)], [diagnostic(11, 1009)], [cause(11, "OVERPRICED_VS_PEER")])
    assert "C-T7-CONFLICT-U011" not in by_id(t7_candidates(context(near, tasks=TASKS)).candidates)


def test_metric_artifact_disagreeing_with_the_dataset_is_a_conflict() -> None:
    metric = MetricPayload.model_validate(
        {
            "metrics": [
                {
                    "metric_id": "price_spread_vs_peer_pct",
                    "calculation_ref": "dw@1",
                    "subject": {"type": "unit", "id": "U011", "label": "A-11.01"},
                    "value": "20.00",
                    "unit": "PCT",
                }
            ]
        }
    )
    c = by_id(t7_candidates(context(healthy(), tasks=TASKS, metric=metric)).candidates)["C-T7-CONFLICT-U011"]
    assert c.dq_flags == ["SOURCE_MISMATCH"]
    assert "ART-METRIC#/metrics/0/value" in c.evidence_refs


def test_legal_cause_on_a_fully_permitted_project_is_a_project_conflict() -> None:
    data = dataset(
        [unit(1)], [inventory(1, 120)], [diagnostic(1, 120, "LEGAL_PERMIT_BARRIER")], [cause(1, "LEGAL_PERMIT_BARRIER")]
    )
    c = by_id(t7_candidates(context(data, tasks=TASKS)).candidates)["C-T7-CONFLICT-PRJ-X"]
    assert c.level == "PROJECT" and c.dq_flags == ["LEGAL_FLAGS_MISMATCH"]
    permitted_not = dataset(
        [unit(1)],
        [inventory(1, 120)],
        [diagnostic(1, 120, "LEGAL_PERMIT_BARRIER")],
        [cause(1, "LEGAL_PERMIT_BARRIER")],
        projects=[project(is_bank_guarantee_issued=False)],
    )
    assert "C-T7-CONFLICT-PRJ-X" not in by_id(t7_candidates(context(permitted_not, tasks=TASKS)).candidates)


def test_requested_market_context_without_an_artifact_is_a_limitation() -> None:
    ctx = context(healthy(), tasks=("T1", "T5", "T7"))
    assert by_id(t7_candidates(ctx).candidates)["C-T7-MARKET_CONTEXT_MISSING"].dq_flags == ["MARKET_CONTEXT_MISSING"]


def test_nothing_without_t7() -> None:
    data = dataset([unit(1)], [inventory(1, 40)])
    assert t7_candidates(context(data, tasks=("T1",))).candidates == []


def test_in_a_zone_or_project_scope_peer_problems_are_one_limitation_each() -> None:
    """The data pack has dozens of constrained units per tower: one limitation per scope, not per unit."""
    found = by_id(t7_candidates(context(peer_problems(extra=30), tasks=TASKS)).candidates)
    assert not [c for c in found if c.startswith("C-T7-PEER_SAMPLE_CONSTRAINED-U")]
    constrained = found["C-T7-PEER_SAMPLE_CONSTRAINED-PRJ-X"]
    assert (constrained.level, constrained.subject.type, constrained.subject.id) == ("PROJECT", "project", "PRJ-X")
    assert constrained.slots["units"].value == 31 and constrained.slots["units"].display == "31 căn"
    assert len(constrained.evidence_refs) == 31 and constrained.dq_flags == ["PEER_SAMPLE_CONSTRAINED"]
    missing = found["C-T7-PEER_DATA_MISSING-PRJ-X"]
    assert missing.slots["units"].value == 1 and missing.dq_flags == ["PEER_DATA_MISSING"]
    zone_scope = scope("ZONE", zone_ids=["ZN-AQUA-01"])
    zone = by_id(t7_candidates(context(peer_problems(extra=3), tasks=TASKS, analysis_scope=zone_scope)).candidates)
    assert zone["C-T7-PEER_SAMPLE_CONSTRAINED-ZN-AQUA-01"].level == "ZONE"
