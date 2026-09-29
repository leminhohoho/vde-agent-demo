"""T1 Unit Diagnosis (spec §6.2, BR-01/02/03/04/06/07, E06)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..candidates.t1_unit import t1_candidates
from ..contracts import CauseRow, DatasetPayload
from .builders import (
    cause,
    context,
    dataset,
    diagnostic,
    dq,
    dq_field,
    inventory,
    llm,
    project,
    scope,
    semantic,
    unit,
    with_params,
)

MART = "dm_unit_friction_diagnostics"


def single_unit(
    primary: str = "OVERPRICED_VS_PEER", causes: list[CauseRow] | None = None, dom: int = 145, **diag_kw: Any
) -> DatasetPayload:
    units = [unit(11, unit_code="A-05.03")]
    inv = [inventory(11, dom)]
    diags = [diagnostic(11, dom, primary, unit_code="A-05.03", **diag_kw)]
    return dataset(units, inv, diags, causes if causes is not None else [cause(11, primary)])


def test_tc01_one_overpriced_candidate_with_its_numbers_evidence_and_action() -> None:
    batch = t1_candidates(context(single_unit()))
    assert batch.rejected == []
    (c,) = batch.candidates
    assert c.candidate_id == "C-T1-U011-1-OVERPRICED_VS_PEER"
    assert (c.task, c.insight_type, c.level, c.cause_code) == ("T1", "ROOT_CAUSE_SIGNAL", "UNIT", "OVERPRICED_VS_PEER")
    assert (c.subject.type, c.subject.id, c.subject.label) == ("unit", "U011", "A-05.03")
    assert (c.slots["dom"].value, c.slots["dom"].unit, c.slots["dom"].display) == (Decimal(145), "DAY", "145 ngày")
    assert c.slots["dom"].metric_ref == "ART-DATASET#/fact_unit_inventory_snapshot/0/unsold_days_dom"
    spread = c.slots["spread"]
    assert (spread.value, spread.unit, spread.display) == (Decimal("12.40"), "PCT", "+12,4%")
    assert spread.metric_ref == f"ART-DATASET#/{MART}/0/price_spread_vs_peer_pct"
    assert c.slots["peers"].value == 12
    assert f"ART-DATASET#/{MART}/0" in c.evidence_refs
    assert "ART-DATASET#/unit_diagnostic_causes/0" in c.evidence_refs
    assert f"{MART}@SNAP-20260630-01" in c.lineage.source_refs and c.lineage.calculation_refs
    assert (c.severity_rank, c.attribution_score, c.priority) == (1, Decimal("1.000"), Decimal("1.000"))
    assert c.action_code == "TARGETED_PRICE_CORRECTION"
    assert (c.confidence, c.significant, c.dq_flags) == ("HIGH", True, [])


def test_tc02_three_causes_follow_severity_rank_and_sum_to_one() -> None:
    causes = [
        cause(11, "LOW_SALES_INCENTIVE", 2, "0.300"),
        cause(11, "OVERPRICED_VS_PEER", 1, "0.500"),
        cause(11, "DEEP_FUNNEL_DROP_OFF", 3, "0.200"),
    ]
    batch = t1_candidates(context(single_unit(causes=causes)))
    assert [(c.severity_rank, c.cause_code) for c in batch.candidates] == [
        (1, "OVERPRICED_VS_PEER"),
        (2, "LOW_SALES_INCENTIVE"),
        (3, "DEEP_FUNNEL_DROP_OFF"),
    ]
    assert sum(c.attribution_score or 0 for c in batch.candidates) == Decimal("1.000")
    assert [c.priority for c in batch.candidates] == [Decimal("0.5"), Decimal("0.15"), Decimal("0.2") / 3]
    assert all("CONFLICT" not in c.dq_flags for c in batch.candidates)


def test_tc05_br01_no_diagnosis_for_boundary_sold_or_booked_units() -> None:
    units = [unit(1), unit(2), unit(3)]
    inv = [inventory(1, 90), inventory(2, 150, "SOLD"), inventory(3, 120, "BOOKED")]
    diags = [diagnostic(i, d) for i, d in ((1, 90), (2, 150), (3, 120))]
    causes = [cause(i, "OVERPRICED_VS_PEER") for i in (1, 2, 3)]
    assert t1_candidates(context(dataset(units, inv, diags, causes))).candidates == []


def test_tc06_threshold_comes_from_the_config_version() -> None:
    data = single_unit(dom=75)
    assert t1_candidates(context(data)).candidates == []
    lower = with_params(semantic(), overdue_threshold_days=60)
    assert len(t1_candidates(context(data, cfg=lower)).candidates) == 1


def test_br02_unknown_cause_codes_are_rejected() -> None:
    batch = t1_candidates(context(single_unit(primary="BAD_LOCATION")))
    assert batch.candidates == []
    assert [(r.candidate_id, r.reason_code) for r in batch.rejected] == [("C-T1-U011-1-BAD_LOCATION", "CAUSE_CODE_NOT_ALLOWED")]


def test_br03_primary_cause_differing_from_rank_one_flags_conflict() -> None:
    causes = [cause(11, "LOW_SALES_INCENTIVE", 1, "1.000")]
    (c,) = t1_candidates(context(single_unit(primary="OVERPRICED_VS_PEER", causes=causes))).candidates
    assert "CONFLICT" in c.dq_flags


def test_br04_scores_not_summing_to_one_flag_conflict() -> None:
    causes = [cause(11, "OVERPRICED_VS_PEER", 1, "0.600")]
    (c,) = t1_candidates(context(single_unit(causes=causes))).candidates
    assert "CONFLICT" in c.dq_flags


def test_e06_missing_peer_spread_rejects_the_peer_price_candidate() -> None:
    batch = t1_candidates(context(single_unit(price_spread_vs_peer_pct=None)))
    assert batch.candidates == []
    assert [r.reason_code for r in batch.rejected] == ["PEER_DATA_MISSING"]


def test_missing_required_evidence_rejects_the_candidate() -> None:
    batch = t1_candidates(context(single_unit(primary="DEEP_FUNNEL_DROP_OFF", funnel_dropoff_rate_pct=None)))
    assert [r.reason_code for r in batch.rejected] == ["EVIDENCE_FIELD_MISSING"]


def test_dq_fail_on_a_primary_field_rejects_and_warn_makes_it_low() -> None:
    fail = dq([dq_field(MART, "price_spread_vs_peer_pct", "FAIL")])
    batch = t1_candidates(context(single_unit(), fail))
    assert batch.candidates == [] and [r.reason_code for r in batch.rejected] == ["EVIDENCE_FIELD_MISSING"]
    warn = dq([dq_field(MART, "price_spread_vs_peer_pct", "WARN")])
    (c,) = t1_candidates(context(single_unit(), warn)).candidates
    assert c.confidence == "LOW" and "DQ_WARN" in c.dq_flags


def test_br07_peer_tiers_and_constrained_sample() -> None:
    (constrained,) = t1_candidates(context(single_unit(is_peer_sample_constrained=True))).candidates
    assert constrained.confidence == "MEDIUM" and "PEER_SAMPLE_CONSTRAINED" in constrained.dq_flags
    assert constrained.significant is False
    (enough,) = t1_candidates(context(single_unit(peer_count=5))).candidates
    assert enough.significant is True and not {"SMALL_SAMPLE", "GROUP_TOO_SMALL"} & set(enough.dq_flags)
    (few,) = t1_candidates(context(single_unit(peer_count=4))).candidates
    assert "SMALL_SAMPLE" in few.dq_flags and few.significant is False
    (tiny,) = t1_candidates(context(single_unit(peer_count=2))).candidates  # kept: only T3 drops (D-71)
    assert "GROUP_TOO_SMALL" in tiny.dq_flags and tiny.significant is False


def test_causes_without_a_peer_comparison_are_not_significant() -> None:
    (c,) = t1_candidates(context(single_unit(primary="DEEP_FUNNEL_DROP_OFF"))).candidates
    assert c.significant is False and c.slots["dropoff"].value == Decimal("20.00")


def test_evidence_from_a_single_table_caps_confidence_at_medium() -> None:
    (c,) = t1_candidates(context(single_unit(primary="LOW_SALES_INCENTIVE"))).candidates
    assert c.confidence == "MEDIUM"
    assert set(c.slots) == {"dom", "commission", "spiff"}


def test_excluded_secondary_field_is_left_out_of_the_slots() -> None:
    excluded = dq([dq_field("fact_unit_inventory_snapshot", "spiff_bonus_vnd", "WARN", primary=False, missing="45")])
    (c,) = t1_candidates(context(single_unit(primary="LOW_SALES_INCENTIVE"), excluded)).candidates
    assert "spiff" not in c.slots


def test_no_action_code_without_t6() -> None:
    (c,) = t1_candidates(context(single_unit(), tasks=("T1", "T7"))).candidates
    assert c.action_code is None


def test_no_candidates_when_t1_is_not_requested() -> None:
    assert t1_candidates(context(single_unit(), tasks=("T3",))).candidates == []


def test_br06_legal_barrier_is_reported_once_at_project_level() -> None:
    units = [unit(1), unit(2), unit(3)]
    inv = [inventory(1, 120), inventory(2, 130), inventory(3, 40)]
    diags = [diagnostic(1, 120, "LEGAL_PERMIT_BARRIER"), diagnostic(2, 130, "LEGAL_PERMIT_BARRIER")]
    causes = [cause(1, "LEGAL_PERMIT_BARRIER"), cause(2, "LEGAL_PERMIT_BARRIER")]
    data = dataset(units, inv, diags, causes, projects=[project(is_sales_permit_issued=False)])
    (c,) = t1_candidates(context(data)).candidates
    assert c.candidate_id == "C-T1-PRJ-X-LEGAL_PERMIT_BARRIER"
    assert (c.level, c.subject.type, c.subject.id, c.cause_code) == ("PROJECT", "project", "PRJ-X", "LEGAL_PERMIT_BARRIER")
    assert (c.slots["overdue_units"].value, c.slots["overdue_units"].display) == (Decimal(2), "2 căn")
    assert "ART-DATASET#/dim_project_profile/0/is_sales_permit_issued" in c.evidence_refs
    assert c.action_code == "EXPEDITE_LEGAL_PROCEDURES" and "CONFLICT" not in c.dq_flags


def test_legal_cause_on_a_fully_permitted_project_is_a_conflict() -> None:
    units = [unit(1)]
    data = dataset(units, [inventory(1, 120)], [diagnostic(1, 120, "LEGAL_PERMIT_BARRIER")], [cause(1, "LEGAL_PERMIT_BARRIER")])
    (c,) = t1_candidates(context(data)).candidates
    assert "CONFLICT" in c.dq_flags


def test_many_units_are_grouped_by_primary_cause_keeping_three_examples_each() -> None:
    ids = range(1, 7)
    units = [unit(i) for i in ids]
    inv = [inventory(i, 100 + 10 * i) for i in ids]
    primaries = {1: "LOW_SALES_INCENTIVE", 2: "LOW_SALES_INCENTIVE"} | dict.fromkeys((3, 4, 5, 6), "OVERPRICED_VS_PEER")
    diags = [diagnostic(i, 100 + 10 * i, primaries[i]) for i in ids]
    causes = [cause(i, primaries[i]) for i in ids]
    cfg = with_params(semantic(), max_units_in_context=4)
    batch = t1_candidates(context(dataset(units, inv, diags, causes), cfg=cfg))
    assert sorted(c.subject.id for c in batch.candidates) == ["U001", "U002", "U004", "U005", "U006"]
    assert [(r.candidate_id, r.reason_code) for r in batch.rejected] == [("C-T1-U003-1-OVERPRICED_VS_PEER", "UNITS_GROUPED")]


def test_recently_discussed_subject_is_boosted() -> None:
    (c,) = t1_candidates(context(single_unit(), recent_subject_ids=frozenset({"U011"}))).candidates
    assert c.priority == Decimal("1.000") + llm().memory.recent_subject_boost


def test_units_outside_the_scope_are_not_diagnosed() -> None:
    data = single_unit()
    assert t1_candidates(context(data, analysis_scope=scope("UNIT", unit_ids=["U999"]))).candidates == []


def test_an_outlying_dom_in_the_zone_is_flagged_but_kept() -> None:
    doms = {1: 100, 2: 101, 3: 102, 4: 103, 5: 400}
    units = [unit(i) for i in doms]
    inv = [inventory(i, d) for i, d in doms.items()]
    diags = [diagnostic(i, d) for i, d in doms.items()]
    causes = [cause(i, "OVERPRICED_VS_PEER") for i in doms]
    batch = t1_candidates(context(dataset(units, inv, diags, causes)))
    flagged = {c.subject.id: [f for f in c.dq_flags if f.startswith("OUTLIER")] for c in batch.candidates}
    assert flagged == {"U001": [], "U002": [], "U003": [], "U004": [], "U005": ["OUTLIER_EXCLUDED"]}
