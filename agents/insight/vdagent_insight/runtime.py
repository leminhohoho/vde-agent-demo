"""What one plugin instance runs with (built once in `setup()`): data source, config, store, providers.

Environment (this plugin's `.env` over the process environment, read with `read_env`, never written):

- `INSIGHT_ARTIFACT_SOURCE` = `export` (the data pack in `<repo>/export`, D-77) or `fixtures` (the
  81-unit cut in tests/fixtures/export_sample). Unset: `export` when that folder exists.
- `INSIGHT_EXPORT_DIR`: another data pack folder.
- `INSIGHT_STORE_PATH`: the SQLite store (default `<repo>/var/insight_artifacts.db`, D-51).
- `INSIGHT_LLM=off`: TEMPLATE only. Otherwise the providers of llm/providers.py (`GEMINI_API_KEY`,
  `OPENAI_API_KEY`, `INSIGHT_FORCE_PROVIDER`); with no key at all the agent runs in TEMPLATE mode
  and says so in the log, a forced provider without its key is a `ConfigError`.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .agent import EventSink, InsightDeps, json_event_sink
from .export_reader import ExportArtifactReader
from .llm.providers import FORCE_VAR, build_providers
from .llm.steps import LlmProviders
from .memory import InsightMemory
from .settings import CONFIG_DIR, ConfigError, LlmConfig, SemanticConfigRegistry, load_llm_config
from .store import InsightStore

REPO_ROOT = Path(__file__).resolve().parents[3]
EXPORT_DIR = REPO_ROOT / "export"
SAMPLE_DIR = Path(__file__).resolve().parent / "tests" / "fixtures" / "export_sample"
STORE_PATH = REPO_ROOT / "var" / "insight_artifacts.db"
LOCAL_TZ = timezone(timedelta(hours=7))


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)


@dataclass(frozen=True)
class InsightRuntime:
    reader: ExportArtifactReader
    registry: SemanticConfigRegistry
    llm: LlmConfig
    store: InsightStore
    providers: LlmProviders | None
    clock: Callable[[], datetime] = local_now
    events: EventSink = field(default_factory=json_event_sink)
    source: str = "export"

    def deps(self, memory: InsightMemory) -> InsightDeps:
        return InsightDeps(
            reader=self.reader, registry=self.registry, llm=self.llm, store=self.store, usage=self.store,
            memory=memory, providers=self.providers, clock=self.clock, events=self.events,
        )  # fmt: skip


def data_folder(env: Mapping[str, str]) -> tuple[str, Path]:
    source = (env.get("INSIGHT_ARTIFACT_SOURCE") or "").strip().lower()
    export = Path(env["INSIGHT_EXPORT_DIR"]) if env.get("INSIGHT_EXPORT_DIR") else EXPORT_DIR
    if source not in ("", "export", "fixtures"):
        raise ConfigError(f"INSIGHT_ARTIFACT_SOURCE must be 'export' or 'fixtures'; got {source!r}")
    if source == "fixtures" or (not source and not export.is_dir()):
        return "fixtures", SAMPLE_DIR
    if not (export / "snapshot_manifest.csv").is_file():
        raise ConfigError(f"INSIGHT_ARTIFACT_SOURCE=export but {export} has no snapshot_manifest.csv")
    return "export", export


def pack_semantic_version(folder: Path) -> str:
    with (folder / "snapshot_manifest.csv").open(encoding="utf-8", newline="") as f:
        return next(csv.DictReader(f))["semantic_version"]


def providers_from(env: Mapping[str, str], llm: LlmConfig, log: logging.Logger) -> LlmProviders | None:
    if (env.get("INSIGHT_LLM") or "").strip().lower() == "off":
        log.info("insight: INSIGHT_LLM=off, TEMPLATE mode")
        return None
    if not env.get(FORCE_VAR) and not env.get("GEMINI_API_KEY") and not env.get("OPENAI_API_KEY"):
        log.warning("insight: no GEMINI_API_KEY / OPENAI_API_KEY, TEMPLATE mode")
        return None
    if not env.get(FORCE_VAR) and not env.get("GEMINI_API_KEY"):
        return build_providers({**env, FORCE_VAR: "openai"}, llm)
    return build_providers(env, llm)


def build_runtime(env: Mapping[str, str], log: logging.Logger) -> InsightRuntime:
    """Read and check everything once, at startup (no file I/O is left for the turns but the pack)."""
    registry = SemanticConfigRegistry(CONFIG_DIR)
    registry.versions()
    llm = load_llm_config(CONFIG_DIR / "llm.yaml")
    source, folder = data_folder(env)
    cfg = registry.get(pack_semantic_version(folder))
    store_path = Path(env["INSIGHT_STORE_PATH"]) if env.get("INSIGHT_STORE_PATH") else STORE_PATH
    return InsightRuntime(
        reader=ExportArtifactReader(folder, cfg),
        registry=registry,
        llm=llm,
        store=InsightStore(store_path),
        providers=providers_from(env, llm, log),
        events=json_event_sink(log),
        source=source,
    )
