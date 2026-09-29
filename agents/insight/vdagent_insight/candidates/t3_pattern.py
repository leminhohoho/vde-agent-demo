"""T3 Pattern Detection (spec §6.2, BR-08): PATTERN candidates, one group vs the rest of the scope.

Pure. Population = the scope's unsold inventory (inventory row AVAILABLE), extreme DOM outliers
removed (3×IQR, unless that would cut more than `outlier_max_excluded_pct`). For every configured
dimension with at least two groups, each group is compared with the rest by median DOM and overdue
rate (overdue units / unsold units):

- BR-08: group < `min_group_size` → rejected GROUP_TOO_SMALL; |median gap| < `min_effect_size_days`
  → rejected EFFECT_TOO_SMALL.
- Groups of `describe_min`..`compare_min - 1` (3–4, D-71) → SMALL_SAMPLE, describe only.
- `significant` = seeded-bootstrap intervals of the two medians do not overlap and the gap reaches
  the effect size (stats.py).

The group medians and rates are computed here from the dataset rows (docs/OPEN_QUESTIONS.md Q14),
so their `metric_ref` points into this task's `insight_candidates` artifact. No cause, no action.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from ..contracts import CandidateLineage, InsightCandidate, Subject
from ..gate import HUNDRED, exclude_outliers, sample_tier
from ..settings import PatternDimension
from ..view import DatasetView, UnitView
from .common import CandidateBatch, CandidateContext, apply_fields, apply_freshness, binding, cap_single_source, computed_ref
from .priority import base_priority, boosted
from .stats import median, significant_median_difference

INVENTORY = "fact_unit_inventory_snapshot"
UNIT_MASTER = "dim_unit_master"


def _dom(u: UnitView) -> Decimal:
    assert u.inventory is not None
    return Decimal(u.inventory.unsold_days_dom)


def _group_value(u: UnitView, dim: PatternDimension) -> str:
    row = u.unit if dim.table == UNIT_MASTER else u.inventory
    return str(getattr(row, dim.name))


def _rate(units: list[UnitView], threshold: int) -> Decimal:
    return Decimal(sum(DatasetView.is_overdue(u, threshold) for u in units)) * HUNDRED / len(units)


def _candidate(
    ctx: CandidateContext, dim: PatternDimension, value: str, group: list[UnitView], rest: list[UnitView]
) -> InsightCandidate:
    params = ctx.cfg.params
    candidate_id = f"C-T3-{dim.name}-{value}"

    def ref(slot: str) -> str:
        return computed_ref(candidate_id, slot)

    group_doms, rest_doms = [_dom(u) for u in group], [_dom(u) for u in rest]
    threshold = params.overdue_threshold_days
    slots = {
        "group_dom": binding("group_dom", median(group_doms), "DAY", ref("group_dom")),
        "rest_dom": binding("rest_dom", median(rest_doms), "DAY", ref("rest_dom")),
        "group_overdue_rate": binding("group_overdue_rate", _rate(group, threshold), "PCT", ref("group_overdue_rate")),
        "rest_overdue_rate": binding("rest_overdue_rate", _rate(rest, threshold), "PCT", ref("rest_overdue_rate")),
        "group_units": binding("group_units", len(group), "COUNT", ref("group_units"), noun="căn"),
        "rest_units": binding("rest_units", len(rest), "COUNT", ref("rest_units"), noun="căn"),
    }
    fields = [f for f in (ctx.gate.field(INVENTORY, "unsold_days_dom"), ctx.gate.field(dim.table, dim.name)) if f]
    confidence, flags = apply_fields("HIGH", fields)
    confidence = apply_freshness(confidence, flags, ctx.gate)
    confidence = cap_single_source(confidence, {INVENTORY, dim.table})
    tier = sample_tier(min(len(group), len(rest)), params.peer_tiers)
    if tier.flag:
        flags.append(tier.flag)
    describe_only = tier.describe_only or any(f.describe_only for f in fields)
    significant = not describe_only and significant_median_difference(group_doms, rest_doms, params)
    evidence = [ctx.ref(INVENTORY, u.inventory_index) for u in group if u.inventory_index is not None]
    subject_id = f"{dim.name}={value}"
    return InsightCandidate.model_validate(
        {
            "candidate_id": candidate_id,
            "task": "T3",
            "insight_type": "PATTERN",
            "level": ctx.request.analysis_scope.level,
            "subject": Subject(type="group", id=subject_id, label=value),
            "slots": slots,
            "evidence_refs": evidence,
            "lineage": CandidateLineage(
                source_refs=sorted({ctx.source(INVENTORY), ctx.source(dim.table)}),
                calculation_refs=[
                    "insight.t3.median_dom@1",
                    "insight.t3.overdue_rate@1",
                    f"insight.stats.bootstrap@seed={params.bootstrap_seed},n={params.bootstrap_iterations}",
                ],
            ),
            "n_eff": len(group),
            "significant": significant,
            "confidence": confidence,
            "dq_flags": flags,
            "priority": boosted(
                base_priority("T3", None, None, params), subject_id, ctx.recent_subject_ids, ctx.recent_subject_boost
            ),
        }
    )


def t3_candidates(ctx: CandidateContext) -> CandidateBatch:
    batch = CandidateBatch()
    if not ctx.wants("T3"):
        return batch
    params = ctx.cfg.params
    unsold = [
        u
        for u in ctx.view.units_in_scope(ctx.request.analysis_scope)
        if u.inventory is not None and u.inventory.inventory_status == "AVAILABLE"
    ]
    population, _ = exclude_outliers(unsold, _dom, params)
    for dim in ctx.cfg.pattern_dimensions:
        groups: dict[str, list[UnitView]] = defaultdict(list)
        for u in population:
            groups[_group_value(u, dim)].append(u)
        if len(groups) < 2:
            continue
        for value in sorted(groups):
            group = groups[value]
            rest = [u for u in population if _group_value(u, dim) != value]
            candidate_id = f"C-T3-{dim.name}-{value}"
            if len(group) < params.min_group_size:
                batch.reject(candidate_id, "GROUP_TOO_SMALL")
            elif abs(median([_dom(u) for u in group]) - median([_dom(u) for u in rest])) < params.min_effect_size_days:
                batch.reject(candidate_id, "EFFECT_TOO_SMALL")
            else:
                batch.candidates.append(_candidate(ctx, dim, value, group, rest))
    return batch
