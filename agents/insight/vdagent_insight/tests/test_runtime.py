"""runtime.py: data source, task clock and providers from the plugin environment."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pytest

from ..runtime import SAMPLE_DIR, as_of_from, build_runtime, data_folder, providers_from
from ..settings import ConfigError
from .builders import llm

LOG = logging.getLogger("test")


def test_the_data_source_follows_the_environment(tmp_path: Path) -> None:
    assert data_folder({"INSIGHT_ARTIFACT_SOURCE": "fixtures"}) == ("fixtures", SAMPLE_DIR)
    assert data_folder({"INSIGHT_EXPORT_DIR": str(tmp_path / "none")}) == ("fixtures", SAMPLE_DIR)
    assert data_folder({"INSIGHT_ARTIFACT_SOURCE": "export", "INSIGHT_EXPORT_DIR": str(SAMPLE_DIR)}) == ("export", SAMPLE_DIR)
    with pytest.raises(ConfigError):
        data_folder({"INSIGHT_ARTIFACT_SOURCE": "export", "INSIGHT_EXPORT_DIR": str(tmp_path)})
    with pytest.raises(ConfigError):
        data_folder({"INSIGHT_ARTIFACT_SOURCE": "warehouse"})


def test_the_task_clock_is_pinned_to_the_snapshot_by_default() -> None:
    assert as_of_from({}, "2026-06-30") == datetime.fromisoformat("2026-07-01T08:00:00+07:00")
    assert as_of_from({"INSIGHT_AS_OF": "snapshot"}, "2026-06-30") == datetime.fromisoformat("2026-07-01T08:00:00+07:00")
    assert as_of_from({"INSIGHT_AS_OF": "now"}, "2026-06-30") is None
    assert as_of_from({"INSIGHT_AS_OF": "2026-07-05T10:00:00+07:00"}, "2026-06-30") == datetime.fromisoformat(
        "2026-07-05T10:00:00+07:00"
    )
    with pytest.raises(ConfigError):
        as_of_from({"INSIGHT_AS_OF": "yesterday"}, "2026-06-30")
    with pytest.raises(ConfigError):
        as_of_from({"INSIGHT_AS_OF": "2026-07-05T10:00:00"}, "2026-06-30")  # no time zone


def test_without_keys_or_with_llm_off_the_agent_runs_in_template_mode() -> None:
    assert providers_from({}, llm(), LOG) is None
    assert providers_from({"GEMINI_API_KEY": "k", "INSIGHT_LLM": "off"}, llm(), LOG) is None
    only_openai = providers_from({"OPENAI_API_KEY": "k"}, llm(), LOG)
    assert only_openai is not None and only_openai.fallback is None
    both = providers_from({"GEMINI_API_KEY": "k", "OPENAI_API_KEY": "k"}, llm(), LOG)
    assert both is not None and both.fallback is not None
    with pytest.raises(ConfigError):
        providers_from({"INSIGHT_FORCE_PROVIDER": "openai", "GEMINI_API_KEY": "k"}, llm(), LOG)


def test_build_runtime_reads_everything_at_startup(tmp_path: Path) -> None:
    env = {"INSIGHT_ARTIFACT_SOURCE": "fixtures", "INSIGHT_STORE_PATH": str(tmp_path / "s.db"), "INSIGHT_LLM": "off"}
    rt = build_runtime(env, LOG)
    assert rt.source == "fixtures" and rt.providers is None and rt.reader.cfg.version == "3.1.0"
    assert rt.as_of == datetime.fromisoformat("2026-07-01T08:00:00+07:00")
    assert rt.deps(memory=None).as_of == rt.as_of  # type: ignore[arg-type]
