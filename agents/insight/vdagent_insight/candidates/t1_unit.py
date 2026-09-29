"""T1 Unit Diagnosis (spec §6.2): ROOT_CAUSE_SIGNAL candidates from the diagnostic mart + bridge.

Pure. For every overdue unit in scope (BR-01, from the inventory snapshot) one candidate per
bridge row, in `severity_rank` order (BR-03), binding `{{dom}}` plus the cause's minimum evidence
from config (spec 6.2 table):

- BR-02: a cause outside `allowed_cause_codes` → rejected CAUSE_CODE_NOT_ALLOWED.
- Missing or DQ-FAIL required evidence → rejected EVIDENCE_FIELD_MISSING; a missing
  `price_spread_vs_peer_pct` → PEER_DATA_MISSING (E06). Excluded supplementary fields are dropped.
- BR-03/BR-04: mart and bridge disagree → every candidate of the unit carries CONFLICT (T7 emits
  the CONFLICT candidate itself).
- BR-06: LEGAL_PERMIT_BARRIER is a project-level factor → one PROJECT candidate per project
  (bound to its number of overdue units with that cause), never per unit.
- BR-07: peer-based causes follow the peer tiers; a constrained sample caps confidence at MEDIUM
  and adds PEER_SAMPLE_CONSTRAINED. `significant` = the peer comparison may be stated strongly.
- More than `max_units_in_context` diagnosed units → per `primary_cause_code` only the
  `unit_examples_per_cause` longest-unsold units are kept, the rest rejected UNITS_GROUPED (T2
  still counts them).
- Outlying DOM within the unit's zone is flagged (OUTLIER_WARN / OUTLIER_EXCLUDED), never dropped.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from ..contracts import CandidateLineage, CauseRow, ConfidenceLevel, InsightCandidate, NumericBinding, Subject
from ..gate import FieldAssessment, cap, iqr_fences, outlier_flag, sample_tier
from ..settings import CauseEntry, EvidenceSpec
from ..view import DatasetView, UnitView
from .common import (
    CandidateBatch,
    CandidateContext,
    apply_fields,
    apply_freshness,
    binding,
    cap_single_source,
    computed_ref,
    unit_conflicts,
)
from .priority import base_priority, boosted

INVENTORY = "fact_unit_inventory_snapshot"
MART = "dm_unit_friction_diagnostics"
BRIDGE = "unit_diagnostic_causes"
PROJECT = "dim_project_profile"
LEGAL = "LEGAL_PERMIT_BARRIER"
PEER_SPREAD = "price_spread_vs_peer_pct"
MIN_ZONE_DOMS_FOR_OUTLIERS = 4


class _Rejected(Exception):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code


def _read(ctx: CandidateContext, unit: UnitView, spec: EvidenceSpec) -> tuple[object, str]:
    if spec.table == INVENTORY:
        row, index = unit.inventory, unit.inventory_index
    elif spec.table == MART:
        row, index = unit.diagnostic, unit.diagnostic_index
    else:
        row = unit.project
        index = ctx.view.project_index(unit.project.project_key) if unit.project else None
    if row is None or index is None:
        return None, ""
    return getattr(row, spec.field), ctx.ref(spec.table, index, spec.field)


def _examples(overdue: list[UnitView], ctx: CandidateContext) -> set[int]:
    params = ctx.cfg.params
    if len(overdue) <= params.max_units_in_context:
        return {u.unit.unit_key for u in overdue}
    groups: dict[str, list[UnitView]] = defaultdict(list)
    for u in overdue:
        assert u.diagnostic is not None
        groups[u.diagnostic.primary_cause_code].append(u)
    keep: set[int] = set()
    for members in groups.values():
        members.sort(key=lambda u: (-(u.inventory.unsold_days_dom if u.inventory else 0), u.unit.unit_code))
        keep |= {u.unit.unit_key for u in members[: params.unit_examples_per_cause]}
    return keep


def _outlier_flags(overdue: list[UnitView], ctx: CandidateContext) -> dict[int, str]:
    params = ctx.cfg.params
    by_zone: dict[int, list[UnitView]] = defaultdict(list)
    for u in overdue:
        by_zone[u.unit.zone_key].append(u)
    flags: dict[int, str] = {}
    for members in by_zone.values():
        doms = [Decimal(u.inventory.unsold_days_dom) for u in members if u.inventory]
        if len(doms) < MIN_ZONE_DOMS_FOR_OUTLIERS:
            continue
        fences = iqr_fences(doms, params.outlier_iqr_warn, params.outlier_iqr_exclude)
        for u in members:
            assert u.inventory is not None
            flag = outlier_flag(Decimal(u.inventory.unsold_days_dom), fences)
            if flag:
                flags[u.unit.unit_key] = flag
    return flags


def _bind_evidence(
    ctx: CandidateContext,
    unit: UnitView,
    cause: CauseEntry,
    slots: dict[str, NumericBinding],
    evidence: list[str],
    fields: list[FieldAssessment],
    sources: set[str],
) -> bool:
    """Bind required then supplementary evidence; raises `_Rejected`. True if a used field is describe-only."""
    describe_only = False
    for spec, required in [(s, True) for s in cause.required_evidence] + [(s, False) for s in cause.supplementary_evidence]:
        assessment = ctx.gate.field(spec.table, spec.field)
        if assessment and (assessment.reject or (assessment.excluded and not required)):
            if required:
                raise _Rejected("EVIDENCE_FIELD_MISSING")
            continue
        value, ref = _read(ctx, unit, spec)
        if value is None:
            if required:
                raise _Rejected("PEER_DATA_MISSING" if spec.field == PEER_SPREAD else "EVIDENCE_FIELD_MISSING")
            continue
        evidence.append(ref)
        if assessment:
            fields.append(assessment)
            describe_only = describe_only or assessment.describe_only
        if spec.unit and isinstance(value, (int, Decimal)) and not isinstance(value, bool):
            slots[spec.slot] = binding(spec.slot, value, spec.unit, ref, noun=spec.noun, signed=spec.signed)
            sources.add(spec.table)
    return describe_only


def _unit_candidate(
    ctx: CandidateContext,
    unit: UnitView,
    row: CauseRow,
    row_index: int,
    candidate_id: str,
    conflicts: list[str],
    outlier: str | None,
) -> InsightCandidate:
    assert unit.inventory is not None and unit.inventory_index is not None
    assert unit.diagnostic is not None and unit.diagnostic_index is not None
    cause = ctx.cfg.cause(row.cause_code)
    dom_ref = ctx.ref(INVENTORY, unit.inventory_index, "unsold_days_dom")
    slots = {"dom": binding("dom", unit.inventory.unsold_days_dom, "DAY", dom_ref)}
    evidence = [ctx.ref(INVENTORY, unit.inventory_index), ctx.ref(MART, unit.diagnostic_index), ctx.ref(BRIDGE, row_index)]
    dom_field = ctx.gate.field(INVENTORY, "unsold_days_dom")
    fields = [dom_field] if dom_field else []
    sources = {INVENTORY}
    describe_only = _bind_evidence(ctx, unit, cause, slots, evidence, fields, sources)

    confidence, flags = apply_fields("HIGH", fields)
    confidence = apply_freshness(confidence, flags, ctx.gate)
    confidence = cap_single_source(confidence, sources)
    significant = False
    if cause.uses_peer_group:
        tier = sample_tier(unit.diagnostic.peer_count, ctx.cfg.params.peer_tiers)
        if tier.flag:
            flags.append(tier.flag)
        if unit.diagnostic.is_peer_sample_constrained:
            flags.append("PEER_SAMPLE_CONSTRAINED")
            confidence = cap(confidence, "MEDIUM")
        significant = tier.flag is None and not unit.diagnostic.is_peer_sample_constrained and not describe_only
    if outlier:
        flags.append(outlier)
    if conflicts:
        flags.append("CONFLICT")

    tables = sorted(sources | {MART, BRIDGE})
    bound = [s for s in cause.required_evidence + cause.supplementary_evidence if s.slot in slots]
    return _candidate(
        ctx,
        candidate_id=candidate_id,
        level="UNIT",
        subject=Subject(type="unit", id=unit.unit.unit_id, label=unit.unit.unit_code),
        cause=cause,
        slots=slots,
        evidence=evidence,
        lineage=CandidateLineage(
            source_refs=[ctx.source(t) for t in tables],
            calculation_refs=[ctx.calculation(INVENTORY, "unsold_days_dom")] + [ctx.calculation(s.table, s.field) for s in bound],
        ),
        severity_rank=row.severity_rank,
        attribution_score=row.attribution_score,
        significant=significant,
        confidence=confidence,
        flags=flags,
    )


def _candidate(
    ctx: CandidateContext,
    *,
    candidate_id: str,
    level: str,
    subject: Subject,
    cause: CauseEntry,
    slots: dict[str, NumericBinding],
    evidence: list[str],
    lineage: CandidateLineage,
    severity_rank: int,
    attribution_score: Decimal | None,
    significant: bool,
    confidence: ConfidenceLevel,
    flags: list[str],
) -> InsightCandidate:
    priority = base_priority(
        "T1", attribution_score if attribution_score is not None else Decimal(1), severity_rank, ctx.cfg.params
    )
    return InsightCandidate.model_validate(
        {
            "candidate_id": candidate_id,
            "task": "T1",
            "insight_type": "ROOT_CAUSE_SIGNAL",
            "level": level,
            "subject": subject,
            "cause_code": cause.cause_code,
            "slots": slots,
            "evidence_refs": evidence,
            "lineage": lineage,
            "severity_rank": severity_rank,
            "attribution_score": attribution_score,
            "significant": significant,
            "confidence": confidence,
            "dq_flags": flags,
            "action_code": cause.action_code if ctx.wants("T6") else None,
            "priority": boosted(priority, subject.id, ctx.recent_subject_ids, ctx.recent_subject_boost),
        }
    )


def _legal_candidates(ctx: CandidateContext, overdue: list[UnitView]) -> CandidateBatch:
    batch = CandidateBatch()
    if LEGAL not in ctx.cfg.allowed_cause_codes:
        return batch
    cause = ctx.cfg.cause(LEGAL)
    for project in ctx.view.projects:
        members = [
            (u, index)
            for u in overdue
            if u.project is not None and u.project.project_key == project.project_key
            for row, index in u.causes
            if row.cause_code == LEGAL
        ]
        if not members:
            continue
        candidate_id = f"C-T1-{project.project_id}-{LEGAL}"
        project_index = ctx.view.project_index(project.project_key)
        assert project_index is not None
        fields: list[FieldAssessment] = []
        evidence: list[str] = []
        rejected = False
        for spec in cause.required_evidence:
            assessment = ctx.gate.field(spec.table, spec.field)
            if assessment and assessment.reject:
                rejected = True
            elif assessment:
                fields.append(assessment)
            evidence.append(ctx.ref(PROJECT, project_index, spec.field))
        if rejected:
            batch.reject(candidate_id, "EVIDENCE_FIELD_MISSING")
            continue
        evidence += [ctx.ref(BRIDGE, index) for _, index in members]
        confidence, flags = apply_fields("HIGH", fields)
        confidence = apply_freshness(confidence, flags, ctx.gate)
        if project.is_sales_permit_issued and project.is_bank_guarantee_issued:
            flags.append("CONFLICT")
        slots = {
            "overdue_units": binding(
                "overdue_units", len(members), "COUNT", computed_ref(candidate_id, "overdue_units"), noun="căn"
            )
        }
        batch.candidates.append(
            _candidate(
                ctx,
                candidate_id=candidate_id,
                level="PROJECT",
                subject=Subject(type="project", id=project.project_id, label=project.project_name),
                cause=cause,
                slots=slots,
                evidence=evidence,
                lineage=CandidateLineage(
                    source_refs=[ctx.source(PROJECT), ctx.source(BRIDGE)],
                    calculation_refs=[ctx.calculation(s.table, s.field) for s in cause.required_evidence]
                    + [f"count({BRIDGE}.cause_code={LEGAL})"],
                ),
                severity_rank=1,
                attribution_score=None,
                significant=False,
                confidence=confidence,
                flags=flags,
            )
        )
    return batch


def t1_candidates(ctx: CandidateContext) -> CandidateBatch:
    batch = CandidateBatch()
    if not ctx.wants("T1"):
        return batch
    threshold = ctx.cfg.params.overdue_threshold_days
    overdue = [
        u
        for u in ctx.view.units_in_scope(ctx.request.analysis_scope)
        if DatasetView.is_overdue(u, threshold) and u.diagnostic is not None
    ]
    examples = _examples(overdue, ctx)
    outliers = _outlier_flags(overdue, ctx)
    for unit in overdue:
        conflicts = unit_conflicts(unit, ctx.cfg)
        for row, row_index in unit.causes:
            if row.cause_code == LEGAL:
                continue
            candidate_id = f"C-T1-{unit.unit.unit_id}-{row.severity_rank}-{row.cause_code}"
            if row.cause_code not in ctx.cfg.allowed_cause_codes:
                batch.reject(candidate_id, "CAUSE_CODE_NOT_ALLOWED")
                continue
            if unit.unit.unit_key not in examples:
                batch.reject(candidate_id, "UNITS_GROUPED")
                continue
            try:
                batch.candidates.append(
                    _unit_candidate(ctx, unit, row, row_index, candidate_id, conflicts, outliers.get(unit.unit.unit_key))
                )
            except _Rejected as rejection:
                batch.reject(candidate_id, rejection.reason_code)
    batch.extend(_legal_candidates(ctx, overdue))
    return batch
