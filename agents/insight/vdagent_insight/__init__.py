"""vdagent agent plugin (LangChain agent with memory). The Backend imports this module, listed under
`plugins:` in `backend/config.yaml`, and calls `setup(api, opts)` once at startup."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from vdagent_sdk import PluginAPI

from .legacy_agent import DESCRIPTION, NAME, build_agent
from .settings import read_env


def setup(api: PluginAPI, opts: Mapping[str, Any]) -> None:
    """Register this agent, configured by this plugin folder's `.env` (`PluginConfigError` if incomplete)."""
    api.register_agent(name=NAME, description=DESCRIPTION, agent=build_agent(read_env()))
