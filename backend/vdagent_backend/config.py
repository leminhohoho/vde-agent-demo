"""Backend configuration: `backend/config.yaml` + `VDAGENT_*` env overrides.

- `VDAGENT_CONFIG` selects the YAML file (default: `backend/config.yaml` next to this package).
- Scalar keys can be overridden by `VDAGENT_<KEY>` (e.g. `VDAGENT_BACKEND_DB`, `VDAGENT_MAX_DEPTH`).
- Relative paths are resolved against the current working directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@dataclass(frozen=True)
class AgentSpec:
    name: str
    address: str
    description: str


@dataclass(frozen=True)
class Config:
    backend_db: str
    warehouse_db: str
    mcp_public_url: str
    frontend_dist: str
    max_depth: int = 4
    max_steps: int = 12
    health_interval_s: float = 10.0
    agents: dict[str, AgentSpec] = field(default_factory=dict)

    @property
    def agent_names(self) -> list[str]:
        return list(self.agents)


_SCALARS: dict[str, type] = {
    "backend_db": str,
    "warehouse_db": str,
    "mcp_public_url": str,
    "frontend_dist": str,
    "max_depth": int,
    "max_steps": int,
    "health_interval_s": float,
}


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    cfg_path = Path(path or os.environ.get("VDAGENT_CONFIG") or DEFAULT_CONFIG_PATH)
    raw = yaml.safe_load(cfg_path.read_text()) or {}
    values: dict[str, object] = {}
    for key, typ in _SCALARS.items():
        env = os.environ.get(f"VDAGENT_{key.upper()}")
        value = env if env is not None else raw.get(key)
        if value is not None:
            values[key] = typ(value)
    agents = {
        name: AgentSpec(name=name, address=str(spec["address"]), description=str(spec.get("description", "")))
        for name, spec in (raw.get("agents") or {}).items()
    }
    return Config(agents=agents, **values)  # type: ignore[arg-type]
