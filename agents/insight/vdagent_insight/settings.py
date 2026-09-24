"""LLM settings from the environment (the host has already loaded `agents/<name>/.env`)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .contract import AgentConfigError

REQUIRED_VARS: tuple[str, ...] = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "LLM_MODEL")
DEFAULT_LLM_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_base_url: str
    llm_model: str
    llm_timeout_s: float


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Read and validate the LLM settings; `AgentConfigError` names the offending variable."""
    env = os.environ if env is None else env
    for var in REQUIRED_VARS:
        if not env.get(var, "").strip():
            raise AgentConfigError(f"missing required environment variable {var}")
    raw_timeout = env.get("LLM_TIMEOUT_S", "").strip()
    try:
        llm_timeout_s = float(raw_timeout) if raw_timeout else DEFAULT_LLM_TIMEOUT_S
    except ValueError:
        raise AgentConfigError(f"LLM_TIMEOUT_S must be a number; got {raw_timeout!r}") from None
    if llm_timeout_s <= 0:
        raise AgentConfigError(f"LLM_TIMEOUT_S must be positive; got {llm_timeout_s:g}")
    return Settings(
        openai_api_key=env["OPENAI_API_KEY"].strip(),
        openai_base_url=env["OPENAI_BASE_URL"].strip(),
        llm_model=env["LLM_MODEL"].strip(),
        llm_timeout_s=llm_timeout_s,
    )
