"""T2 Cause Distribution (spec §6.2, BR-05; coverage and n_eff from the gate)."""

from __future__ import annotations

from decimal import Decimal

from ..candidates.t2_distribution import cause_distribution, t2_candidates
from ..contracts import DatasetPayload
from .builders import cause, context, dataset, diagnostic, inventory, scope, semantic, unit, with_params

ZONE = scope("ZONE", zone_ids=["ZN-AQUA-01"])
TASKS = ("T2", "T6", "T7")


def zone_units(overdue: int, diagnosed: int, causes_of: dict[int, list[tuple[str, str]]]) -> DatasetPayload:
    """`overdue` overdue units (DOM 100 + i); the first `diagnosed` have mart + bridge rows."""
    units = [unit(i) for i in range(1, overdue + 1)]
    inv = [inventory(i, 100 + i) for i in range(1, overdue + 1)]
    diags, rows = [], []
    for i in range(1, diagnosed + 1):
        spec = causes_of.get(i, [("OVERPRICED_VS_PEER", "1.000")])
        diags.append(diagnostic(i, 100 + i, spec[0][0]))
        rows += [cause(i, code, rank, score) for rank, (code, score) in enumerate(spec, start=1)]
    return dataset(units, inv, diags, rows)


def tc03_dataset() -> DatasetPayload:
    """40 overdue units: 20 OVERPRICED only, 10 LOW_SALES only, 10 OVERPRICED 0.6 + DEEP_FUNNEL 0.4."""
    causes_of: dict[int, list[tuple[str, str]]] = {}
    for i in range(21, 31):
        causes_of[i] = [("LOW_SALES_INCENTIVE", "1.000")]
    for i in range(31, 41):
        causes_of[i] = [("OVERPRICED_VS_PEER", "0.600"), ("DEEP_FUNNEL_DROP_OFF", "0.400")]
    return zone_units(40, 40, causes_of)


def test_tc03_both_counting_methods_and_weighted_shares_sum_to_100() -> None:
    ctx = context(tc03_dataset(), tasks=TASKS, analysis_scope=ZONE)
    shares = cause_distribution(ctx, ctx.gate.scope("ZONE", "ZN-AQUA-01"))
    assert sum(s.weighted_share_pct for s in shares) == Decimal(100)
    by_code = {s.cause_code: s for s in shares}
    assert by_code["OVERPRICED_VS_PEER"].weighted_share_pct == Decimal(65)  # (20 + 10×0.6) / 40
    assert by_code["OVERPRICED_VS_PEER"].unit_share_pct == Decimal(75)  # 30 of 40 units at any rank
    assert by_code["DEEP_FUNNEL_DROP_OFF"].weighted_share_pct == Decimal(10)
    assert by_code["DEEP_FUNNEL_DROP_OFF"].unit_share_pct == Decimal(25)

    batch = t2_candidates(ctx)
    assert [c.cause_code for c in batch.candidates] == ["OVERPRICED_VS_PEER", "LOW_SALES_INCENTIVE", "DEEP_FUNNEL_DROP_OFF"]
    top = batch.candidates[0]
    assert top.candidate_id == "C-T2-ZN-AQUA-01-OVERPRICED_VS_PEER"
    assert (top.task, top.insight_type, top.level) == ("T2", "CAUSE_DISTRIBUTION", "ZONE")
    assert (top.subject.type, top.subject.id, top.subject.label) == ("zone", "ZN-AQUA-01", "Tòa Aqua 1")
    assert (top.slots["weighted_share"].display, top.slots["unit_share"].display) == ("65%", "75%")
    assert (top.slots["units_with_cause"].value, top.slots["units_diagnosed"].value, top.slots["overdue_units"].value) == (
        30,
        40,
        40,
    )
    assert top.slots["weighted_share"].metric_ref == "insight_candidates#/C-T2-ZN-AQUA-01-OVERPRICED_VS_PEER/slots/weighted_share"
    assert (top.n_eff, top.coverage, top.confidence, top.priority) == (40, Decimal(100), "HIGH", Decimal("0.5"))
    assert top.action_code == "TARGETED_PRICE_CORRECTION"
    assert len(top.evidence_refs) == 30 and all("#/unit_diagnostic_causes/" in r for r in top.evidence_refs)


def test_causes_below_the_minimum_share_are_rejected() -> None:
    causes_of = {1: [("OVERPRICED_VS_PEER", "0.800"), ("DEEP_FUNNEL_DROP_OFF", "0.200")]}
    ctx = context(zone_units(10, 10, causes_of), tasks=TASKS, analysis_scope=ZONE)
    batch = t2_candidates(ctx)  # DEEP_FUNNEL: 0.2 / 10 = 2 % < 5 %
    assert [c.cause_code for c in batch.candidates] == ["OVERPRICED_VS_PEER"]
    assert [(r.candidate_id, r.reason_code) for r in batch.rejected] == [
        ("C-T2-ZN-AQUA-01-DEEP_FUNNEL_DROP_OFF", "CAUSE_SHARE_TOO_SMALL")
    ]


def test_tc21_partial_coverage_states_the_denominator_and_downgrades_once() -> None:
    ctx = context(zone_units(40, 28, {}), tasks=TASKS, analysis_scope=ZONE)
    (c,) = t2_candidates(ctx).candidates
    assert (c.slots["units_diagnosed"].display, c.slots["overdue_units"].display) == ("28 căn", "40 căn")
    assert c.coverage == Decimal(70) and c.confidence == "MEDIUM"
    assert "PARTIAL_COVERAGE" in c.dq_flags


def test_insufficient_coverage_means_no_conclusion_at_that_level() -> None:
    batch = t2_candidates(context(zone_units(40, 19, {}), tasks=TASKS, analysis_scope=ZONE))
    assert batch.candidates == []
    assert [(r.candidate_id, r.reason_code) for r in batch.rejected] == [("C-T2-ZN-AQUA-01", "INSUFFICIENT_COVERAGE")]


def test_low_coverage_is_describe_only() -> None:
    (c,) = t2_candidates(context(zone_units(40, 24, {}), tasks=TASKS, analysis_scope=ZONE)).candidates
    assert "LOW_COVERAGE" in c.dq_flags and c.significant is False


def test_small_effective_samples_are_kept_but_describe_only() -> None:
    """D-71: only T3 drops a too-small group; T2 keeps it flagged."""
    (few,) = t2_candidates(context(zone_units(4, 4, {}), tasks=TASKS, analysis_scope=ZONE)).candidates
    assert "SMALL_SAMPLE" in few.dq_flags and few.significant is False
    (tiny,) = t2_candidates(context(zone_units(2, 2, {}), tasks=TASKS, analysis_scope=ZONE)).candidates
    assert "GROUP_TOO_SMALL" in tiny.dq_flags


def test_project_level_scope_gives_project_distributions() -> None:
    batch = t2_candidates(context(zone_units(10, 10, {}), tasks=TASKS, analysis_scope=scope("PROJECT", project_ids=["PRJ-X"])))
    assert [(c.level, c.subject.id) for c in batch.candidates] == [("PROJECT", "PRJ-X")]


def test_no_t2_for_a_unit_scope_or_when_not_requested() -> None:
    data = zone_units(10, 10, {})
    assert t2_candidates(context(data, tasks=TASKS, analysis_scope=scope("UNIT", unit_ids=["U001"]))).candidates == []
    assert t2_candidates(context(data, tasks=("T1",), analysis_scope=ZONE)).candidates == []


def test_unknown_cause_codes_count_in_the_total_but_get_no_candidate() -> None:
    causes_of = {i: [("BAD_LOCATION", "1.000")] for i in range(1, 6)}
    ctx = context(zone_units(10, 10, causes_of), tasks=TASKS, analysis_scope=ZONE)
    batch = t2_candidates(ctx)
    assert [c.cause_code for c in batch.candidates] == ["OVERPRICED_VS_PEER"]
    assert batch.candidates[0].slots["weighted_share"].value == Decimal(50)
    assert ("C-T2-ZN-AQUA-01-BAD_LOCATION", "CAUSE_CODE_NOT_ALLOWED") in [(r.candidate_id, r.reason_code) for r in batch.rejected]


def test_min_share_comes_from_config() -> None:
    causes_of = {1: [("OVERPRICED_VS_PEER", "0.800"), ("DEEP_FUNNEL_DROP_OFF", "0.200")]}
    cfg = with_params(semantic(), min_cause_share_pct=Decimal(1))
    batch = t2_candidates(context(zone_units(10, 10, causes_of), tasks=TASKS, analysis_scope=ZONE, cfg=cfg))
    assert [c.cause_code for c in batch.candidates] == ["OVERPRICED_VS_PEER", "DEEP_FUNNEL_DROP_OFF"]
