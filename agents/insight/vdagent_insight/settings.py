"""Settings (pipeline step 2): this plugin folder's `.env` and the versioned `config/*.yaml`.

`.env`: every plugin shares the Backend's process, so the file is read with `dotenv_values()` into a
mapping and `os.environ` is never modified (SDK rule R11).

`config/semantic_insight*.yaml` (spec §5.4, §7.6) and `config/llm.yaml` (spec §7.5) hold every
threshold, template, model name and price (luật 2). The loaders validate them strictly: unknown or
missing keys, and any YAML float (luật 3: decimals are quoted strings), raise `ConfigError` naming
the offending key. `SemanticConfigRegistry` serves each semantic_config version, cached.
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from vdagent_sdk import PluginConfigError

from .contracts import BindingUnit, Dec, DiagnosticRow, InventoryRow, ProjectRow

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"  # agents/<name>/.env
CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"  # agents/<name>/config
SEMANTIC_GLOB = "semantic_insight*.yaml"
REQUIRED_VARS: tuple[str, ...] = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "LLM_MODEL")
DEFAULT_LLM_TIMEOUT_S = 120.0
DEFAULT_EMBED_MODEL = "openai/text-embedding-3-small"


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_base_url: str
    llm_model: str
    llm_timeout_s: float
    embed_model: str


def read_env(env_file: Path = ENV_FILE) -> dict[str, str]:
    """The process environment overlaid with `env_file` (the file wins; a missing file is fine)."""
    from_file = dotenv_values(env_file) if env_file.is_file() else {}
    return {**os.environ, **{k: v for k, v in from_file.items() if v is not None}}


def load_settings(env: Mapping[str, str]) -> Settings:
    """Read and validate the model settings; `PluginConfigError` names the offending variable."""
    for var in REQUIRED_VARS:
        if not env.get(var, "").strip():
            raise PluginConfigError(f"missing required environment variable {var}")
    raw_timeout = env.get("LLM_TIMEOUT_S", "").strip()
    try:
        llm_timeout_s = float(raw_timeout) if raw_timeout else DEFAULT_LLM_TIMEOUT_S
    except ValueError:
        raise PluginConfigError(f"LLM_TIMEOUT_S must be a number; got {raw_timeout!r}") from None
    if llm_timeout_s <= 0:
        raise PluginConfigError(f"LLM_TIMEOUT_S must be positive; got {llm_timeout_s:g}")
    return Settings(
        openai_api_key=env["OPENAI_API_KEY"].strip(),
        openai_base_url=env["OPENAI_BASE_URL"].strip(),
        llm_model=env["LLM_MODEL"].strip(),
        llm_timeout_s=llm_timeout_s,
        embed_model=env.get("EMBED_MODEL", "").strip() or DEFAULT_EMBED_MODEL,
    )


# ---- Versioned YAML config --------------------------------------------------------------------


class ConfigError(PluginConfigError):
    """A config file is missing, unreadable or invalid; the message names the file and the key."""


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _read_yaml(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name}: invalid YAML: {exc}") from None


def _reject_floats(node: Any, path: Path, where: str = "") -> None:
    if isinstance(node, float):
        raise ConfigError(f"{path.name}: {where}: float {node!r} is not allowed; quote decimals as strings")
    if isinstance(node, dict):
        for key, value in node.items():
            _reject_floats(value, path, f"{where}.{key}" if where else str(key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _reject_floats(value, path, f"{where}[{i}]")


def _validate[M: BaseModel](model: type[M], data: Any, path: Path) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ConfigError(f"{path.name}: {problems}") from None


# semantic_insight.yaml (spec §5.4, §7.6)

ParamStatus = Literal["APPROVED", "PENDING"]


class PeerTiers(_Config):
    compare_min: int
    describe_min: int


class MissingRateTiers(_Config):
    """Inclusive upper bounds (%) of a secondary field's missing rate (spec 5.5): ≤none_max fine,
    ≤note_max DQ_NOTE, ≤warn_max DQ_WARN, ≤describe_only_max DQ_DESCRIBE_ONLY, above → FIELD_EXCLUDED."""

    none_max: Dec
    note_max: Dec
    warn_max: Dec
    describe_only_max: Dec


class CoverageTiers(_Config):
    """Lower bounds (%) of zone/project coverage: full, PARTIAL_COVERAGE, LOW_COVERAGE; below
    `low_min` → INSUFFICIENT_COVERAGE (spec 5.5)."""

    full_min: Dec
    partial_min: Dec
    low_min: Dec


class PriorityWithoutRank(_Config):
    """Priority of candidates that have no severity_rank (spec 6.4 step 1)."""

    T2: Dec
    T3: Dec
    T5: Dec
    T7: Dec


class SemanticParams(_Config):
    overdue_threshold_days: int
    peer_area_tolerance_pct: Dec
    peer_tiers: PeerTiers
    physical_defect_trigger: int
    thermal_penalty_trigger: int
    subsidy_min_months: int
    funnel_dropoff_trigger_pct: Dec
    low_commission_max_pct: Dec
    missing_rate_tiers: MissingRateTiers
    coverage_tiers: CoverageTiers
    min_group_size: int
    min_effect_size_days: int
    outlier_iqr_warn: Dec
    outlier_iqr_exclude: Dec
    outlier_max_excluded_pct: Dec
    freshness_warn_hours: int
    freshness_error_hours: int
    mnar_gap_pct: Dec
    max_key_insights: int
    attribution_sum_tolerance: Dec
    max_units_in_context: int
    unit_examples_per_cause: int
    min_cause_share_pct: Dec
    conflict_tolerance_pct: Dec
    significance_confidence_pct: Dec
    bootstrap_iterations: int
    bootstrap_seed: int
    priority_without_rank: PriorityWithoutRank


class _ParamEntry(_Config):
    value: Any
    status: ParamStatus
    source: str


EvidenceTable = Literal["dm_unit_friction_diagnostics", "fact_unit_inventory_snapshot", "dim_project_profile"]
EVIDENCE_TABLE_MODELS: dict[str, type[BaseModel]] = {
    "dm_unit_friction_diagnostics": DiagnosticRow,
    "fact_unit_inventory_snapshot": InventoryRow,
    "dim_project_profile": ProjectRow,
}


class EvidenceSpec(_Config):
    """One DW column backing a cause (spec 6.2 "Bằng chứng tối thiểu")."""

    slot: str
    table: EvidenceTable
    field: str
    unit: BindingUnit | None = None
    """None: evidence only (e.g. a boolean flag), no numeric slot."""
    noun: str | None = None
    signed: bool = False


class CauseEntry(_Config):
    cause_code: str
    cause_label_vi: str
    action_code: str
    template: str
    """TEMPLATE fallback sentence for ROOT_CAUSE_SIGNAL (spec 8.2)."""
    recommendation_text: str
    required_evidence: list[EvidenceSpec]
    """Missing or DQ-FAIL → the candidate is rejected (EVIDENCE_FIELD_MISSING)."""
    supplementary_evidence: list[EvidenceSpec] = []
    """Used when present and not excluded by the gate."""
    uses_peer_group: bool = False
    """The claim compares with the DW peer group: peer tiers and BR-07 apply."""


class _SemanticRaw(_Config):
    version: str = Field(min_length=1)
    params: dict[str, _ParamEntry]
    causes: list[CauseEntry] = Field(min_length=1)
    insight_templates: dict[str, str]
    forbidden_phrases: list[str]
    english_whitelist: list[str]


class _SemanticFile(_Config):
    semantic_config: _SemanticRaw


class SemanticConfig(_Config):
    version: str
    params: SemanticParams
    param_status: dict[str, ParamStatus]
    causes: tuple[CauseEntry, ...]
    insight_templates: dict[str, str]
    forbidden_phrases: tuple[str, ...]
    """NFC + lowercase, ready for GR-02 matching."""
    english_whitelist: tuple[str, ...]

    @property
    def allowed_cause_codes(self) -> frozenset[str]:
        return frozenset(c.cause_code for c in self.causes)

    @property
    def cause_action_mapping(self) -> dict[str, str]:
        return {c.cause_code: c.action_code for c in self.causes}

    def cause(self, cause_code: str) -> CauseEntry:
        for entry in self.causes:
            if entry.cause_code == cause_code:
                return entry
        raise KeyError(cause_code)


def normalize_phrase(text: str) -> str:
    """GR-02 matching form: Unicode NFC, lowercase."""
    return unicodedata.normalize("NFC", text).lower()


def load_semantic_config(path: Path) -> SemanticConfig:
    data = _read_yaml(path)
    _reject_floats(data, path)
    raw = _validate(_SemanticFile, data, path).semantic_config
    params = _validate(SemanticParams, {k: e.value for k, e in raw.params.items()}, path)
    codes = [c.cause_code for c in raw.causes]
    duplicates = sorted({c for c in codes if codes.count(c) > 1})
    if duplicates:
        raise ConfigError(f"{path.name}: duplicate cause_code {', '.join(duplicates)}")
    for entry in raw.causes:
        for spec in (*entry.required_evidence, *entry.supplementary_evidence):
            if spec.field not in EVIDENCE_TABLE_MODELS[spec.table].model_fields:
                raise ConfigError(f"{path.name}: {entry.cause_code}: {spec.table} has no column {spec.field}")
    return SemanticConfig(
        version=raw.version,
        params=params,
        param_status={k: e.status for k, e in raw.params.items()},
        causes=tuple(raw.causes),
        insight_templates=dict(raw.insight_templates),
        forbidden_phrases=tuple(normalize_phrase(p) for p in raw.forbidden_phrases),
        english_whitelist=tuple(raw.english_whitelist),
    )


class SemanticConfigRegistry:
    """Every `semantic_insight*.yaml` of a folder, by version; read once, on first use (call
    `versions()` from `setup()` so no turn blocks on file I/O)."""

    def __init__(self, config_dir: Path = CONFIG_DIR) -> None:
        self._dir = config_dir
        self._by_version: dict[str, SemanticConfig] | None = None

    def _index(self) -> dict[str, SemanticConfig]:
        if self._by_version is None:
            by_version: dict[str, SemanticConfig] = {}
            for path in sorted(self._dir.glob(SEMANTIC_GLOB)):
                cfg = load_semantic_config(path)
                if cfg.version in by_version:
                    raise ConfigError(f"{path.name}: semantic_config version {cfg.version!r} defined twice")
                by_version[cfg.version] = cfg
            self._by_version = by_version
        return self._by_version

    def versions(self) -> list[str]:
        return sorted(self._index())

    def get(self, version: str) -> SemanticConfig:
        try:
            return self._index()[version]
        except KeyError:
            raise ConfigError(f"no semantic_config version {version!r} in {self._dir}") from None


# llm.yaml (spec §7.5)


class PrimaryModel(_Config):
    provider: Literal["gemini"]
    model_id: str
    thinking_level: Literal["minimal"]
    """Reasoning off (luật 6)."""


class FallbackModel(_Config):
    provider: Literal["openai"]
    model_id: str
    api: Literal["responses"]
    reasoning_effort: Literal["none"]


class RepairPolicy(_Config):
    reasoning: Literal["low"]
    max_attempts: Literal[1]


class LlmLimits(_Config):
    max_candidates_in_context: int = Field(gt=0)
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    max_selected_insights: int = Field(gt=0)
    timeout_ms: int = Field(gt=0)
    transient_retries: int = Field(ge=0)
    transient_backoff_ms: int = Field(ge=0)


class ModelPrice(_Config):
    """USD per 1M tokens."""

    input: Dec
    cached_input: Dec
    output: Dec


class MemoryConfig(_Config):
    enabled: bool
    max_refs_per_conversation: int = Field(gt=0)
    conversation_ttl_days: int = Field(gt=0)
    recent_subject_boost: Dec
    job_timeout_s: Dec
    async_jobs: list[Literal["extract_user_pref", "compact_topics"]]


class BudgetConfig(_Config):
    daily_usd: Dec
    run_cost_alert_multiplier: Dec
    hidden_thinking_alert_tokens: int


class LlmConfig(_Config):
    version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    primary: PrimaryModel
    fallback: FallbackModel
    repair: RepairPolicy
    limits: LlmLimits
    pricing_usd_per_1m: dict[str, ModelPrice]
    memory: MemoryConfig
    budget: BudgetConfig

    def price_for(self, model_id: str) -> ModelPrice | None:
        """None → the call's `cost_usd` is null and PRICING_MISSING is warned (TC-28)."""
        return self.pricing_usd_per_1m.get(model_id)


class _LlmFile(_Config):
    insight_llm_config: LlmConfig


def load_llm_config(path: Path) -> LlmConfig:
    data = _read_yaml(path)
    _reject_floats(data, path)
    return _validate(_LlmFile, data, path).insight_llm_config
