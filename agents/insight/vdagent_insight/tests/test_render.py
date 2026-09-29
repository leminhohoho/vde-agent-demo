"""claim_binder, labels and TEMPLATE mode (pipeline step 8, spec §8.2)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from ..candidates.common import CandidateContext
from ..candidates.t1_unit import t1_candidates
from ..candidates.t2_distribution import t2_candidates
from ..candidates.t7_limitation import t7_candidates
from ..contracts import InsightCandidate
from ..render import RenderError, bind_claim, recommendation_text, render_template
from .builders import cause, context, dataset, diagnostic, dq, dq_field, inventory, project, scope, unit


def overpriced(**diag_kw: object) -> tuple[CandidateContext, InsightCandidate]:
    data = dataset(
        [unit(11, unit_code="SAPPHIRE1-16.231")],
        [inventory(11, 145)],
        [diagnostic(11, 145, **diag_kw)],
        [cause(11, "OVERPRICED_VS_PEER")],
    )
    ctx = context(data)
    (c,) = t1_candidates(ctx).candidates
    return ctx, c


def test_template_mode_binds_real_numbers_and_the_cause_label() -> None:
    ctx, c = overpriced()
    claim = render_template(c, ctx.cfg, ctx.view)
    assert claim.template == ctx.cfg.cause("OVERPRICED_VS_PEER").template
    assert claim.rendered_text == (
        "Căn SAPPHIRE1-16.231 tồn 145 ngày; yếu tố có khả năng liên quan: giá cao hơn nhóm tương đồng "
        "(đơn giá/m² so với trung vị peer +12,4%)."
    )
    assert [(b.slot, b.value, b.metric_ref) for b in claim.numeric_bindings] == [
        ("dom", Decimal(145), c.slots["dom"].metric_ref),
        ("spread", Decimal("12.40"), c.slots["spread"].metric_ref),
    ]


def test_too_few_peers_hide_the_peer_comparison() -> None:
    ctx, c = overpriced(peer_count=2)
    claim = render_template(c, ctx.cfg, ctx.view)
    assert claim.template == ctx.cfg.language.peer_hidden_template
    assert "12,4" not in claim.rendered_text and "nhóm tương đồng quá ít căn" in claim.rendered_text


@pytest.mark.parametrize(
    ("permit", "guarantee", "label"),
    [
        (False, True, "giấy phép mở bán"),
        (True, False, "bảo lãnh ngân hàng"),
        (False, False, "giấy phép mở bán và bảo lãnh ngân hàng"),
    ],
)
def test_legal_template_says_what_the_project_lacks(permit: bool, guarantee: bool, label: str) -> None:
    data = dataset(
        [unit(1)],
        [inventory(1, 120)],
        [diagnostic(1, 120, "LEGAL_PERMIT_BARRIER")],
        [cause(1, "LEGAL_PERMIT_BARRIER")],
        projects=[project(is_sales_permit_issued=permit, is_bank_guarantee_issued=guarantee)],
    )
    ctx = context(data)
    (c,) = t1_candidates(ctx).candidates
    claim = render_template(c, ctx.cfg, ctx.view)
    assert claim.rendered_text == (
        f"Dự án Dự án X chưa đủ {label}; vướng mắc pháp lý / giấy phép bán hàng là yếu tố có khả năng liên quan tới 1 căn quá hạn."
    )


def test_distribution_template_states_both_counting_methods() -> None:
    units = [unit(i) for i in range(1, 11)]
    data = dataset(
        units,
        [inventory(i, 100 + i) for i in range(1, 11)],
        [diagnostic(i, 100 + i) for i in range(1, 11)],
        [cause(i, "OVERPRICED_VS_PEER") for i in range(1, 11)],
    )
    ctx = context(data, tasks=("T2",), analysis_scope=scope("ZONE", zone_ids=["ZN-AQUA-01"]))
    (c,) = t2_candidates(ctx).candidates
    text = render_template(c, ctx.cfg, ctx.view).rendered_text
    assert text.startswith("Trong Tòa Aqua 1, giá cao hơn nhóm tương đồng chiếm 100% tổng điểm")
    assert "(có trọng số)" in text and "(đếm theo căn)" in text


def test_limitation_template_uses_the_catalogue_message() -> None:
    field = dq_field("fact_unit_inventory_snapshot", "spiff_bonus_vnd", "WARN", primary=False, missing="45")
    ctx = context(dataset([unit(1)], [inventory(1, 40)]), dq([field]))
    c = next(x for x in t7_candidates(ctx).candidates if x.candidate_id.startswith("C-T7-DQ-"))
    text = render_template(c, ctx.cfg, ctx.view).rendered_text
    assert text == "Dữ liệu cho spiff_bonus_vnd còn hạn chế: trường dữ liệu thiếu quá nhiều nên không được sử dụng."


def test_bind_claim_follows_the_slot_refs_of_the_draft() -> None:
    ctx, c = overpriced()
    cid = c.candidate_id
    claim = bind_claim(
        "{{unit}} đã tồn {{days}}, có khả năng liên quan tới {{cause_label}}.",
        {"unit": f"{cid}.unit", "days": f"{cid}.dom", "cause_label": f"{cid}.cause_label"},
        {cid: c},
        ctx.cfg,
        ctx.view,
    )
    assert claim.rendered_text == "SAPPHIRE1-16.231 đã tồn 145 ngày, có khả năng liên quan tới giá cao hơn nhóm tương đồng."
    assert [(b.slot, b.value) for b in claim.numeric_bindings] == [("days", Decimal(145))]


@pytest.mark.parametrize(
    ("template", "refs"),
    [
        ("Tồn {{dom}}.", {}),  # no ref for a slot
        ("Tồn {{dom}}.", {"dom": "C-UNKNOWN.dom"}),  # unknown candidate
        ("Tồn {{dom}}.", {"dom": "{cid}.nope"}),  # unknown slot of the candidate
    ],
)
def test_unresolvable_slots_are_render_errors(template: str, refs: dict[str, str]) -> None:
    ctx, c = overpriced()
    refs = {k: v.format(cid=c.candidate_id) for k, v in refs.items()}
    with pytest.raises(RenderError):
        bind_claim(template, refs, {c.candidate_id: c}, ctx.cfg, ctx.view)


def test_recommendation_comes_from_the_config_only_with_an_action() -> None:
    ctx, c = overpriced()
    assert recommendation_text(c, ctx.cfg) == ctx.cfg.cause("OVERPRICED_VS_PEER").recommendation_text
    ctx2 = context(
        dataset([unit(11)], [inventory(11, 145)], [diagnostic(11, 145)], [cause(11, "OVERPRICED_VS_PEER")]), tasks=("T1",)
    )
    (no_t6,) = t1_candidates(ctx2).candidates
    assert recommendation_text(no_t6, ctx2.cfg) is None
