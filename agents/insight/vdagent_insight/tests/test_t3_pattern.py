"""T3 Pattern Detection (spec §6.2, BR-08, §5.5 significant flag)."""

from __future__ import annotations

from decimal import Decimal

from ..candidates.t3_pattern import t3_candidates
from ..contracts import DatasetPayload, InsightCandidate
from .builders import context, dataset, inventory, scope, semantic, unit

TASKS = ("T3", "T7")
PROJECT = scope("PROJECT", project_ids=["PRJ-X"])


def orientation_dataset(groups: dict[str, list[int]], sold: int = 0) -> DatasetPayload:
    """AVAILABLE units by balcony orientation with the given DOMs, plus `sold` SOLD units facing E."""
    units, inv = [], []
    i = 0
    for orientation, doms in groups.items():
        for dom in doms:
            i += 1
            units.append(unit(i, balcony_orientation=orientation))
            inv.append(inventory(i, dom))
    for _ in range(sold):
        i += 1
        units.append(unit(i, balcony_orientation="E"))
        inv.append(inventory(i, 400, "SOLD"))
    return dataset(units, inv)


def by_id(batch_candidates: list[InsightCandidate]) -> dict[str, InsightCandidate]:
    return {c.candidate_id: c for c in batch_candidates}


def test_tc19_west_pattern_is_reported_and_the_tiny_ne_group_is_rejected() -> None:
    data = orientation_dataset({"W": list(range(150, 200, 2)), "E": list(range(30, 60, 2)), "NE": [100, 110]})
    batch = t3_candidates(context(data, tasks=TASKS, analysis_scope=PROJECT))
    c = by_id(batch.candidates)["C-T3-balcony_orientation-W"]
    assert (c.task, c.insight_type, c.level) == ("T3", "PATTERN", "PROJECT")
    assert (c.subject.type, c.subject.id, c.subject.label) == ("group", "balcony_orientation=W", "W")
    assert c.slots["group_units"].value == 25 and c.slots["rest_units"].value == 17
    assert c.slots["group_dom"].value == Decimal(174) and c.slots["group_dom"].unit == "DAY"
    assert c.slots["group_overdue_rate"].value == Decimal(100)
    assert c.significant is True and c.cause_code is None and c.action_code is None
    assert c.priority == Decimal("0.5")
    assert c.slots["group_dom"].metric_ref == "insight_candidates#/C-T3-balcony_orientation-W/slots/group_dom"
    assert ("C-T3-balcony_orientation-NE", "GROUP_TOO_SMALL") in [(r.candidate_id, r.reason_code) for r in batch.rejected]


def test_tc22_overlapping_intervals_give_a_pattern_that_is_not_significant() -> None:
    west = [10, 30, 60, 90, 110, 130, 150, 170, 200, 240, 280, 320]
    east = [5, 20, 40, 70, 90, 110, 120, 140, 160, 200, 230, 260]
    batch = t3_candidates(context(orientation_dataset({"W": west, "E": east}), tasks=TASKS, analysis_scope=PROJECT))
    c = by_id(batch.candidates)["C-T3-balcony_orientation-W"]
    assert c.significant is False
    assert c.slots["group_units"].value == 12 and "SMALL_SAMPLE" not in c.dq_flags


def test_br08_small_effect_is_rejected() -> None:
    data = orientation_dataset({"W": [100 + i for i in range(12)], "E": [95 + i for i in range(12)]})
    batch = t3_candidates(context(data, tasks=TASKS, analysis_scope=PROJECT))
    assert batch.candidates == []
    reasons = {(r.candidate_id, r.reason_code) for r in batch.rejected}
    assert ("C-T3-balcony_orientation-W", "EFFECT_TOO_SMALL") in reasons


def test_groups_of_three_or_four_are_describe_only_and_five_may_compare() -> None:
    four = orientation_dataset({"W": [200, 210, 220, 230], "E": list(range(20, 60, 2))})
    c = by_id(t3_candidates(context(four, tasks=TASKS, analysis_scope=PROJECT)).candidates)["C-T3-balcony_orientation-W"]
    assert "SMALL_SAMPLE" in c.dq_flags and c.significant is False
    five = orientation_dataset({"W": [200, 210, 220, 230, 240], "E": list(range(20, 60, 2))})
    c = by_id(t3_candidates(context(five, tasks=TASKS, analysis_scope=PROJECT)).candidates)["C-T3-balcony_orientation-W"]
    assert "SMALL_SAMPLE" not in c.dq_flags


def test_groups_under_three_are_dropped() -> None:
    data = orientation_dataset({"W": [70, 72], "E": list(range(20, 60, 2))})
    batch = t3_candidates(context(data, tasks=TASKS, analysis_scope=PROJECT))
    assert ("C-T3-balcony_orientation-W", "GROUP_TOO_SMALL") in [(r.candidate_id, r.reason_code) for r in batch.rejected]


def test_only_unsold_inventory_is_compared() -> None:
    data = orientation_dataset({"W": list(range(150, 200, 2)), "E": list(range(30, 60, 2))}, sold=10)
    c = by_id(t3_candidates(context(data, tasks=TASKS, analysis_scope=PROJECT)).candidates)["C-T3-balcony_orientation-E"]
    assert c.slots["group_units"].value == 15


def test_a_dimension_with_a_single_group_gives_nothing() -> None:
    data = orientation_dataset({"W": list(range(150, 200, 2)), "E": list(range(30, 60, 2))})
    batch = t3_candidates(context(data, tasks=TASKS, analysis_scope=PROJECT))
    assert all(c.candidate_id.startswith("C-T3-balcony_orientation-") for c in batch.candidates)
    assert all("floor_band" not in r.candidate_id for r in batch.rejected)


def test_dimensions_come_from_config() -> None:
    assert [d.name for d in semantic().pattern_dimensions] == [
        "floor_band",
        "balcony_orientation",
        "view_primary_type",
        "unit_type",
        "launch_batch_id",
        "channel_key",
    ]


def test_not_requested_means_no_patterns() -> None:
    data = orientation_dataset({"W": list(range(150, 200, 2)), "E": list(range(30, 60, 2))})
    assert t3_candidates(context(data, tasks=("T1",), analysis_scope=PROJECT)).candidates == []
