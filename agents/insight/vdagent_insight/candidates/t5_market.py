"""T5 Market Context (spec §6.2, BR-09, D-72): one MARKET_CONTEXT candidate per market + segment.

Pure. For the market and segment of each project in scope, over the months present in the optional
`market_context` artifact (the data pack holds one 12-month window, no year-ago month):
- trend metrics (`market_metrics` with `trend: true`: mortgage rate, absorption) bind the latest
  month as `<slot>` and the first month of the window as `<slot>_start`, plus `window_months`;
- inferred metrics (`trend: false`: MOI, PIR, household income, PENDING in the DW) bind the latest
  level only and add the INFERRED_MARKET_METRIC limitation.
A null value leaves its slot out. Context only (BR-09): no cause_code, no action_code, never a
primary cause, `significant` false. One DW table backs it, so confidence tops out at MEDIUM.
"""

from __future__ import annotations

from decimal import Decimal

from ..contracts import CandidateLineage, InsightCandidate, MacroRow, NumericBinding, Subject
from ..settings import MarketMetric
from .common import CandidateBatch, CandidateContext, binding, cap_single_source, computed_ref
from .priority import base_priority, boosted

MACRO = "fact_market_macro_monthly"
INFERRED = "INFERRED_MARKET_METRIC"


def _bind(ctx: CandidateContext, metric: MarketMetric, row: MacroRow, index: int, name: str) -> NumericBinding | None:
    value = getattr(row, metric.column)
    if value is None:
        return None
    ref = f"{ctx.market_id}#/{MACRO}/{index}/{metric.column}"
    return binding(name, Decimal(value), metric.unit, ref, noun=metric.noun)


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
        matching = sorted(
            ((i, r) for i, r in rows if r.market_id == market_id and r.segment == segment), key=lambda ir: ir[1].date_key
        )
        if not matching:
            continue
        candidate_id = f"C-T5-{market_id}-{segment}"
        (first_index, first), (last_index, last) = matching[0], matching[-1]
        months = len({r.date_key // 100 for _, r in matching})
        slots = {
            "window_months": binding("window_months", months, "COUNT", computed_ref(candidate_id, "window_months"), noun="tháng")
        }
        flags: list[str] = []
        for metric in ctx.cfg.market_metrics:
            latest = _bind(ctx, metric, last, last_index, metric.slot)
            if latest is None:
                continue
            slots[metric.slot] = latest
            if not metric.trend:
                if INFERRED not in flags:
                    flags.append(INFERRED)
            elif first_index != last_index:
                start = _bind(ctx, metric, first, first_index, f"{metric.slot}_start")
                if start is not None:
                    slots[start.slot] = start
        evidence = sorted({f"{ctx.market_id}#/{MACRO}/{first_index}", f"{ctx.market_id}#/{MACRO}/{last_index}"})
        confidence = cap_single_source("HIGH", {MACRO})  # freshness (5.5) is about inventory and prices
        batch.candidates.append(
            InsightCandidate.model_validate(
                {
                    "candidate_id": candidate_id,
                    "task": "T5",
                    "insight_type": "MARKET_CONTEXT",
                    "level": "MARKET",
                    "subject": Subject(type="market", id=market_id, label=f"{market_id} ({segment})"),
                    "slots": slots,
                    "evidence_refs": evidence,
                    "lineage": CandidateLineage(
                        source_refs=[ctx.source(MACRO)],
                        calculation_refs=[ctx.calculation(MACRO, m.column) for m in ctx.cfg.market_metrics]
                        + ["insight.t5.window_trend@1"],
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
