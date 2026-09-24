"""Backend configuration: `backend/config.yaml` + `VDAGENT_*` env overrides.

- `VDAGENT_CONFIG` selects the YAML file (default: `backend/config.yaml` next to this package).
- The repo-root `.env` (nearest walking up from the config file, else from the working directory)
  is loaded first; the process environment wins over it.
- Scalar keys can be overridden by `VDAGENT_<KEY>` (e.g. `VDAGENT_BACKEND_DB`, `VDAGENT_AGENT_LISTEN`).
- Agent tokens come from `VDAGENT_AGENT_TOKEN_<NAME>` (`tokens_from_env`).
- Relative paths are resolved against the current working directory.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import find_dotenv, load_dotenv

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str


@dataclass(frozen=True)
class Config:
    backend_db: str
    warehouse_db: str
    mcp_public_url: str
    frontend_dist: str
    agent_listen: str = "127.0.0.1:50050"
    max_depth: int = 4
    max_steps: int = 12
    agents: dict[str, AgentSpec] = field(default_factory=dict)

    @property
    def agent_names(self) -> list[str]:
        return list(self.agents)


_SCALARS: dict[str, type] = {
    "backend_db": str,
    "warehouse_db": str,
    "mcp_public_url": str,
    "agent_listen": str,
    "frontend_dist": str,
    "max_depth": int,
    "max_steps": int,
}


def token_env_var(agent: str) -> str:
    return f"VDAGENT_AGENT_TOKEN_{agent.upper()}"


def tokens_from_env(agents: Mapping[str, AgentSpec]) -> dict[str, str]:
    """Each agent's session token; agents without one are logged and can never connect."""
    tokens: dict[str, str] = {}
    for name in agents:
        token = os.environ.get(token_env_var(name), "").strip()
        if token:
            tokens[name] = token
        else:
            log.warning("agent %s has no token (%s is unset); it cannot connect", name, token_env_var(name))
    return tokens


def _load_env_file(cfg_path: Path) -> None:
    for directory in cfg_path.resolve().parents:
        candidate = directory / ".env"
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return
    found = find_dotenv(usecwd=True)
    if found:
        load_dotenv(found, override=False)


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    cfg_path = Path(path or os.environ.get("VDAGENT_CONFIG") or DEFAULT_CONFIG_PATH)
    _load_env_file(cfg_path)
    raw = yaml.safe_load(cfg_path.read_text()) or {}
    values: dict[str, object] = {}
    for key, typ in _SCALARS.items():
        env = os.environ.get(f"VDAGENT_{key.upper()}")
        value = env if env is not None else raw.get(key)
        if value is not None:
            values[key] = typ(value)
    agents = {
        name: AgentSpec(name=name, description=str((spec or {}).get("description", "")))
        for name, spec in (raw.get("agents") or {}).items()
    }
    return Config(agents=agents, **values)  # type: ignore[arg-type]
