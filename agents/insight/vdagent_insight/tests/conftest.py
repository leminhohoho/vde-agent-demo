"""Test configuration of the Insight Agent.

`@pytest.mark.datapack` marks tests that need the full real-estate data pack (`export/` at the repo
root, not in git; `INSIGHT_DATAPACK_DIR` overrides the location). They are skipped when it is absent.
A small cut of it lives in `fixtures/export_sample/` for the tests that always run.

`@pytest.mark.live` marks tests that call the real LLM providers (they cost money). They run only
with `INSIGHT_LIVE=1` and the keys in `agents/insight/.env` (or the environment); otherwise skipped.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[4]
DATAPACK_DIR = Path(os.environ.get("INSIGHT_DATAPACK_DIR", REPO_ROOT / "export"))
EXPORT_SAMPLE_DIR = Path(__file__).parent / "fixtures" / "export_sample"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
LIVE_KEYS = ("GEMINI_API_KEY", "OPENAI_API_KEY")


def live_env() -> dict[str, str]:
    """Provider keys from the plugin .env over the process environment (values never printed)."""
    from_file = dotenv_values(ENV_FILE) if ENV_FILE.is_file() else {}
    merged = {**os.environ, **{k: v for k, v in from_file.items() if v}}
    return {k: v for k, v in merged.items() if k in (*LIVE_KEYS, "OPENAI_BASE_URL") and v}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "datapack: needs the full data pack in export/ (skipped when absent)")
    config.addinivalue_line("markers", "live: calls the real LLM providers (INSIGHT_LIVE=1 and keys required)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skips: dict[str, pytest.MarkDecorator] = {}
    if not (DATAPACK_DIR / "snapshot_manifest.csv").is_file():
        skips["datapack"] = pytest.mark.skip(reason=f"data pack not found at {DATAPACK_DIR}")
    missing = [k for k in LIVE_KEYS if k not in live_env()]
    if os.environ.get("INSIGHT_LIVE") != "1" or missing:
        reason = f"missing keys {missing}" if missing else "set INSIGHT_LIVE=1 to call the real providers"
        skips["live"] = pytest.mark.skip(reason=reason)
    for item in items:
        for marker, skip in skips.items():
            if marker in item.keywords:
                item.add_marker(skip)
