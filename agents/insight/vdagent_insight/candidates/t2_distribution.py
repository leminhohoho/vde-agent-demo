"""T2 Cause Distribution (spec §6.2, BR-05): CAUSE_DISTRIBUTION candidates per zone or project.

Pure. Counts only the gate's effective units of the scope (overdue, diagnosed with scores that sum
to 1, extreme DOM outliers removed) and reports **both** methods:

- `weighted_share` = Σ attribution_score of the cause / Σ attribution_score of all rows (shares
  of all causes add up to 100 %);
- `unit_share` = units having the cause at any rank / effective units (may add up to > 100 %).

A candidate per allowed cause with `weighted_share ≥ min_cause_share_pct`, largest first; smaller
ones are rejected CAUSE_SHARE_TOO_SMALL, unknown codes CAUSE_CODE_NOT_ALLOWED (BR-02; their weight
still counts in the total). Coverage below `low_min` → no conclusion at that level
(INSUFFICIENT_COVERAGE), n_eff below `describe_min` → GROUP_TOO_SMALL (spec §5.5). T2 makes no
statistical comparison, so `significant` is always false.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from ..contracts import CandidateLineage, InsightCandidate, Subject
from ..gate import HUNDRED, ScopeAssessment
from .common import CandidateBatch, CandidateContext, binding, computed_ref
from .priority import base_priority, boosted

BRIDGE = "unit_diagnostic_causes"
MART = "dm_unit_friction_diagnostics"


@dataclass(frozen=True)
class CauseShare:
    cause_code: str
    weighted_share_pct: Decimal
    unit_share_pct: Decimal
    units_with_cause: int
    evidence_refs: tuple[str, ...]


def cause_distribution(ctx: CandidateContext, scope: ScopeAssessment) -> list[CauseShare]:
    """Every cause of the scope's effective units, by weighted share (largest first), then code."""
    weights: dict[str, Decimal] = defaultdict(Decimal)
    units: dict[str, set[int]] = defaultdict(set)
    refs: dict[str, list[str]] = defaultdict(list)
    for u in ctx.view.units:
        if u.unit.unit_key not in scope.n_eff_unit_keys:
            continue
        for row, index in u.causes:
            weights[row.cause_code] += row.attribution_score
            units[row.cause_code].add(u.unit.unit_key)
            refs[row.cause_code].append(ctx.ref(BRIDGE, index))
    total = sum(weights.values(), Decimal(0))
    n = len(scope.n_eff_unit_keys)
    if not total or not n:
        return []
    shares = [
        CauseShare(
            cause_code=code,
            weighted_share_pct=weights[code] * HUNDRED / total,
            unit_share_pct=Decimal(len(units[code])) * HUNDRED / n,
            units_with_cause=len(units[code]),
            evidence_refs=tuple(refs[code]),
        )
        for code in weights
    ]
    return sorted(shares, key=lambda s: (-s.weighted_share_pct, s.cause_code))


def _scopes(ctx: CandidateContext) -> list[ScopeAssessment]:
    level = ctx.request.analysis_scope.level
    wanted = "ZONE" if level == "ZONE" else "PROJECT" if level in ("PROJECT", "MARKET") else None
    return [s for (lvl, _), s in sorted(ctx.gate.scopes.items()) if lvl == wanted]


def _candidate(ctx: CandidateContext, scope: ScopeAssessment, share: CauseShare) -> InsightCandidate:
    candidate_id = f"C-T2-{scope.id}-{share.cause_code}"

    def ref(slot: str) -> str:
        return computed_ref(candidate_id, slot)

    slots = {
        "weighted_share": binding("weighted_share", share.weighted_share_pct, "PCT", ref("weighted_share")),
        "unit_share": binding("unit_share", share.unit_share_pct, "PCT", ref("unit_share")),
        "units_with_cause": binding("units_with_cause", share.units_with_cause, "COUNT", ref("units_with_cause"), noun="căn"),
        "units_diagnosed": binding("units_diagnosed", scope.n_eff, "COUNT", ref("units_diagnosed"), noun="căn"),
        "overdue_units": binding("overdue_units", scope.overdue_units, "COUNT", ref("overdue_units"), noun="căn"),
    }
    cause = ctx.cfg.cause(share.cause_code)
    priority = base_priority("T2", None, None, ctx.cfg.params)
    return InsightCandidate.model_validate(
        {
            "candidate_id": candidate_id,
            "task": "T2",
            "insight_type": "CAUSE_DISTRIBUTION",
            "level": scope.level,
            "subject": Subject(type=scope.level.lower(), id=scope.id, label=scope.label),
            "cause_code": share.cause_code,
            "slots": slots,
            "evidence_refs": list(share.evidence_refs),
            "lineage": CandidateLineage(
                source_refs=[ctx.source(BRIDGE), ctx.source(MART)],
                calculation_refs=["insight.t2.weighted_share@1", "insight.t2.unit_share@1"],
            ),
            "n_eff": scope.n_eff,
            "coverage": scope.coverage_pct,
            "significant": False,
            "confidence": scope.confidence,
            "dq_flags": list(scope.flags),
            "action_code": cause.action_code if ctx.wants("T6") else None,
            "priority": boosted(priority, scope.id, ctx.recent_subject_ids, ctx.recent_subject_boost),
        }
    )


def t2_candidates(ctx: CandidateContext) -> CandidateBatch:
    batch = CandidateBatch()
    if not ctx.wants("T2"):
        return batch
    min_share = ctx.cfg.params.min_cause_share_pct
    for scope in _scopes(ctx):
        if scope.overdue_units == 0:
            continue
        blocker = scope.coverage if scope.coverage.excluded else scope.sample if scope.sample.excluded else None
        if blocker is not None and blocker.flag:
            batch.reject(f"C-T2-{scope.id}", blocker.flag)
            continue
        for share in cause_distribution(ctx, scope):
            candidate_id = f"C-T2-{scope.id}-{share.cause_code}"
            if share.cause_code not in ctx.cfg.allowed_cause_codes:
                batch.reject(candidate_id, "CAUSE_CODE_NOT_ALLOWED")
            elif share.weighted_share_pct < min_share:
                batch.reject(candidate_id, "CAUSE_SHARE_TOO_SMALL")
            else:
                batch.candidates.append(_candidate(ctx, scope, share))
    return batch
