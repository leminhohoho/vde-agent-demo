"""Versioned config (spec §5.4, §7.5, §7.6): `config/semantic_insight.yaml` and `config/llm.yaml`."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from ..settings import (
    CONFIG_DIR,
    ConfigError,
    SemanticConfigRegistry,
    load_llm_config,
    load_semantic_config,
)

SEMANTIC = CONFIG_DIR / "semantic_insight.yaml"
LLM = CONFIG_DIR / "llm.yaml"

SPEC_CAUSES = {
    "LEGAL_PERMIT_BARRIER": "EXPEDITE_LEGAL_PROCEDURES",
    "SEVERE_PHYSICAL_DEFECT": "DEFECT_COMPENSATION_DISCOUNT",
    "EXTREME_THERMAL_EXPOSURE": "INSULATION_INTERIOR_PACKAGE",
    "SECONDARY_ARBITRAGE": "EXTENDED_PAYMENT_SCHEDULE",
    "LUMP_SUM_TICKET_BARRIER": "BANK_SUBSIDY_EXTENSION",
    "OVERPRICED_VS_PEER": "TARGETED_PRICE_CORRECTION",
    "LOW_SALES_INCENTIVE": "BOOST_BROKER_COMMISSION",
    "DEEP_FUNNEL_DROP_OFF": "SALES_PITCH_AUDIT",
}
SLOT = re.compile(r"\{\{\s*[a-z_]+\s*\}\}")


def mutated(tmp_path: Path, source: Path, change: Any) -> Path:
    """Copy `source` to tmp with `change(data)` applied to its parsed YAML."""
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    change(data)
    target = tmp_path / source.name
    target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target


# ---- semantic_insight.yaml --------------------------------------------------------------------


def test_shipped_semantic_config_has_the_spec_defaults() -> None:
    cfg = load_semantic_config(SEMANTIC)
    assert cfg.version
    p = cfg.params
    assert p.overdue_threshold_days == 90
    assert p.peer_area_tolerance_pct == Decimal("0.10")
    assert (p.peer_tiers.compare_min, p.peer_tiers.describe_min) == (10, 5)
    assert p.physical_defect_trigger == 25
    assert (p.thermal_penalty_trigger, p.subsidy_min_months) == (40, 24)
    assert p.funnel_dropoff_trigger_pct == Decimal("60")
    assert p.low_commission_max_pct == Decimal("1.5")
    assert (p.min_group_size, p.min_effect_size_days) == (5, 15)
    assert (p.freshness_warn_hours, p.freshness_error_hours) == (24, 72)
    assert p.mnar_gap_pct == Decimal("10")
    m = p.missing_rate_tiers
    assert (m.none_max, m.note_max, m.warn_max, m.describe_only_max) == (5, 10, 20, 40)
    c = p.coverage_tiers
    assert (c.full_min, c.partial_min, c.low_min) == (90, 70, 50)
    r = p.priority_without_rank
    assert (r.T2, r.T3, r.T5, r.T7) == (Decimal("0.5"), Decimal("0.5"), Decimal("0.3"), Decimal("0"))
    assert p.max_key_insights == 5
    assert p.attribution_sum_tolerance == Decimal("0.001")


def test_every_param_records_its_approval_status() -> None:
    cfg = load_semantic_config(SEMANTIC)
    assert set(cfg.param_status) == set(type(cfg.params).model_fields)
    assert cfg.param_status["overdue_threshold_days"] == "APPROVED"
    assert cfg.param_status["max_key_insights"] == "APPROVED"
    for assumed in ("min_group_size", "max_units_in_context", "min_cause_share_pct", "conflict_tolerance_pct"):
        assert cfg.param_status[assumed] == "PENDING"


def test_cause_catalogue_matches_spec_7_6() -> None:
    cfg = load_semantic_config(SEMANTIC)
    assert cfg.allowed_cause_codes == frozenset(SPEC_CAUSES)
    assert cfg.cause_action_mapping == SPEC_CAUSES
    assert cfg.cause("OVERPRICED_VS_PEER").cause_label_vi == "giá cao hơn nhóm tương đồng"


def test_forbidden_phrases_are_stored_nfc_lowercase() -> None:
    cfg = load_semantic_config(SEMANTIC)
    assert "chắc chắn do" in cfg.forbidden_phrases
    assert "bỏ qua hướng dẫn" in cfg.forbidden_phrases
    for phrase in cfg.forbidden_phrases:
        assert phrase == unicodedata.normalize("NFC", phrase).lower()


def test_shipped_templates_obey_gr01_and_gr02_themselves() -> None:
    cfg = load_semantic_config(SEMANTIC)
    texts = [c.template for c in cfg.causes] + [c.recommendation_text for c in cfg.causes]
    texts += list(cfg.insight_templates.values())
    assert set(cfg.insight_templates) >= {"CAUSE_DISTRIBUTION", "PATTERN", "MARKET_CONTEXT", "DATA_LIMITATION", "CONFLICT"}
    for text in texts:
        assert not re.search(r"\d", SLOT.sub("", text)), text
        lowered = unicodedata.normalize("NFC", text).lower()
        assert not any(p in lowered for p in cfg.forbidden_phrases), text


def test_a_float_in_the_yaml_is_rejected(tmp_path: Path) -> None:
    def change(d: dict[str, Any]) -> None:
        d["semantic_config"]["params"]["peer_area_tolerance_pct"]["value"] = 0.1

    with pytest.raises(ConfigError, match="peer_area_tolerance_pct"):
        load_semantic_config(mutated(tmp_path, SEMANTIC, change))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["semantic_config"]["params"].pop("overdue_threshold_days"), "overdue_threshold_days"),
        (lambda d: d["semantic_config"]["params"].update(made_up={"value": 1, "status": "PENDING"}), "made_up"),
        (lambda d: d["semantic_config"]["params"]["max_key_insights"].update(status="MAYBE"), "status"),
        (lambda d: d["semantic_config"].pop("version"), "version"),
        (lambda d: d["semantic_config"]["causes"].append(dict(d["semantic_config"]["causes"][0])), "duplicate"),
    ],
)
def test_invalid_semantic_config_names_the_problem(tmp_path: Path, change: Any, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_semantic_config(mutated(tmp_path, SEMANTIC, change))


def test_missing_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_semantic_config(tmp_path / "nope.yaml")


def test_registry_serves_each_version_and_rejects_unknown_ones(tmp_path: Path) -> None:
    shipped = load_semantic_config(SEMANTIC)

    def newer(d: dict[str, Any]) -> None:
        d["semantic_config"]["version"] = "sem-test-60"
        d["semantic_config"]["params"]["overdue_threshold_days"]["value"] = 60

    (tmp_path / "semantic_insight.yaml").write_text(SEMANTIC.read_text(encoding="utf-8"), encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()
    mutated(staging, SEMANTIC, newer).rename(tmp_path / "semantic_insight.sem-test-60.yaml")

    registry = SemanticConfigRegistry(tmp_path)
    assert registry.get(shipped.version).params.overdue_threshold_days == 90
    assert registry.get("sem-test-60").params.overdue_threshold_days == 60
    assert registry.get("sem-test-60") is registry.get("sem-test-60")
    with pytest.raises(ConfigError, match="sem-unknown"):
        registry.get("sem-unknown")


# ---- llm.yaml ---------------------------------------------------------------------------------


def test_shipped_llm_config_matches_spec_7_5() -> None:
    cfg = load_llm_config(LLM)
    assert cfg.version
    assert cfg.prompt_version
    assert (cfg.primary.provider, cfg.primary.model_id, cfg.primary.thinking_level) == (
        "gemini",
        "gemini-3.5-flash-lite",
        "minimal",
    )
    assert (cfg.fallback.provider, cfg.fallback.model_id, cfg.fallback.api, cfg.fallback.reasoning_effort) == (
        "openai",
        "gpt-6-luna",
        "responses",
        "none",
    )
    assert (cfg.repair.reasoning, cfg.repair.max_attempts) == ("low", 1)
    lim = cfg.limits
    assert (lim.max_candidates_in_context, lim.max_input_tokens, lim.max_output_tokens) == (40, 16000, 2500)
    assert (lim.max_selected_insights, lim.timeout_ms, lim.transient_retries) == (12, 20000, 1)
    price = cfg.price_for("gemini-3.5-flash-lite")
    assert price is not None
    assert (price.input, price.cached_input, price.output) == (Decimal("0.30"), Decimal("0.03"), Decimal("2.50"))
    assert cfg.price_for("unknown-model") is None
    assert cfg.memory.enabled is True and cfg.memory.job_timeout_s == 5
    assert cfg.memory.max_refs_per_conversation == 20 and cfg.memory.conversation_ttl_days == 30
    assert cfg.memory.recent_subject_boost == Decimal("0.1")
    assert cfg.budget.daily_usd == Decimal("5.0") and cfg.budget.hidden_thinking_alert_tokens == 500


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["insight_llm_config"]["repair"].update(max_attempts=2), "max_attempts"),
        (lambda d: d["insight_llm_config"]["repair"].update(reasoning="high"), "reasoning"),
        (lambda d: d["insight_llm_config"]["primary"].update(thinking_level="high"), "thinking_level"),
        (lambda d: d["insight_llm_config"]["fallback"].update(reasoning_effort="medium"), "reasoning_effort"),
        (lambda d: d["insight_llm_config"]["pricing_usd_per_1m"]["gpt-6-luna"].update(input=0.1), "input"),
        (lambda d: d["insight_llm_config"]["primary"].update(temperature="0.2"), "temperature"),
    ],
)
def test_invalid_llm_config_names_the_problem(tmp_path: Path, change: Any, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_llm_config(mutated(tmp_path, LLM, change))
