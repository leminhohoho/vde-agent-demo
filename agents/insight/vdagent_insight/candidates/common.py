"""Shared pieces of the Candidate Engine (pipeline step 4): the task context, the result batch,
bindings, evidence references and the mart–bridge consistency checks (BR-03, BR-04).

Pure. References have the spec's `<artifact_id>#<json_path>` form; values computed by the engine
itself (T2 shares, T3 medians) point into the task's own `insight_candidates` artifact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..contracts import (
    BindingUnit,
    ConfidenceLevel,
    InsightCandidate,
    InsightTaskRequest,
    MetricPayload,
    NumericBinding,
    RejectedCandidate,
    TaskCode,
)
from ..formatting import format_value
from ..gate import FieldAssessment, GateResult, cap, downgrade
from ..settings import SemanticConfig
from ..view import DatasetView, UnitView, json_path

CANDIDATES_ARTIFACT = "insight_candidates"
"""Artifact id prefix of values computed in this task (spec §4.1 lists it among the inputs)."""


@dataclass(frozen=True)
class CandidateContext:
    request: InsightTaskRequest
    cfg: SemanticConfig
    view: DatasetView
    gate: GateResult
    dataset_id: str
    metric: MetricPayload | None = None
    metric_id: str | None = None
    recent_subject_ids: frozenset[str] = frozenset()
    """Subjects of INSIGHT_REFs in memory (§9.5): priority boost only."""
    recent_subject_boost: Decimal = Decimal(0)

    def wants(self, task: TaskCode) -> bool:
        return task in self.request.tasks

    def ref(self, table: str, index: int, column: str | None = None) -> str:
        return f"{self.dataset_id}#{json_path(table, index, column)}"

    def source(self, table: str) -> str:
        return f"{table}@{self.request.snapshot_id}"

    def calculation(self, table: str, column: str) -> str:
        return f"{table}.{column}@{self.request.semantic_config_version}"


@dataclass
class CandidateBatch:
    candidates: list[InsightCandidate] = field(default_factory=list)
    rejected: list[RejectedCandidate] = field(default_factory=list)

    def reject(self, candidate_id: str, reason_code: str) -> None:
        self.rejected.append(RejectedCandidate(candidate_id=candidate_id, reason_code=reason_code))

    def extend(self, other: CandidateBatch) -> None:
        self.candidates += other.candidates
        self.rejected += other.rejected


def binding(
    slot: str, value: Decimal | int, unit: BindingUnit, metric_ref: str, *, noun: str | None = None, signed: bool = False
) -> NumericBinding:
    number = Decimal(value)
    return NumericBinding(
        slot=slot, metric_ref=metric_ref, value=number, unit=unit, display=format_value(number, unit, noun=noun, signed=signed)
    )


def computed_ref(candidate_id: str, slot: str) -> str:
    return f"{CANDIDATES_ARTIFACT}#/{candidate_id}/slots/{slot}"


def apply_fields(level: ConfidenceLevel, fields: list[FieldAssessment]) -> tuple[ConfidenceLevel, list[str]]:
    """Confidence effect of the DQ assessments of every field a candidate uses."""
    flags: list[str] = []
    steps = 0
    force_low = False
    for f in fields:
        flags += [x for x in f.flags if x not in flags]
        steps += f.downgrade
        force_low = force_low or f.force_low
    level = downgrade(level, steps)
    return ("LOW" if force_low else level), flags


def apply_freshness(level: ConfidenceLevel, flags: list[str], gate: GateResult) -> ConfidenceLevel:
    tier = gate.freshness
    if tier.flag and tier.flag not in flags:
        flags.append(tier.flag)
    level = downgrade(level, tier.downgrade)
    return "LOW" if tier.force_low else level


def cap_single_source(level: ConfidenceLevel, source_tables: set[str]) -> ConfidenceLevel:
    """HIGH needs evidence from at least two independent sources (§5.2): here, two DW tables."""
    return level if len(source_tables) >= 2 else cap(level, "MEDIUM")


# ---- mart–bridge consistency (BR-03, BR-04) ----------------------------------------------------

PRIMARY_CAUSE_MISMATCH = "PRIMARY_CAUSE_MISMATCH"
ATTRIBUTION_SUM_MISMATCH = "ATTRIBUTION_SUM_MISMATCH"


def unit_conflicts(unit: UnitView, cfg: SemanticConfig) -> list[str]:
    """Why the mart and the bridge disagree for this unit (empty when they agree)."""
    if unit.diagnostic is None or not unit.causes:
        return []
    problems = []
    rank_one = [row.cause_code for row, _ in unit.causes if row.severity_rank == 1]
    if rank_one != [unit.diagnostic.primary_cause_code]:
        problems.append(PRIMARY_CAUSE_MISMATCH)
    total = sum((row.attribution_score for row, _ in unit.causes), Decimal(0))
    if abs(total - 1) > cfg.params.attribution_sum_tolerance:
        problems.append(ATTRIBUTION_SUM_MISMATCH)
    return problems
