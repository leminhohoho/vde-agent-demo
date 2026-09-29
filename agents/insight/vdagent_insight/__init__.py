"""vdagent plugin of the Insight Agent v2 (docs/insight_agent_spec.md). The Backend imports this module,
listed under `plugins:` in `backend/config.yaml`, and calls `setup(api, opts)` once at startup."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from vdagent_sdk import PluginAPI

from .bridge import DESCRIPTION, NAME, InsightAgent
from .runtime import build_runtime
from .settings import read_env


def setup(api: PluginAPI, opts: Mapping[str, Any]) -> None:
    """Register the agent; bad config or data source → `ConfigError` (a `PluginConfigError`)."""
    runtime = build_runtime(read_env(), api.log)
    api.log.info("insight: data source %s, LLM %s", runtime.source, "on" if runtime.providers else "off (TEMPLATE)")
    api.register_agent(name=NAME, description=DESCRIPTION, agent=InsightAgent(runtime))
