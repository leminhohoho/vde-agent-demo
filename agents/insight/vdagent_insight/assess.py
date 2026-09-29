"""Confidence, KEY/SUPPORTING, status and the payload (pipeline step 9, spec §5.2, §4.2, §4.4). Pure.

The LLM never assigns any of this. From the narrated items (narrate.py) and their candidates:
- one `Insight` per item, ordered LEGAL_PERMIT_BARRIER first (BR-06), then the level of a
  ZONE/PROJECT scope (D-78), then by priority, then id;
  ids `INS-001`…, evidence ids `EV-<insight>-<n>`;
- merged candidates (D-27): `candidate_id` = the first, the others as `candidate:<id>` in
  `lineage.calculation_refs`; evidence and lineage are unions, confidence the weakest, the cause
  code only when all candidates share it;
- evidence kind: dq artifact → DQ, metric artifact or values computed in the task → METRIC (D-31),
  market artifact → MARKET, dataset rows → DIAGNOSTIC_ROW;
- KEY (5.2, D-28): ROOT_CAUSE_SIGNAL, CAUSE_DISTRIBUTION or a `significant` PATTERN, with evidence,
  both lineage branches, confidence ≠ LOW and no CONFLICT; at most `max_key_insights` (request
  constraint, else config). `eligible_for_conclusion` = KEY;
- recommendation (BR-10, D-33): the validated model text, else the config sentence; only with an
  action code, never for a metric lookup, and not when the user turned recommendations off;
- status (4.4): PARTIAL when the narration fell back to TEMPLATE, a candidate was rejected for
  missing evidence (E05/E06), a CONFLICT is open or an optional input is missing; INVALID when
  candidates existed but nothing could be rendered; otherwise VALID ("no overdue unit" is VALID).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .candidates.common import CANDIDATES_ARTIFACT, CandidateContext
from .candidates.priority import scope_rank
from .contracts import (
    ArtifactStatus,
    ChartHint,
    Claim,
    Confidence,
    Coverage,
    EvidenceRef,
    Insight,
    InsightCandidate,
    InsightPayload,
    Level,
    Limitation,
    Lineage,
    Recommendation,
    RejectedCandidate,
    Summary,
)
from .gate import LADDER
from .narrate import NarratedItem, Narration
from .render import recommendation_text
from .view import DatasetView

KEYABLE = frozenset({"ROOT_CAUSE_SIGNAL", "CAUSE_DISTRIBUTION", "PATTERN"})
RECOMMENDABLE = frozenset({"ROOT_CAUSE_SIGNAL", "CAUSE_DISTRIBUTION"})
EVIDENCE_REJECTIONS = frozenset({"EVIDENCE_FIELD_MISSING", "PEER_DATA_MISSING"})
PARTIAL_FLAGS = frozenset({"CONFLICT", "MARKET_CONTEXT_MISSING"})
LEGAL = "LEGAL_PERMIT_BARRIER"


@dataclass(frozen=True)
class Assessment:
    payload: InsightPayload
    status: ArtifactStatus


def _kind(artifact_id: str, ctx: CandidateContext) -> str:
    if artifact_id == ctx.dq_id:
        return "DQ"
    if artifact_id == ctx.market_id:
        return "MARKET"
    if artifact_id == ctx.metric_id or artifact_id == CANDIDATES_ARTIFACT:
        return "METRIC"
    return "DIAGNOSTIC_ROW"


def _evidence(number: int, refs: list[str], ctx: CandidateContext) -> list[EvidenceRef]:
    out = []
    for n, ref in enumerate(dict.fromkeys(refs), start=1):
        artifact_id, _, path = ref.partition("#")
        out.append(
            EvidenceRef.model_validate(
                {
                    "evidence_id": f"EV-{number:03d}-{n}",
                    "artifact_id": artifact_id,
                    "path": path or "/",
                    "kind": _kind(artifact_id, ctx),
                }
            )
        )
    return out


def _confidence(cands: list[InsightCandidate]) -> Confidence:
    level = LADDER[max(LADDER.index(c.confidence) for c in cands)]
    flags = list(dict.fromkeys(f for c in cands for f in c.dq_flags))
    reasons = []
    if not any(f.startswith("DQ_") or f in ("FIELD_EXCLUDED", "EVIDENCE_FIELD_MISSING") for f in flags):
        reasons.append("DQ_PASS")
    if level == "HIGH":
        reasons.append("TWO_INDEPENDENT_EVIDENCE")
    if all(c.significant for c in cands) and any(c.task == "T1" for c in cands):
        reasons.append("PEER_N_GE_MIN")
    return Confidence(level=level, reasons=reasons + flags)


def _order(item: NarratedItem, by_id: dict[str, InsightCandidate], scope_level: Level) -> tuple[int, int, object, str]:
    cands = [by_id[c] for c in item.candidate_ids]
    legal = any(c.cause_code == LEGAL and c.level == "PROJECT" for c in cands)
    fit = min(scope_rank(c, scope_level) for c in cands)
    return (0 if legal else 1, fit, -max(c.priority for c in cands), item.candidate_ids[0])


def _is_keyable(ins: Insight, cands: list[InsightCandidate]) -> bool:
    if ins.insight_type not in KEYABLE or (ins.insight_type == "PATTERN" and not all(c.significant for c in cands)):
        return False
    lineage_ok = bool(ins.lineage.source_refs) and bool(ins.lineage.calculation_refs)
    conflict = any("CONFLICT" in c.dq_flags for c in cands)
    return bool(ins.evidence_refs) and lineage_ok and ins.confidence.level != "LOW" and not conflict


def _recommendation(
    item: NarratedItem, cands: list[InsightCandidate], ctx: CandidateContext, show: bool
) -> Recommendation | None:
    actionable = next((c for c in cands if c.action_code), None)
    if (
        not show
        or actionable is None
        or ctx.request.intent == "PERFORMANCE_METRIC_LOOKUP"
        or cands[0].insight_type not in RECOMMENDABLE
    ):
        return None
    text = item.recommendation_text or recommendation_text(actionable, ctx.cfg)
    if text is None or actionable.action_code is None:
        return None
    return Recommendation(action_code=actionable.action_code, text=text, is_suggestion=True, requires_human_approval=True)


def _insight(number: int, item: NarratedItem, by_id: dict[str, InsightCandidate], ctx: CandidateContext, show: bool) -> Insight:
    cands = [by_id[c] for c in item.candidate_ids]
    first = cands[0]
    causes = {c.cause_code for c in cands}
    messages = ctx.cfg.language.limitation_messages
    flags = list(dict.fromkeys(f for c in cands for f in c.dq_flags))
    limitations = [messages[f] for f in flags if f in messages]
    if item.limitation_text:
        limitations.append(item.limitation_text)
    peer_based = any(
        c.cause_code in ctx.cfg.allowed_cause_codes and ctx.cfg.cause(c.cause_code).uses_peer_group for c in cands if c.cause_code
    )
    return Insight(
        insight_id=f"INS-{number:03d}",
        candidate_id=first.candidate_id,
        insight_type=first.insight_type,
        level=first.level,
        subject=first.subject,
        cause_code=first.cause_code if len(causes) == 1 else None,
        claim=Claim(
            template=item.claim.template, rendered_text=item.claim.rendered_text, numeric_bindings=item.claim.numeric_bindings
        ),
        evidence_refs=_evidence(number, [r for c in cands for r in c.evidence_refs], ctx),
        lineage=Lineage(
            source_refs=list(dict.fromkeys(r for c in cands for r in c.lineage.source_refs)),
            calculation_refs=list(dict.fromkeys(r for c in cands for r in c.lineage.calculation_refs))
            + [f"candidate:{c.candidate_id}" for c in cands[1:]],
            peer_rule_ref=f"dw.peer_group@{ctx.request.semantic_config_version}" if peer_based else None,
        ),
        severity_rank=first.severity_rank,
        attribution_score=first.attribution_score,
        confidence=_confidence(cands),
        materiality="SUPPORTING",
        eligible_for_conclusion=False,
        recommendation=_recommendation(item, cands, ctx, show),
        limitations=limitations,
    )


def _explained_units(insights: list[Insight], by_id: dict[str, InsightCandidate], ctx: CandidateContext) -> set[str]:
    view, threshold = ctx.view, ctx.cfg.params.overdue_threshold_days
    overdue = [u for u in view.units_in_scope(ctx.request.analysis_scope) if DatasetView.is_overdue(u, threshold)]
    explained: set[str] = set()
    for ins in insights:
        if ins.insight_type == "ROOT_CAUSE_SIGNAL" and ins.level == "UNIT":
            explained.add(ins.subject.id)
        elif ins.insight_type == "ROOT_CAUSE_SIGNAL" and ins.cause_code == LEGAL:
            explained |= {
                u.unit.unit_id
                for u in overdue
                if u.project is not None
                and u.project.project_id == ins.subject.id
                and any(r.cause_code == LEGAL for r, _ in u.causes)
            }
        elif ins.insight_type == "CAUSE_DISTRIBUTION":
            scope = ctx.gate.scopes.get((ins.level, ins.subject.id))
            if scope is not None:
                explained |= {u.unit.unit_id for u in overdue if u.unit.unit_key in scope.n_eff_unit_keys}
    return explained & {u.unit.unit_id for u in overdue}


def _limitations(
    insights: list[Insight], by_id: dict[str, InsightCandidate], rejected: list[RejectedCandidate], ctx: CandidateContext
) -> list[Limitation]:
    messages = ctx.cfg.language.limitation_messages
    affected: dict[str, list[str]] = defaultdict(list)
    for ins in insights:
        cands = [by_id[ins.candidate_id]] + [
            by_id[r.split(":", 1)[1]] for r in ins.lineage.calculation_refs if r.startswith("candidate:")
        ]
        for flag in dict.fromkeys(f for c in cands for f in c.dq_flags):
            if flag in messages:
                affected[flag].append(ins.insight_id)
    for r in rejected:
        if r.reason_code in messages:
            affected[r.reason_code].append(r.candidate_id)
    return [
        Limitation(code=code, message=messages[code], affected=list(dict.fromkeys(ids))) for code, ids in sorted(affected.items())
    ]


def assess(
    ctx: CandidateContext,
    candidates: list[InsightCandidate],
    rejected: list[RejectedCandidate],
    narration: Narration,
    *,
    show_recommendation: bool = True,
) -> Assessment:
    by_id = {c.candidate_id: c for c in candidates}
    items = sorted(narration.items, key=lambda i: _order(i, by_id, ctx.request.analysis_scope.level))
    insights = [_insight(n, item, by_id, ctx, show_recommendation) for n, item in enumerate(items, start=1)]

    max_key = ctx.request.constraints.max_key_insights or ctx.cfg.params.max_key_insights
    keyed: list[str] = []
    for i, ins in enumerate(insights):
        cands = [by_id[c] for c in items[i].candidate_ids]
        if len(keyed) < max_key and _is_keyable(ins, cands):
            insights[i] = ins.model_copy(update={"materiality": "KEY", "eligible_for_conclusion": True})
            keyed.append(ins.insight_id)

    conflicts = {ins.subject.id: ins.insight_id for ins in insights if ins.insight_type == "CONFLICT"}
    for i, ins in enumerate(insights):
        cands = [by_id[c] for c in items[i].candidate_ids]
        if ins.insight_type != "CONFLICT" and any("CONFLICT" in c.dq_flags for c in cands) and ins.subject.id in conflicts:
            insights[i] = ins.model_copy(update={"conflict_with": [conflicts[ins.subject.id]]})

    hints = [
        ChartHint(
            insight_id=ins.insight_id, suggested_chart=chart, metric_refs=[b.metric_ref for b in ins.claim.numeric_bindings]
        )
        for ins in insights
        if ins.materiality == "KEY"
        and (chart := ctx.cfg.language.chart_hints.get(ins.insight_type))
        and ins.claim.numeric_bindings
    ]
    all_rejected = [*rejected, *narration.rejected]
    view, threshold = ctx.view, ctx.cfg.params.overdue_threshold_days
    in_scope = view.units_in_scope(ctx.request.analysis_scope)
    coverage = Coverage(
        units_in_scope=len(in_scope),
        overdue_units=sum(DatasetView.is_overdue(u, threshold) for u in in_scope),
        units_explained=len(_explained_units(insights, by_id, ctx)),
    )
    payload = InsightPayload(
        summary=Summary(headline_insight_ids=keyed, coverage=coverage, narrative_mode=narration.narrative_mode),
        insights=insights,
        rejected_candidates=all_rejected,
        chart_hints=hints,
        limitations=_limitations(insights, by_id, all_rejected, ctx),
    )

    flags = {f for c in candidates for f in c.dq_flags}
    if candidates and not insights:
        status: ArtifactStatus = "INVALID"
    elif (
        narration.narrative_mode == "TEMPLATE"
        or any(r.reason_code in EVIDENCE_REJECTIONS for r in all_rejected)
        or flags & PARTIAL_FLAGS
        or conflicts
    ):
        status = "PARTIAL"
    else:
        status = "VALID"
    return Assessment(payload=payload, status=status)
