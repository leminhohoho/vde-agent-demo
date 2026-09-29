"""Confidence, KEY, status and the payload (pipeline step 9, spec §5.2, §4.2, §4.4; D-27..D-33)."""

from __future__ import annotations

from typing import Any

from ..assess import Assessment, assess
from ..candidates import CandidateBatch, generate_candidates
from ..candidates.common import CandidateContext
from ..contracts import DatasetPayload, LlmInsightDraft
from ..narrate import narrate
from .builders import cause, context, dataset, diagnostic, dq, dq_field, inventory, project, request, unit, with_params


def run(ctx: CandidateContext, draft: LlmInsightDraft | None = None, **kw: Any) -> tuple[CandidateBatch, Assessment]:
    batch = generate_candidates(ctx, max_candidates=40)
    narration = narrate(batch.candidates, draft, ctx.cfg, ctx.view, ctx.request, max_items=12)
    return batch, assess(ctx, batch.candidates, batch.rejected, narration, **kw)


def overpriced_ctx(**diag_kw: Any) -> CandidateContext:
    data = dataset(
        [unit(11, unit_code="SAPPHIRE1-16.231")],
        [inventory(11, 145)],
        [diagnostic(11, 145, **diag_kw)],
        [cause(11, "OVERPRICED_VS_PEER")],
    )
    return context(data)


def llm_draft(cid: str, **extra: Any) -> LlmInsightDraft:
    item = {
        "candidate_ids": [cid],
        "template": "Căn {{unit}} đã tồn {{dom}}; có khả năng liên quan tới {{cause_label}}.",
        "slots": [{"slot": s, "ref": f"{cid}.{s}"} for s in ("unit", "dom", "cause_label")],
        **extra,
    }
    return LlmInsightDraft.model_validate({"selected": [item], "skipped": []})


def test_a_well_evidenced_root_cause_is_key_with_a_suggestion() -> None:
    ctx = overpriced_ctx()
    cid = "C-T1-U011-1-OVERPRICED_VS_PEER"
    _, result = run(ctx, llm_draft(cid))
    (ins,) = result.payload.insights
    assert (ins.insight_id, ins.candidate_id, ins.insight_type, ins.cause_code) == (
        "INS-001",
        cid,
        "ROOT_CAUSE_SIGNAL",
        "OVERPRICED_VS_PEER",
    )
    assert ins.claim.rendered_text.startswith("Căn SAPPHIRE1-16.231 đã tồn 145 ngày")
    assert (ins.materiality, ins.eligible_for_conclusion) == ("KEY", True)
    assert ins.confidence.level == "HIGH" and "TWO_INDEPENDENT_EVIDENCE" in ins.confidence.reasons
    assert {e.kind for e in ins.evidence_refs} == {"DIAGNOSTIC_ROW"}
    assert [e.evidence_id for e in ins.evidence_refs] == [f"EV-001-{i}" for i in range(1, len(ins.evidence_refs) + 1)]
    assert ins.lineage.source_refs and ins.lineage.calculation_refs and ins.lineage.peer_rule_ref == "dw.peer_group@3.1.0"
    assert ins.recommendation is not None and ins.recommendation.action_code == "TARGETED_PRICE_CORRECTION"
    assert ins.recommendation.text == ctx.cfg.cause("OVERPRICED_VS_PEER").recommendation_text
    assert result.payload.summary.headline_insight_ids == ["INS-001"]
    assert result.payload.summary.narrative_mode == "LLM" and result.status == "VALID"
    (hint,) = result.payload.chart_hints
    assert (hint.insight_id, hint.suggested_chart) == ("INS-001", "kpi_card") and hint.metric_refs


def test_the_model_recommendation_is_used_when_it_passed_validation() -> None:
    rec = "Có thể cân nhắc rà soát đơn giá niêm yết của căn so với nhóm tương đồng."
    _, result = run(overpriced_ctx(), llm_draft("C-T1-U011-1-OVERPRICED_VS_PEER", recommendation_text=rec))
    (ins,) = result.payload.insights
    assert ins.recommendation is not None and ins.recommendation.text == rec


def test_template_mode_makes_the_artifact_partial() -> None:
    _, result = run(overpriced_ctx())
    assert result.payload.summary.narrative_mode == "TEMPLATE" and result.status == "PARTIAL"


def test_recommendations_are_left_out_on_user_preference_and_for_metric_lookups() -> None:
    _, hidden = run(overpriced_ctx(), show_recommendation=False)
    assert hidden.payload.insights[0].recommendation is None
    ctx = overpriced_ctx()
    lookup = CandidateContext(**{**ctx.__dict__, "request": request(intent="PERFORMANCE_METRIC_LOOKUP")})
    _, result = run(lookup)
    assert result.payload.insights[0].recommendation is None


def test_conflicts_block_key_and_are_linked() -> None:
    data = dataset([unit(11)], [inventory(11, 145)], [diagnostic(11, 145)], [cause(11, "LOW_SALES_INCENTIVE", 1, "1.000")])
    _, result = run(context(data))
    by_candidate = {i.candidate_id: i for i in result.payload.insights}
    root = by_candidate["C-T1-U011-1-LOW_SALES_INCENTIVE"]
    conflict = by_candidate["C-T7-CONFLICT-U011"]
    assert (root.materiality, root.eligible_for_conclusion) == ("SUPPORTING", False)
    assert root.conflict_with == [conflict.insight_id]
    assert conflict.materiality == "SUPPORTING" and result.status == "PARTIAL"

    _, result = run(overpriced_ctx(), llm_draft("C-T1-U011-1-OVERPRICED_VS_PEER"))
    warn = context(result_dataset(), dq([dq_field("dm_unit_friction_diagnostics", "price_spread_vs_peer_pct", "WARN")]))
    _, low = run(warn, llm_draft("C-T1-U011-1-OVERPRICED_VS_PEER"))
    assert low.payload.insights[0].confidence.level == "LOW" and low.payload.insights[0].materiality == "SUPPORTING"


def result_dataset() -> DatasetPayload:
    return dataset([unit(11)], [inventory(11, 145)], [diagnostic(11, 145)], [cause(11, "OVERPRICED_VS_PEER")])


def test_key_insights_are_capped_and_legal_leads_the_headline() -> None:
    ids = range(1, 5)
    units = [unit(i) for i in ids]
    inv = [inventory(i, 120 + i) for i in ids]
    diags = [diagnostic(i, 120 + i) for i in (1, 2, 3)] + [diagnostic(4, 124, "LEGAL_PERMIT_BARRIER")]
    causes = [cause(i, "OVERPRICED_VS_PEER") for i in (1, 2, 3)] + [cause(4, "LEGAL_PERMIT_BARRIER")]
    data = dataset(units, inv, diags, causes, projects=[project(is_sales_permit_issued=False)])
    ctx = context(data, cfg=with_params(context(data).cfg, max_key_insights=2))
    _, result = run(ctx)
    key = [i for i in result.payload.insights if i.materiality == "KEY"]
    assert len(key) == 2
    first = next(i for i in result.payload.insights if i.insight_id == result.payload.summary.headline_insight_ids[0])
    assert first.cause_code == "LEGAL_PERMIT_BARRIER" and first.level == "PROJECT"


def test_no_overdue_units_is_a_valid_answer() -> None:
    _, result = run(context(dataset([unit(1)], [inventory(1, 40)])))
    (ins,) = result.payload.insights
    assert ins.insight_type == "DATA_LIMITATION" and ins.materiality == "SUPPORTING"
    assert result.status == "PARTIAL"  # TEMPLATE narration; see the next test for the LLM case
    assert result.payload.summary.coverage.overdue_units == 0


def test_limitations_rejections_and_coverage_are_reported() -> None:
    data = dataset(
        [unit(1), unit(2), unit(3)],
        [inventory(1, 120), inventory(2, 130), inventory(3, 40)],
        [diagnostic(1, 120), diagnostic(2, 130, price_spread_vs_peer_pct=None, is_peer_sample_constrained=True)],
        [cause(1, "OVERPRICED_VS_PEER"), cause(2, "OVERPRICED_VS_PEER")],
    )
    batch, result = run(context(data))
    payload = result.payload
    assert ("C-T1-U002-1-OVERPRICED_VS_PEER", "PEER_DATA_MISSING") in [
        (r.candidate_id, r.reason_code) for r in payload.rejected_candidates
    ]
    codes = {lim.code: lim for lim in payload.limitations}
    assert "PEER_DATA_MISSING" in codes and codes["PEER_DATA_MISSING"].message == data_message("PEER_DATA_MISSING")
    assert codes["PEER_DATA_MISSING"].affected
    cov = payload.summary.coverage
    assert (cov.units_in_scope, cov.overdue_units, cov.units_explained) == (3, 2, 1)
    assert result.status == "PARTIAL"
    assert len(payload.rejected_candidates) >= len(batch.rejected)


def data_message(code: str) -> str:
    return context(result_dataset()).cfg.language.limitation_messages[code]


def test_merged_candidates_keep_the_first_id_and_trace_the_others() -> None:
    data = dataset(
        [unit(11)],
        [inventory(11, 145)],
        [diagnostic(11, 145)],
        [cause(11, "OVERPRICED_VS_PEER", 1, "0.600"), cause(11, "LOW_SALES_INCENTIVE", 2, "0.400")],
    )
    ctx = context(data)
    a, b = "C-T1-U011-1-OVERPRICED_VS_PEER", "C-T1-U011-2-LOW_SALES_INCENTIVE"
    merged = LlmInsightDraft.model_validate(
        {
            "selected": [
                {
                    "candidate_ids": [a, b],
                    "template": "Căn {{unit}} tồn {{dom}}; có khả năng liên quan tới {{cause_label}} và {{second}}.",
                    "slots": [{"slot": "unit", "ref": f"{a}.unit"}, {"slot": "dom", "ref": f"{a}.dom"},
                              {"slot": "cause_label", "ref": f"{a}.cause_label"}, {"slot": "second", "ref": f"{b}.cause_label"}],
                }
            ],
            "skipped": [],
        }
    )  # fmt: skip
    _, result = run(ctx, merged)
    (ins,) = result.payload.insights
    assert ins.candidate_id == a and f"candidate:{b}" in ins.lineage.calculation_refs
    assert ins.cause_code is None  # two causes in one idea
    assert ins.confidence.level == "MEDIUM"  # the weakest of the two
