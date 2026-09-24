"""Agent process settings (spec §7.1): environment loaded from the repo-root `.env`, process env wins."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

AGENT_NAMES: tuple[str, ...] = ("orchestrator", "data", "compare", "insight", "report")
REQUIRED_VARS: tuple[str, ...] = ("AGENT_NAME", "OPENAI_API_KEY", "OPENAI_BASE_URL", "LLM_MODEL")
DEFAULT_GRPC_PORT = 50051
DEFAULT_LLM_TIMEOUT_S = 120.0


class SettingsError(Exception):
    """Invalid or missing configuration; the message names the offending variable."""


@dataclass(frozen=True)
class Settings:
    agent_name: str
    grpc_port: int
    openai_api_key: str
    openai_base_url: str
    llm_model: str
    llm_timeout_s: float


def find_repo_env_file() -> Path | None:
    """The repo-root `.env`: nearest `.env` walking up from this package, else from the cwd."""
    for directory in Path(__file__).resolve().parents:
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    found = find_dotenv(usecwd=True)
    return Path(found) if found else None


def load_env_file() -> None:
    """Load the repo-root `.env` into the process env without overriding existing variables."""
    env_file = find_repo_env_file()
    if env_file is not None:
        load_dotenv(env_file, override=False)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    for var in REQUIRED_VARS:
        if not env.get(var, "").strip():
            raise SettingsError(f"missing required environment variable {var}")

    agent_name = env["AGENT_NAME"].strip()
    if agent_name not in AGENT_NAMES:
        raise SettingsError(f"AGENT_NAME must be one of {', '.join(AGENT_NAMES)}; got {agent_name!r}")

    try:
        grpc_port = int(env.get("GRPC_PORT", "").strip() or DEFAULT_GRPC_PORT)
    except ValueError:
        raise SettingsError(f"GRPC_PORT must be an integer; got {env['GRPC_PORT']!r}") from None
    try:
        llm_timeout_s = float(env.get("LLM_TIMEOUT_S", "").strip() or DEFAULT_LLM_TIMEOUT_S)
    except ValueError:
        raise SettingsError(f"LLM_TIMEOUT_S must be a number; got {env['LLM_TIMEOUT_S']!r}") from None
    if llm_timeout_s <= 0:
        raise SettingsError(f"LLM_TIMEOUT_S must be positive; got {llm_timeout_s}")

    return Settings(
        agent_name=agent_name,
        grpc_port=grpc_port,
        openai_api_key=env["OPENAI_API_KEY"].strip(),
        openai_base_url=env["OPENAI_BASE_URL"].strip(),
        llm_model=env["LLM_MODEL"].strip(),
        llm_timeout_s=llm_timeout_s,
    )
