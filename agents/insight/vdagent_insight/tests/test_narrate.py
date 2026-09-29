"""Narration (steps 7–8): apply the LLM draft, TEMPLATE per failing item, TEMPLATE for everything
when there is no draft (D-32)."""

from __future__ import annotations

from typing import Any

from ..candidates.common import CandidateContext
from ..candidates.t1_unit import t1_candidates
from ..candidates.t7_limitation import t7_candidates
from ..contracts import InsightCandidate, LlmInsightDraft
from ..narrate import check_draft, narrate
from .builders import cause, context, dataset, diagnostic, dq, inventory, unit


def setting() -> tuple[CandidateContext, list[InsightCandidate]]:
    data = dataset(
        [unit(11, unit_code="SAPPHIRE1-16.231")],
        [inventory(11, 145)],
        [diagnostic(11, 145, primary="OVERPRICED_VS_PEER")],
        [cause(11, "OVERPRICED_VS_PEER", 1, "0.600"), cause(11, "LOW_SALES_INCENTIVE", 2, "0.400")],
    )
    ctx = context(data, dq(data_as_of="2026-06-30T00:00:00+07:00"))  # 24 h: fresh; stale limitation below
    return ctx, t1_candidates(ctx).candidates


def draft(*items: dict[str, Any], skipped: list[dict[str, str]] | None = None) -> LlmInsightDraft:
    return LlmInsightDraft.model_validate({"selected": list(items), "skipped": skipped or []})


def good_item(cid: str) -> dict[str, Any]:
    return {
        "candidate_ids": [cid],
        "template": "Căn {{unit}} đã tồn {{dom}}; có khả năng liên quan tới {{cause_label}}.",
        "slots": [{"slot": s, "ref": f"{cid}.{s}"} for s in ("unit", "dom", "cause_label")],
    }


def test_a_valid_draft_is_used_as_written_and_unselected_candidates_are_skipped() -> None:
    ctx, cands = setting()
    first, second = cands
    result = narrate(cands, draft(good_item(first.candidate_id)), ctx.cfg, ctx.view, ctx.request, max_items=12)
    (item,) = result.items
    assert item.source == "LLM" and item.candidate_ids == (first.candidate_id,)
    assert (
        item.claim.rendered_text == "Căn SAPPHIRE1-16.231 đã tồn 145 ngày; có khả năng liên quan tới giá cao hơn nhóm tương đồng."
    )
    assert result.narrative_mode == "LLM" and result.violations == {}
    assert [(r.candidate_id, r.reason_code) for r in result.rejected] == [(second.candidate_id, "LLM_SKIPPED")]


def test_tc12_a_free_number_sends_only_that_item_to_template() -> None:
    ctx, cands = setting()
    first, second = cands
    bad = {**good_item(first.candidate_id), "template": "Căn {{unit}} cao hơn peer 20%, liên quan tới {{cause_label}}."}
    bad["slots"] = [{"slot": s, "ref": f"{first.candidate_id}.{s}"} for s in ("unit", "cause_label")]
    good = good_item(second.candidate_id)
    result = narrate(cands, draft(bad, good), ctx.cfg, ctx.view, ctx.request, max_items=12)
    assert [(i.candidate_ids[0], i.source) for i in result.items] == [
        (first.candidate_id, "TEMPLATE"),
        (second.candidate_id, "LLM"),
    ]
    assert "20%" not in result.items[0].claim.rendered_text and "+12,4%" in result.items[0].claim.rendered_text
    assert {v.code for v in result.violations[0]} >= {"E10"}
    assert result.narrative_mode == "TEMPLATE"


def test_check_draft_reports_violations_by_item_for_the_repair_call() -> None:
    ctx, cands = setting()
    first = cands[0]
    bad = {**good_item(first.candidate_id), "template": "Căn {{unit}} chắc chắn do {{cause_label}}, tồn {{dom}}."}
    found = check_draft(draft(bad), {c.candidate_id: c for c in cands}, ctx.cfg, ctx.request)
    assert list(found) == [0] and "E12" in {v.code for v in found[0]}


def test_mixing_insight_types_or_reusing_a_candidate_is_rejected() -> None:
    ctx, cands = setting()
    first = cands[0]
    stale = context(ctx.view.dataset, dq(data_as_of="2026-06-27T23:00:00+07:00"))
    (limitation,) = [c for c in t7_candidates(stale).candidates]
    mixed = good_item(first.candidate_id) | {
        "candidate_ids": [first.candidate_id, limitation.candidate_id],
    }
    found = check_draft(
        draft(mixed, good_item(first.candidate_id)), {c.candidate_id: c for c in [*cands, limitation]}, ctx.cfg, ctx.request
    )
    assert "E11" in {v.code for v in found[0]} and "E11" in {v.code for v in found[1]}


def test_without_a_draft_everything_is_template_and_limitations_are_always_kept() -> None:
    ctx, _ = setting()
    stale = context(ctx.view.dataset, dq(data_as_of="2026-06-27T23:00:00+07:00"))
    all_cands = [*t1_candidates(stale).candidates, *t7_candidates(stale).candidates]
    result = narrate(all_cands, None, stale.cfg, stale.view, stale.request, max_items=1)
    assert [(i.candidate_ids[0].split("-")[1], i.source) for i in result.items] == [("T1", "TEMPLATE"), ("T7", "TEMPLATE")]
    assert result.narrative_mode == "TEMPLATE"
    assert [r.reason_code for r in result.rejected] == ["SELECTION_LIMIT"]


def test_a_limitation_the_model_ignored_is_still_reported() -> None:
    ctx, _ = setting()
    stale = context(ctx.view.dataset, dq(data_as_of="2026-06-27T23:00:00+07:00"))
    cands = [*t1_candidates(stale).candidates, *t7_candidates(stale).candidates]
    first = cands[0]
    result = narrate(cands, draft(good_item(first.candidate_id)), stale.cfg, stale.view, stale.request, max_items=12)
    assert [(i.candidate_ids[0], i.source) for i in result.items][-1] == ("C-T7-STALE_SNAPSHOT", "TEMPLATE")
    assert result.narrative_mode == "LLM"
