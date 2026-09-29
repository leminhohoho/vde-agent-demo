"""Candidate priority and the context-budget cut (spec §6.4 steps 1–2, §9.5 recent_subject_boost)."""

from __future__ import annotations

from decimal import Decimal

from ..candidates.priority import base_priority, boosted, select_for_context
from .builders import candidate, semantic

CFG = semantic()


def test_priority_is_attribution_over_rank_else_the_configured_default() -> None:
    p = CFG.params
    assert base_priority("T1", Decimal("0.600"), 1, p) == Decimal("0.6")
    assert base_priority("T1", Decimal("0.300"), 2, p) == Decimal("0.15")
    assert base_priority("T2", None, None, p) == Decimal("0.5")
    assert base_priority("T3", None, None, p) == Decimal("0.5")
    assert base_priority("T5", None, None, p) == Decimal("0.3")
    assert base_priority("T7", None, None, p) == Decimal("0")


def test_subjects_discussed_recently_get_the_memory_boost() -> None:
    boost = Decimal("0.1")
    assert boosted(Decimal("0.5"), "U011", frozenset({"U011"}), boost) == Decimal("0.6")
    assert boosted(Decimal("0.5"), "U012", frozenset({"U011"}), boost) == Decimal("0.5")


def test_tc24_cut_keeps_every_t7_and_rejects_the_rest_for_context_budget() -> None:
    cands = [candidate(f"C-T1-{i:02d}", "T1", f"0.{i:02d}") for i in range(72)]
    cands += [candidate(f"C-T7-{i}", "T7", "0") for i in range(3)]
    kept, rejected = select_for_context(cands, 40)
    assert len(kept) == 40 and len(rejected) == 35
    assert {c.candidate_id for c in kept if c.task == "T7"} == {"C-T7-0", "C-T7-1", "C-T7-2"}
    assert all(r.reason_code == "CONTEXT_BUDGET" for r in rejected)
    kept_t1 = [c.priority for c in kept if c.task == "T1"]
    assert min(kept_t1) > max(Decimal(f"0.{int(r.candidate_id[-2:]):02d}") for r in rejected)


def test_cut_order_is_deterministic_on_ties() -> None:
    cands = [candidate(f"C-{c}", "T1", "0.5") for c in "dbca"]
    kept, rejected = select_for_context(cands, 2)
    assert [c.candidate_id for c in kept] == ["C-a", "C-b"]
    assert [r.candidate_id for r in rejected] == ["C-c", "C-d"]


def test_nothing_is_cut_under_the_budget() -> None:
    cands = [candidate("C-1", "T1", "0.9"), candidate("C-2", "T7", "0")]
    kept, rejected = select_for_context(cands, 40)
    assert [c.candidate_id for c in kept] == ["C-1", "C-2"] and rejected == []


def test_in_a_zone_or_project_scope_candidates_of_that_level_come_first() -> None:
    unit = candidate("C-T1-U001", "T1", "0.9")
    zone = candidate("C-T2-Z", "T2", "0.5").model_copy(update={"level": "ZONE"})
    kept, rejected = select_for_context([unit, zone], 1, scope_level="ZONE")
    assert [c.candidate_id for c in kept] == ["C-T2-Z"] and [r.candidate_id for r in rejected] == ["C-T1-U001"]
    kept, _ = select_for_context([unit, zone], 2, scope_level="ZONE")
    assert [c.candidate_id for c in kept] == ["C-T2-Z", "C-T1-U001"]
    kept, _ = select_for_context([unit, zone], 1)  # a unit scope: priority only
    assert [c.candidate_id for c in kept] == ["C-T1-U001"]
