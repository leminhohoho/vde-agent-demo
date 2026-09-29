"""T7 Limitation Reporting (spec §6.2, §5.5): what is missing, weak or contradictory, said out loud.

Pure. DATA_LIMITATION candidates:
- every DQ field the gate flagged (DQ_NOTE … FIELD_EXCLUDED, MISSING_NOT_RANDOM, E05);
- stale inventory/price data (STALE_SNAPSHOT);
- zone/project coverage or sample tiers, when T2 is requested (PARTIAL/LOW/INSUFFICIENT_COVERAGE,
  SMALL_SAMPLE, GROUP_TOO_SMALL);
- overdue units with a peer-based cause: PEER_SAMPLE_CONSTRAINED (BR-07) or PEER_DATA_MISSING (E06), per
  unit in a UNIT scope, else one per zone/project with the number of units;
- no overdue unit in scope (E07, NO_OVERDUE_UNITS: an answer, not an error);
- T5 requested without a `market_context` artifact (MARKET_CONTEXT_MISSING).

CONFLICT candidates (E14), one per unit or project, with every reason in `dq_flags`:
- PRIMARY_CAUSE_MISMATCH (BR-03) and ATTRIBUTION_SUM_MISMATCH (BR-04);
- ACTION_CODE_MISMATCH: mart `recommended_action` ≠ the config mapping of its primary cause (7.6);
- SOURCE_MISMATCH: two sources of one snapshot differ by more than `conflict_tolerance_pct`
  (mart vs inventory DOM, metric artifact vs dataset);
- LEGAL_FLAGS_MISMATCH: LEGAL_PERMIT_BARRIER in the bridge of a fully permitted project.

T7 candidates are always kept by the context cut; their priority is `priority_without_rank.T7`.
"""

from __future__ import annotations

from decimal import Decimal

from ..contracts import CandidateLineage, InsightCandidate, InsightType, Level, NumericBinding, Subject
from ..gate import HUNDRED
from ..view import DatasetView, UnitView
from .common import CandidateBatch, CandidateContext, binding, computed_ref, requested_scopes, unit_conflicts
from .priority import base_priority, boosted

INVENTORY = "fact_unit_inventory_snapshot"
MART = "dm_unit_friction_diagnostics"
BRIDGE = "unit_diagnostic_causes"
PROJECT = "dim_project_profile"
LEGAL = "LEGAL_PERMIT_BARRIER"


def _candidate(
    ctx: CandidateContext,
    candidate_id: str,
    insight_type: InsightType,
    level: Level,
    subject: Subject,
    flags: list[str],
    slots: dict[str, NumericBinding] | None = None,
    evidence: list[str] | None = None,
    sources: list[str] | None = None,
) -> InsightCandidate:
    priority = base_priority("T7", None, None, ctx.cfg.params)
    return InsightCandidate.model_validate(
        {
            "candidate_id": candidate_id,
            "task": "T7",
            "insight_type": insight_type,
            "level": level,
            "subject": subject,
            "slots": slots or {},
            "evidence_refs": evidence or [],
            "lineage": CandidateLineage(source_refs=[ctx.source(t) for t in sources or []], calculation_refs=[]),
            "significant": False,
            "confidence": "HIGH",
            "dq_flags": flags,
            "priority": boosted(priority, subject.id, ctx.recent_subject_ids, ctx.recent_subject_boost),
        }
    )


def _unit_subject(u: UnitView) -> Subject:
    return Subject(type="unit", id=u.unit.unit_id, label=u.unit.unit_code)


def _scope_subject(ctx: CandidateContext, units: list[UnitView]) -> tuple[Level, Subject]:
    s = ctx.request.analysis_scope
    first = units[0] if units else None
    if s.level == "UNIT" and first:
        return "UNIT", _unit_subject(first)
    if s.level == "ZONE" and first and first.zone:
        return "ZONE", Subject(type="zone", id=first.zone.zone_id, label=first.zone.zone_name)
    if s.level in ("PROJECT", "ZONE", "UNIT") and first and first.project:
        return "PROJECT", Subject(type="project", id=first.project.project_id, label=first.project.project_name)
    return "MARKET", Subject(type="market", id="MARKET", label="thị trường")


def _dq_limitations(ctx: CandidateContext) -> list[InsightCandidate]:
    out = []
    level = ctx.request.analysis_scope.level
    for i, (key, field) in enumerate(ctx.gate.fields.items()):
        if not field.flags:
            continue
        candidate_id = f"C-T7-DQ-{field.table}-{field.field}"  # no dot: draft refs are <candidate_id>.<slot>
        slots = {
            "missing_rate": binding("missing_rate", field.missing_rate_pct, "PCT", f"{ctx.dq_id}#/fields/{i}/missing_rate_pct")
        }
        if field.mnar_gap_pct is not None:
            slots["mnar_gap"] = binding("mnar_gap", field.mnar_gap_pct, "PCT", computed_ref(candidate_id, "mnar_gap"))
        out.append(
            _candidate(
                ctx,
                candidate_id,
                "DATA_LIMITATION",
                level,
                Subject(type="field", id=key, label=field.field),
                list(field.flags),
                slots,
                [f"{ctx.dq_id}#/fields/{i}"],
                [field.table],
            )
        )
    return out


def _freshness_limitation(ctx: CandidateContext) -> list[InsightCandidate]:
    tier = ctx.gate.freshness
    if not tier.flag:
        return []
    candidate_id = f"C-T7-{tier.flag}"
    slots = {
        "data_age_hours": binding(
            "data_age_hours", ctx.gate.freshness_hours, "COUNT", computed_ref(candidate_id, "data_age_hours"), noun="giờ"
        )
    }
    subject = Subject(type="snapshot", id=ctx.request.snapshot_id, label=ctx.request.snapshot_id)
    return [
        _candidate(
            ctx,
            candidate_id,
            "DATA_LIMITATION",
            ctx.request.analysis_scope.level,
            subject,
            [tier.flag],
            slots,
            [f"{ctx.dq_id}#/data_as_of"],
        )
    ]


def _scope_limitations(ctx: CandidateContext) -> list[InsightCandidate]:
    if not ctx.wants("T2"):
        return []
    out = []
    for scope in requested_scopes(ctx):
        if scope.overdue_units == 0:
            continue
        for flag in (scope.coverage.flag, scope.sample.flag):
            if not flag:
                continue
            candidate_id = f"C-T7-{flag}-{scope.id}"

            def ref(slot: str, cid: str = candidate_id) -> str:
                return computed_ref(cid, slot)

            slots = {
                "units_valid": binding("units_valid", scope.units_valid, "COUNT", ref("units_valid"), noun="căn"),
                "overdue_units": binding("overdue_units", scope.overdue_units, "COUNT", ref("overdue_units"), noun="căn"),
                "n_eff": binding("n_eff", scope.n_eff, "COUNT", ref("n_eff"), noun="căn"),
            }
            if scope.coverage_pct is not None:
                slots["coverage"] = binding("coverage", scope.coverage_pct, "PCT", ref("coverage"))
            subject = Subject(type=scope.level.lower(), id=scope.id, label=scope.label)
            out.append(
                _candidate(
                    ctx, candidate_id, "DATA_LIMITATION", scope.level, subject, [flag], slots, [], [MART, BRIDGE, INVENTORY]
                )
            )
    return out


def _peer_problems(ctx: CandidateContext, overdue: list[UnitView]) -> list[tuple[UnitView, str]]:
    """(unit, PEER_DATA_MISSING | PEER_SAMPLE_CONSTRAINED) for overdue units with a peer-based cause."""
    out = []
    for u in overdue:
        diag = u.diagnostic
        if diag is None or u.diagnostic_index is None:
            continue
        if not any(
            row.cause_code in ctx.cfg.allowed_cause_codes and ctx.cfg.cause(row.cause_code).uses_peer_group for row, _ in u.causes
        ):
            continue
        if diag.price_spread_vs_peer_pct is None:
            out.append((u, "PEER_DATA_MISSING"))
        elif diag.is_peer_sample_constrained:
            out.append((u, "PEER_SAMPLE_CONSTRAINED"))
    return out


def _group_subject(ctx: CandidateContext, u: UnitView) -> tuple[Level, Subject]:
    if ctx.request.analysis_scope.level == "ZONE" and u.zone is not None:
        return "ZONE", Subject(type="zone", id=u.zone.zone_id, label=u.zone.zone_name)
    if u.project is not None:
        return "PROJECT", Subject(type="project", id=u.project.project_id, label=u.project.project_name)
    return "MARKET", Subject(type="market", id="MARKET", label="thị trường")


def _peer_limitations(ctx: CandidateContext, overdue: list[UnitView]) -> list[InsightCandidate]:
    """Per unit in a UNIT scope; in a ZONE/PROJECT scope one candidate per zone/project and flag, with
    the number of units (the data pack has dozens of constrained units per tower)."""
    problems = _peer_problems(ctx, overdue)
    out = []
    if ctx.request.analysis_scope.level == "UNIT":
        for u, flag in problems:
            assert u.diagnostic is not None and u.diagnostic_index is not None
            slots = None
            if flag == "PEER_SAMPLE_CONSTRAINED":
                ref = ctx.ref(MART, u.diagnostic_index, "peer_count")
                slots = {"peers": binding("peers", u.diagnostic.peer_count, "COUNT", ref, noun="căn")}
            out.append(
                _candidate(
                    ctx,
                    f"C-T7-{flag}-{u.unit.unit_id}",
                    "DATA_LIMITATION",
                    "UNIT",
                    _unit_subject(u),
                    [flag],
                    slots,
                    [ctx.ref(MART, u.diagnostic_index)],
                    [MART],
                )
            )
        return out
    groups: dict[tuple[str, str], tuple[Level, Subject, list[UnitView]]] = {}
    for u, flag in problems:
        level, subject = _group_subject(ctx, u)
        groups.setdefault((flag, subject.id), (level, subject, []))[2].append(u)
    for (flag, subject_id), (level, subject, units) in sorted(groups.items()):
        candidate_id = f"C-T7-{flag}-{subject_id}"
        slots = {"units": binding("units", len(units), "COUNT", computed_ref(candidate_id, "units"), noun="căn")}
        evidence = [ctx.ref(MART, u.diagnostic_index) for u in units if u.diagnostic_index is not None]
        out.append(_candidate(ctx, candidate_id, "DATA_LIMITATION", level, subject, [flag], slots, evidence, [MART]))
    return out


def _differs(value: Decimal, reference: Decimal, tolerance_pct: Decimal) -> bool:
    if reference == 0:
        return value != 0
    return abs(value - reference) * HUNDRED / abs(reference) > tolerance_pct


def _source_mismatch(ctx: CandidateContext, u: UnitView, evidence: list[str]) -> bool:
    tolerance = ctx.cfg.params.conflict_tolerance_pct
    mismatch = False
    if u.diagnostic and u.inventory and u.inventory_index is not None:
        if _differs(Decimal(u.diagnostic.unsold_days_dom), Decimal(u.inventory.unsold_days_dom), tolerance):
            mismatch = True
            evidence.append(ctx.ref(INVENTORY, u.inventory_index, "unsold_days_dom"))
    if ctx.metric is not None:
        for i, m in enumerate(ctx.metric.metrics):
            if m.subject.type != "unit" or m.subject.id != u.unit.unit_id or m.value is None:
                continue
            for row in (u.diagnostic, u.inventory):
                dataset_value = getattr(row, m.metric_id, None) if row is not None else None
                if isinstance(dataset_value, (int, Decimal)) and not isinstance(dataset_value, bool):
                    if _differs(m.value, Decimal(dataset_value), tolerance):
                        mismatch = True
                        evidence.append(f"{ctx.metric_id}#/metrics/{i}/value")
                    break
    return mismatch


def _unit_conflict(ctx: CandidateContext, u: UnitView) -> InsightCandidate | None:
    if u.diagnostic is None or u.diagnostic_index is None:
        return None
    evidence = [ctx.ref(MART, u.diagnostic_index)] + [ctx.ref(BRIDGE, i) for _, i in u.causes]
    flags = unit_conflicts(u, ctx.cfg)
    expected = ctx.cfg.cause_action_mapping.get(u.diagnostic.primary_cause_code)
    if expected is not None and u.diagnostic.recommended_action != expected:
        flags.append("ACTION_CODE_MISMATCH")
    if _source_mismatch(ctx, u, evidence):
        flags.append("SOURCE_MISMATCH")
    if not flags:
        return None
    return _candidate(
        ctx, f"C-T7-CONFLICT-{u.unit.unit_id}", "CONFLICT", "UNIT", _unit_subject(u), flags, None, evidence, [MART, BRIDGE]
    )


def _project_conflicts(ctx: CandidateContext, overdue: list[UnitView]) -> list[InsightCandidate]:
    out = []
    for project in ctx.view.projects:
        legal = [
            i
            for u in overdue
            if u.project is not None and u.project.project_key == project.project_key
            for row, i in u.causes
            if row.cause_code == LEGAL
        ]
        if legal and project.is_sales_permit_issued and project.is_bank_guarantee_issued:
            index = ctx.view.project_index(project.project_key)
            evidence = [ctx.ref(PROJECT, index)] if index is not None else []
            evidence += [ctx.ref(BRIDGE, i) for i in legal]
            subject = Subject(type="project", id=project.project_id, label=project.project_name)
            out.append(
                _candidate(
                    ctx,
                    f"C-T7-CONFLICT-{project.project_id}",
                    "CONFLICT",
                    "PROJECT",
                    subject,
                    ["LEGAL_FLAGS_MISMATCH"],
                    None,
                    evidence,
                    [PROJECT, BRIDGE],
                )
            )
    return out


def t7_candidates(ctx: CandidateContext) -> CandidateBatch:
    batch = CandidateBatch()
    if not ctx.wants("T7"):
        return batch
    units = ctx.view.units_in_scope(ctx.request.analysis_scope)
    threshold = ctx.cfg.params.overdue_threshold_days
    overdue = [u for u in units if DatasetView.is_overdue(u, threshold)]

    batch.candidates += _dq_limitations(ctx)
    batch.candidates += _freshness_limitation(ctx)
    batch.candidates += _scope_limitations(ctx)
    batch.candidates += _peer_limitations(ctx, overdue)
    if not overdue and (ctx.wants("T1") or ctx.wants("T2")):
        level, subject = _scope_subject(ctx, units)
        candidate_id = "C-T7-NO_OVERDUE_UNITS"
        slots = {
            "units_in_scope": binding(
                "units_in_scope", len(units), "COUNT", computed_ref(candidate_id, "units_in_scope"), noun="căn"
            )
        }
        batch.candidates.append(
            _candidate(ctx, candidate_id, "DATA_LIMITATION", level, subject, ["NO_OVERDUE_UNITS"], slots, [], [INVENTORY])
        )
    if ctx.wants("T5") and ctx.market is None:
        subject = Subject(type="artifact", id="market_context", label="market_context")
        batch.candidates.append(
            _candidate(ctx, "C-T7-MARKET_CONTEXT_MISSING", "DATA_LIMITATION", "MARKET", subject, ["MARKET_CONTEXT_MISSING"])
        )
    for u in overdue:
        conflict = _unit_conflict(ctx, u)
        if conflict is not None:
            batch.candidates.append(conflict)
    batch.candidates += _project_conflicts(ctx, overdue)
    return batch
