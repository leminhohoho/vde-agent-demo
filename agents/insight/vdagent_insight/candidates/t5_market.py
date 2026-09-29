"""T5 Market Context (spec §6.2, BR-09): one MARKET_CONTEXT candidate per market + segment.

Pure. For the market and segment of each project in scope, the latest month of the optional
`market_context` artifact is set against the same month one year earlier: mortgage rate,
absorption rate, MOI and PIR (a null or missing value leaves its slot out). Context only
(BR-09): no cause_code, no action_code, never a primary cause, `significant` false. One DW table
backs it, so confidence tops out at MEDIUM.
"""

from __future__ import annotations

from decimal import Decimal

from ..contracts import BindingUnit, CandidateLineage, InsightCandidate, MacroRow, NumericBinding, Subject
from .common import CandidateBatch, CandidateContext, binding, cap_single_source
from .priority import base_priority, boosted

MACRO = "fact_market_macro_monthly"

# (slot, column, unit, noun)
METRICS: tuple[tuple[str, str, BindingUnit, str | None], ...] = (
    ("interest_rate", "floating_mortgage_rate_pct", "PCT", None),
    ("absorption_rate", "absorption_rate_pct", "PCT", None),
    ("moi", "months_of_inventory_moi", "RATIO", "tháng"),
    ("pir", "macro_price_to_income_ratio", "RATIO", None),
)


def _slots(ctx: CandidateContext, row: MacroRow, index: int, suffix: str) -> dict[str, NumericBinding]:
    slots: dict[str, NumericBinding] = {}
    for slot, column, unit, noun in METRICS:
        value = getattr(row, column)
        if value is not None:
            name = slot + suffix
            ref = f"{ctx.market_id}#/{MACRO}/{index}/{column}"
            slots[name] = binding(name, Decimal(value), unit, ref, noun=noun)
    return slots


def t5_candidates(ctx: CandidateContext) -> CandidateBatch:
    batch = CandidateBatch()
    if not ctx.wants("T5") or ctx.market is None:
        return batch
    rows = list(enumerate(ctx.market.fact_market_macro_monthly))
    markets = sorted(
        {
            (u.project.market_id, u.project.segment)
            for u in ctx.view.units_in_scope(ctx.request.analysis_scope)
            if u.project is not None
        }
    )
    for market_id, segment in markets:
        matching = [(i, r) for i, r in rows if r.market_id == market_id and r.segment == segment]
        if not matching:
            continue
        latest_index, latest = max(matching, key=lambda ir: ir[1].date_key)
        prior_month = (latest.date_key // 100) - 100  # YYYYMM one year earlier
        prior = next(((i, r) for i, r in matching if r.date_key // 100 == prior_month), None)
        slots = _slots(ctx, latest, latest_index, "")
        evidence = [f"{ctx.market_id}#/{MACRO}/{latest_index}"]
        if prior is not None:
            slots |= _slots(ctx, prior[1], prior[0], "_prior")
            evidence.append(f"{ctx.market_id}#/{MACRO}/{prior[0]}")
        flags: list[str] = []
        confidence = cap_single_source("HIGH", {MACRO})  # freshness (5.5) is about inventory and prices
        subject = Subject(type="market", id=market_id, label=f"{market_id} ({segment})")
        batch.candidates.append(
            InsightCandidate.model_validate(
                {
                    "candidate_id": f"C-T5-{market_id}-{segment}",
                    "task": "T5",
                    "insight_type": "MARKET_CONTEXT",
                    "level": "MARKET",
                    "subject": subject,
                    "slots": slots,
                    "evidence_refs": evidence,
                    "lineage": CandidateLineage(
                        source_refs=[ctx.source(MACRO)],
                        calculation_refs=[ctx.calculation(MACRO, column) for _, column, _, _ in METRICS],
                    ),
                    "significant": False,
                    "confidence": confidence,
                    "dq_flags": flags,
                    "priority": boosted(
                        base_priority("T5", None, None, ctx.cfg.params),
                        market_id,
                        ctx.recent_subject_ids,
                        ctx.recent_subject_boost,
                    ),
                }
            )
        )
    return batch
