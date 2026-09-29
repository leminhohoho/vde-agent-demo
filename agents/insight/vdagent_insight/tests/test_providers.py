"""Provider wiring from the plugin environment (primary / fallback / token counter)."""

from __future__ import annotations

import pytest

from ..llm.gemini_client import GeminiClient
from ..llm.openai_client import OpenAIClient
from ..llm.providers import build_providers
from ..settings import ConfigError
from .builders import llm

KEYS = {"GEMINI_API_KEY": "g-test", "OPENAI_API_KEY": "o-test"}


def test_default_is_gemini_first_with_openai_fallback() -> None:
    p = build_providers(KEYS, llm())
    assert isinstance(p.primary, GeminiClient) and isinstance(p.fallback, OpenAIClient) and p.counter is p.primary


def test_force_openai_uses_only_openai() -> None:
    p = build_providers({**KEYS, "INSIGHT_FORCE_PROVIDER": "openai"}, llm())
    assert isinstance(p.primary, OpenAIClient) and p.fallback is None and p.counter is p.primary


def test_force_gemini_has_no_fallback_and_a_missing_openai_key_is_fine_without_it() -> None:
    p = build_providers({"GEMINI_API_KEY": "g", "INSIGHT_FORCE_PROVIDER": "gemini"}, llm())
    assert isinstance(p.primary, GeminiClient) and p.fallback is None


@pytest.mark.parametrize("env", [{}, {"OPENAI_API_KEY": "o"}, {**KEYS, "INSIGHT_FORCE_PROVIDER": "claude"}])
def test_missing_keys_or_an_unknown_provider_are_config_errors(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        build_providers(env, llm())
