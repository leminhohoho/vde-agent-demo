"""Contracts of the Insight Agent as Pydantic v2 models (spec §3.3, §4.1–4.2, §6.5, §7.5, §9.5).

The spec writes the schemas in Zod syntax; field and enum names are kept exactly. Every model is
`extra="forbid"` and frozen. Business numbers and costs are `decimal.Decimal` (`Dec`): floats are
refused on input and decimals serialise to exact strings in JSON.

The sdk has no envelope or task-request model to reuse (docs/OPEN_QUESTIONS.md Q1–Q3), so all of
them live here.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    PlainValidator,
    StringConstraints,
    WithJsonSchema,
    field_validator,
)


def _to_decimal(value: object) -> Decimal:
    if isinstance(value, (bool, float)):
        raise ValueError(f"floats are not allowed for business numbers; pass a decimal string, got {value!r}")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation:
            raise ValueError(f"not a decimal number: {value!r}") from None
    else:
        raise ValueError(f"expected a decimal string, got {type(value).__name__}")
    if not result.is_finite():
        raise ValueError(f"not a finite decimal: {value!r}")
    return result


def _check_uuid(value: str) -> str:
    uuid.UUID(value)
    return value


Dec = Annotated[
    Decimal,
    PlainValidator(_to_decimal),
    PlainSerializer(str, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "description": "decimal number as a string"}),
]
"""A business number: `Decimal` in Python, an exact decimal string in JSON, never a float."""

UuidStr = Annotated[str, AfterValidator(_check_uuid)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

Level = Literal["MARKET", "PROJECT", "ZONE", "UNIT"]
Intent = Literal["SLOW_MOVING_INVESTIGATION", "PEER_GROUP_COMPARISON", "PERFORMANCE_METRIC_LOOKUP"]
TaskCode = Literal["T1", "T2", "T3", "T5", "T6", "T7"]
CandidateTask = Literal["T1", "T2", "T3", "T5", "T7"]
InsightType = Literal["ROOT_CAUSE_SIGNAL", "CAUSE_DISTRIBUTION", "PATTERN", "MARKET_CONTEXT", "DATA_LIMITATION", "CONFLICT"]
InputArtifactType = Literal["metric", "dq", "dataset", "market_context"]
BindingUnit = Literal["DAY", "PCT", "VND", "VND_PER_M2", "RATIO", "COUNT", "SCORE"]
ConfidenceLevel = Literal["HIGH", "MEDIUM", "LOW"]
Materiality = Literal["KEY", "SUPPORTING"]
ArtifactStatus = Literal["VALID", "PARTIAL", "INVALID"]
Role = Literal["SALES_OPS", "SALES_MANAGER", "EVALUATOR"]
"""Roles of the data pack `users` table, upper-cased by the Orchestrator (D-76)."""
Provider = Literal["gemini", "openai"]
CallType = Literal["MAIN", "REPAIR", "MEMORY"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---- Input (§3.3) -----------------------------------------------------------------------------


class ArtifactRef(Contract):
    artifact_id: str
    artifact_type: InputArtifactType
    version: int
    status: Literal["VALID", "PARTIAL"]
    content_hash: Sha256Hex


class AnalysisScope(Contract):
    level: Level
    project_ids: list[str] = []
    zone_ids: list[str] = []
    unit_ids: list[str] = []


class AuthorizedScope(Contract):
    project_ids: list[str]
    zone_ids: list[str]


class UserContext(Contract):
    user_id: str
    role: Role
    authorized_scope: AuthorizedScope


class Constraints(Contract):
    max_key_insights: int | None = None
    """Default from semantic_config."""
    language: Literal["vi"] = "vi"
    deadline_ms: int = 60000


class InsightTaskRequest(Contract):
    run_id: UuidStr
    task_id: UuidStr
    attempt: int = Field(ge=1)
    fencing_token: int
    intent: Intent
    tasks: list[TaskCode] = Field(min_length=1)
    question_normalized: str = Field(max_length=1000)
    analysis_scope: AnalysisScope
    snapshot_id: str
    semantic_config_version: str
    user_context: UserContext
    input_artifact_refs: list[ArtifactRef] = Field(min_length=1)
    constraints: Constraints = Field(default_factory=Constraints)
    parent_insight_ref: str | None = None
    conversation_id: UuidStr | None = None
    """Key of the conversation part of agent memory (§9.5); absent → that part is skipped."""


class InputArtifact(Contract):
    """An input artifact as read back from its store; `content_hash` is computed from `payload`."""

    artifact_id: str
    artifact_type: InputArtifactType
    version: int
    status: ArtifactStatus
    snapshot_id: str
    semantic_config_version: str
    content_hash: Sha256Hex
    payload: dict[str, Any]
    """Parsed by type with the models of the "Input artifact payloads" section below."""


# ---- Payload (§4.2) ---------------------------------------------------------------------------


class NumericBinding(Contract):
    slot: str
    metric_ref: str
    """`<artifact_id>#<json_path>`."""
    value: Dec
    unit: BindingUnit
    display: str
    """Formatted by the formatter, e.g. "145 ngày", "+12,4%"."""


class Subject(Contract):
    type: str
    id: str
    label: str


class Claim(Contract):
    template: str
    rendered_text: str
    numeric_bindings: list[NumericBinding]


class EvidenceRef(Contract):
    evidence_id: str
    artifact_id: str
    path: str
    kind: Literal["METRIC", "DIAGNOSTIC_ROW", "DQ", "MARKET"]


class Lineage(Contract):
    source_refs: list[str]
    calculation_refs: list[str]
    peer_rule_ref: str | None = None


class Confidence(Contract):
    level: ConfidenceLevel
    reasons: list[str]


class Recommendation(Contract):
    action_code: str
    text: str
    is_suggestion: Literal[True]
    requires_human_approval: Literal[True]


class Insight(Contract):
    insight_id: str
    candidate_id: str
    insight_type: InsightType
    level: Level
    subject: Subject
    cause_code: str | None = None
    claim: Claim
    evidence_refs: list[EvidenceRef]
    lineage: Lineage
    severity_rank: int | None = None
    attribution_score: Dec | None = None
    confidence: Confidence
    materiality: Materiality
    eligible_for_conclusion: bool
    recommendation: Recommendation | None = None
    limitations: list[str]
    conflict_with: list[str] = []


class Coverage(Contract):
    units_in_scope: int
    overdue_units: int
    units_explained: int


class Summary(Contract):
    headline_insight_ids: list[str]
    coverage: Coverage
    narrative_mode: Literal["LLM", "TEMPLATE"]


class RejectedCandidate(Contract):
    candidate_id: str
    reason_code: str


class ChartHint(Contract):
    insight_id: str
    suggested_chart: str
    metric_refs: list[str]


class Limitation(Contract):
    code: str
    message: str
    affected: list[str]


class InsightPayload(Contract):
    summary: Summary
    insights: list[Insight]
    rejected_candidates: list[RejectedCandidate]
    chart_hints: list[ChartHint]
    limitations: list[Limitation]


# ---- LLM usage (§7.5) -------------------------------------------------------------------------


class LlmUsage(Contract):
    provider: Provider
    model_id: str
    call_type: CallType
    input_tokens: int = Field(ge=0)
    """Total input, cached part included."""
    cached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    """Without thinking."""
    thinking_tokens: int = Field(ge=0)
    cost_usd: Dec | None
    """None when the model has no price in the config (PRICING_MISSING, TC-28)."""
    latency_ms: int = Field(ge=0)
    finish_reason: str


# ---- Envelope (§4.1) --------------------------------------------------------------------------


class ReadArtifactRef(Contract):
    """An artifact the task read, the generated `insight_candidates` included."""

    artifact_id: str
    artifact_type: Literal["metric", "dq", "dataset", "market_context", "insight_candidates"]
    version: int
    content_hash: Sha256Hex


class Producer(Contract):
    agent: str
    """`insight_agent@<agent_version>`."""
    prompt_version: str
    model_id: str | None
    """None when no LLM answered (TEMPLATE only)."""
    llm_usage: list[LlmUsage] = []


class ArtifactEnvelope(Contract):
    artifact_id: str
    artifact_type: Literal["insight"] = "insight"
    schema_version: Literal["insight.v2"] = "insight.v2"
    version: int = Field(ge=1)
    status: ArtifactStatus
    producer: Producer
    snapshot_refs: list[str]
    semantic_config_version: str
    input_artifact_refs: list[ReadArtifactRef]
    evidence_refs: list[str]
    content_hash: Sha256Hex
    """SHA-256 of the canonical JSON of `payload`."""
    limitations: list[Limitation]
    payload: InsightPayload


# ---- Internal schemas (§6.5) ------------------------------------------------------------------


class CandidateLineage(Contract):
    source_refs: list[str]
    calculation_refs: list[str]


class InsightCandidate(Contract):
    candidate_id: str
    task: CandidateTask
    insight_type: InsightType
    level: Level
    subject: Subject
    cause_code: str | None = None
    slots: dict[str, NumericBinding]
    evidence_refs: list[str]
    lineage: CandidateLineage
    severity_rank: int | None = None
    attribution_score: Dec | None = None
    n_eff: int | None = None
    coverage: Dec | None = None
    significant: bool
    confidence: ConfidenceLevel
    dq_flags: list[str]
    action_code: str | None = None
    priority: Dec


class RecentRef(Contract):
    insight_id: str
    subject: Subject
    insight_type: str
    cause_code: str | None = None
    level: str


class UserPref(Contract):
    preferred_level: Level | None = None
    verbosity: Literal["SHORT", "NORMAL"] = "NORMAL"
    show_recommendation: bool = True


class MemoryContext(Contract):
    """Filtered agent memory (step 2): references and preferences only, no numbers, no free text."""

    recent_refs: list[RecentRef] = Field(max_length=20)
    topic_summary: list[str] = Field(max_length=10)
    user_pref: UserPref = Field(default_factory=UserPref)
    stale_refs_dropped: int = Field(ge=0)

    @classmethod
    def empty(cls) -> MemoryContext:
        return cls(recent_refs=[], topic_summary=[], stale_refs_dropped=0)


class InsightRef(Contract):
    """Payload of an INSIGHT_REF memory record (§9.5): no rendered_text, no numeric_bindings."""

    insight_id: str
    artifact_id: str
    subject: Subject
    insight_type: InsightType
    cause_code: str | None = None
    level: Level
    materiality: Materiality


class SlotRef(Contract):
    """One `{{slot}}` of a draft template and the candidate slot that fills it.

    A list of these replaces the spec's `slot_map` record: strict structured output rejects objects
    with dynamic `additionalProperties` (docs/OPEN_QUESTIONS.md Q10).
    """

    slot: str
    """Slot name in the template, e.g. "dom"."""
    ref: str
    """`<candidate_id>.<slot>`, e.g. "C-011-1.dom"."""

    @field_validator("ref")
    @classmethod
    def _candidate_dot_slot(cls, ref: str) -> str:
        candidate_id, dot, slot = ref.partition(".")
        if not dot or not candidate_id or not slot or "." in slot:
            raise ValueError(f"ref must be '<candidate_id>.<slot>' with exactly one dot, got {ref!r}")
        return ref


class DraftItem(Contract):
    candidate_ids: list[str] = Field(min_length=1)
    """Several candidates may be merged into one idea."""
    template: str = Field(max_length=400)
    """Sentence with `{{slot}}` placeholders."""
    slots: list[SlotRef]
    # TODO(P2, GR-01/GR-03 in validation.py): every `{{slot}}` of `template` has a SlotRef and vice
    # versa, and each ref names a candidate/slot given to the model. Checked per item by the
    # validator, not here, so one bad item falls back to TEMPLATE instead of failing the whole draft.
    limitation_text: str | None = Field(default=None, max_length=300)
    recommendation_text: str | None = Field(default=None, max_length=300)

    @field_validator("slots")
    @classmethod
    def _unique_slots(cls, slots: list[SlotRef]) -> list[SlotRef]:
        names = [s.slot for s in slots]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"slot names must be unique within an item: {', '.join(duplicates)}")
        return slots


class SkippedItem(Contract):
    candidate_id: str
    reason: str = Field(max_length=100)


class LlmInsightDraft(Contract):
    """Output of [LLM-1] and [LLM-R]: no numeric field for the model to fill."""

    selected: list[DraftItem] = Field(max_length=12)
    skipped: list[SkippedItem]


# ---- Input artifact payloads ------------------------------------------------------------------
# TODO(data-agent-contract): the Data Agent spec does not define these payloads yet; the shapes are
# ours, the row columns are the DW Schema v3.1.0 names (a subset per table: what Insight reads).
# Nullable columns stay `| None` so the Sufficiency Gate can measure missing rates.


class ProjectRow(Contract):
    """dim_project_profile."""

    project_key: int
    project_id: str
    project_name: str
    market_id: str
    segment: Literal["AFFORDABLE", "MID", "MID_HIGH", "LUXURY"]
    is_sales_permit_issued: bool
    is_bank_guarantee_issued: bool


class ZoneRow(Contract):
    """dim_zone_master."""

    zone_key: int
    zone_id: str
    project_key: int
    zone_name: str


class UnitRow(Contract):
    """dim_unit_master."""

    unit_key: int
    unit_id: str
    unit_code: str
    project_key: int
    zone_key: int
    unit_type: Literal["STUDIO", "1PN", "2PN", "3PN", "4PN", "PENTHOUSE"]
    net_area_m2: Dec
    floor_number: int
    floor_band: Literal["LOW", "MID", "HIGH", "TOP"]
    balcony_orientation: Literal["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    view_primary_type: Literal["RIVER", "PARK", "POOL", "CITY_OPEN", "OBSTRUCTED"]


class InventoryRow(Contract):
    """fact_unit_inventory_snapshot."""

    snapshot_date_key: int
    unit_key: int
    project_key: int
    zone_key: int
    channel_key: int
    launch_batch_id: str
    inventory_status: Literal["AVAILABLE", "BOOKED", "SOLD"]
    unsold_days_dom: int
    is_overdue_flag: bool
    asking_price_vnd: int | None
    asking_price_per_m2: int | None
    subsidy_duration_mo: int
    base_commission_pct: Dec
    spiff_bonus_vnd: int | None


class DiagnosticRow(Contract):
    """dm_unit_friction_diagnostics (+ two peer columns the DW doc lacks)."""

    diagnostic_id: str
    snapshot_date_key: int
    unit_key: int
    unit_code: str
    project_name: str
    zone_name: str
    unsold_days_dom: int
    price_spread_vs_peer_pct: Dec | None
    ticket_size_vs_income_ratio: Dec | None
    physical_defect_penalty: int
    thermal_view_penalty: int
    secondary_price_gap_pct: Dec | None
    funnel_dropoff_rate_pct: Dec | None
    primary_cause_code: str
    recommended_action: str
    is_peer_sample_constrained: bool
    """TODO(data-agent-contract): not a column of any DW v3.1.0 table (docs/OPEN_QUESTIONS.md Q8a)."""
    peer_count: int
    """TODO(data-agent-contract): not a column of any DW v3.1.0 table (docs/OPEN_QUESTIONS.md Q8a)."""


class CauseRow(Contract):
    """unit_diagnostic_causes (bridge)."""

    diagnostic_id: str
    cause_code: str
    unit_key: int
    snapshot_date_key: int
    severity_rank: int
    attribution_score: Dec
    evidence_artifact_id: str | None = None


class DatasetPayload(Contract):
    """`dataset` artifact: a slice of the diagnostic mart, the bridge and the dimensions they need."""

    source_refs: list[str]
    """`<table>@<snapshot_id>` for every table in the slice."""
    dim_project_profile: list[ProjectRow] = []
    dim_zone_master: list[ZoneRow] = []
    dim_unit_master: list[UnitRow] = []
    fact_unit_inventory_snapshot: list[InventoryRow] = []
    dm_unit_friction_diagnostics: list[DiagnosticRow] = []
    unit_diagnostic_causes: list[CauseRow] = []


DqStatus = Literal["PASS", "WARN", "FAIL"]


class DqFieldResult(Contract):
    table: str
    field: str
    status: DqStatus
    is_primary: bool
    """Primary field of a candidate (spec 6.2) vs secondary field (missing-rate tiers of 5.5)."""
    missing_rate_pct: Dec
    missing_rate_overdue_pct: Dec | None = None
    """For MNAR: missing rate among overdue units."""
    missing_rate_sold_pct: Dec | None = None
    """For MNAR: missing rate among sold units."""


class DqPayload(Contract):
    """`dq` artifact (Data Quality Result)."""

    overall_status: DqStatus
    snapshot_date: str
    """ISO date of `snapshot_manifest.snapshot_date`."""
    data_as_of: str
    """ISO datetime of the newest inventory/price load (freshness, spec 5.5)."""
    fields: list[DqFieldResult]


class MetricValue(Contract):
    metric_id: str
    calculation_ref: str
    """Formula id + version, copied into the insight's lineage."""
    subject: Subject
    dimension: str | None = None
    """For T3: floor_band, balcony_orientation, … (None for a plain subject metric)."""
    group: str | None = None
    value: Dec | None
    unit: BindingUnit
    n: int | None = None


class MacroRow(Contract):
    """fact_market_macro_monthly."""

    macro_record_id: str
    date_key: int
    """Last day of the month, YYYYMMDD."""
    market_id: str
    segment: str
    floating_mortgage_rate_pct: Dec
    months_of_inventory_moi: Dec | None
    absorption_rate_pct: Dec
    median_household_income_vnd: int
    macro_price_to_income_ratio: Dec | None


class MarketContextPayload(Contract):
    """`market_context` artifact (optional input, T5)."""

    source_refs: list[str]
    fact_market_macro_monthly: list[MacroRow]


class MetricPayload(Contract):
    """`metric` artifact."""

    metrics: list[MetricValue]
