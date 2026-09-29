"""Test configuration of the Insight Agent.

`@pytest.mark.datapack` marks tests that need the full real-estate data pack (`export/` at the repo
root, not in git; `INSIGHT_DATAPACK_DIR` overrides the location). They are skipped when it is absent.
A small cut of it lives in `fixtures/export_sample/` for the tests that always run.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
DATAPACK_DIR = Path(os.environ.get("INSIGHT_DATAPACK_DIR", REPO_ROOT / "export"))
EXPORT_SAMPLE_DIR = Path(__file__).parent / "fixtures" / "export_sample"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "datapack: needs the full data pack in export/ (skipped when absent)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if (DATAPACK_DIR / "snapshot_manifest.csv").is_file():
        return
    skip = pytest.mark.skip(reason=f"data pack not found at {DATAPACK_DIR}")
    for item in items:
        if "datapack" in item.keywords:
            item.add_marker(skip)
