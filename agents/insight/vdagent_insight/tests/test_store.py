"""SQLite store of the Insight Agent (D-51): immutable artifacts, idempotency, LLM usage."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ..contracts import ArtifactEnvelope, LlmUsage
from ..llm.usage import UsageSink
from ..store import InsightStore

TZ7 = datetime.fromisoformat("2026-07-01T10:00:00+07:00").tzinfo


def envelope(artifact_id: str = "ART-INSIGHT-1", status: str = "VALID") -> ArtifactEnvelope:
    return ArtifactEnvelope.model_validate(
        {
            "artifact_id": artifact_id, "version": 1, "status": status,
            "producer": {"agent": "insight_agent@2.0.0", "prompt_version": "p", "model_id": None},
            "snapshot_refs": ["SNAP-20260630-01"], "semantic_config_version": "3.1.0", "input_artifact_refs": [],
            "evidence_refs": [], "content_hash": "a" * 64, "limitations": [],
            "payload": {
                "summary": {
                    "headline_insight_ids": [],
                    "coverage": {"units_in_scope": 0, "overdue_units": 0, "units_explained": 0},
                    "narrative_mode": "TEMPLATE",
                },
                "insights": [], "rejected_candidates": [], "chart_hints": [], "limitations": [],
            },
        }
    )  # fmt: skip


def usage(cost: str | None = "0.002") -> LlmUsage:
    return LlmUsage(
        provider="gemini", model_id="gemini-3.5-flash-lite", call_type="MAIN", input_tokens=10, cached_input_tokens=0,
        output_tokens=5, thinking_tokens=0, cost_usd=None if cost is None else Decimal(cost), latency_ms=3, finish_reason="STOP",
    )  # fmt: skip


async def test_an_artifact_is_found_by_its_idempotency_key_and_id(tmp_path: Path) -> None:
    store = InsightStore(tmp_path / "var" / "insight_artifacts.db")
    assert await store.find("KEY-1") is None
    saved = await store.save(envelope(), "KEY-1")
    assert saved == envelope()
    assert await store.find("KEY-1") == envelope() and await store.get("ART-INSIGHT-1") == envelope()


async def test_e17_a_second_write_with_the_same_key_returns_the_first_artifact(tmp_path: Path) -> None:
    store = InsightStore(tmp_path / "db.sqlite")
    await store.save(envelope("ART-A"), "KEY")
    kept = await store.save(envelope("ART-B", "PARTIAL"), "KEY")
    assert kept.artifact_id == "ART-A" and await store.get("ART-B") is None


async def test_candidates_are_stored_as_their_own_artifact(tmp_path: Path) -> None:
    store = InsightStore(tmp_path / "db.sqlite")
    await store.save_json("ART-CAND-1", "insight_candidates", {"candidates": []}, "b" * 64)
    assert await store.get_json("ART-CAND-1") == {"candidates": []}


async def test_supersede_marks_the_old_version(tmp_path: Path) -> None:
    store = InsightStore(tmp_path / "db.sqlite")
    await store.save(envelope("ART-OLD"), "K1")
    await store.save(envelope("ART-NEW"), "K2")
    await store.supersede("ART-OLD", "ART-NEW")
    assert await store.superseded_by("ART-OLD") == "ART-NEW" and await store.superseded_by("ART-NEW") is None


async def test_usage_is_recorded_and_summed_per_local_day(tmp_path: Path) -> None:
    now = datetime(2026, 7, 1, 10, 0, tzinfo=TZ7)
    store = InsightStore(tmp_path / "db.sqlite", clock=lambda: now)
    sink: UsageSink = store
    await sink.record("task-1", [usage("0.002"), usage(None)])
    await sink.record("task-2", [usage("0.003")])
    assert await sink.spent_today() == Decimal("0.005")
    later = InsightStore(tmp_path / "db.sqlite", clock=lambda: datetime(2026, 7, 2, 0, 1, tzinfo=TZ7))
    assert await later.spent_today() == Decimal(0)
    assert await store.usage_count("task-1") == 2
