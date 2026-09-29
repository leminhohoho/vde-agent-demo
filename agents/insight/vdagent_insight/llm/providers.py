"""Provider wiring from the plugin environment (D-30).

Default: Gemini primary (`GEMINI_API_KEY`), OpenAI Responses fallback when `OPENAI_API_KEY` is set
(`OPENAI_BASE_URL` optional), token counting by the primary. `INSIGHT_FORCE_PROVIDER=openai|gemini`
runs one provider only, without fallback (to exercise a provider on purpose). Model ids and limits
come from config/llm.yaml. Constructing the SDK clients makes no network call.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..settings import ConfigError, LlmConfig
from .gemini_client import GeminiClient
from .openai_client import OpenAIClient
from .steps import LlmProviders

FORCE_VAR = "INSIGHT_FORCE_PROVIDER"


def build_providers(env: Mapping[str, str], llm: LlmConfig) -> LlmProviders:
    force = (env.get(FORCE_VAR) or "").strip().lower() or None
    if force not in (None, "gemini", "openai"):
        raise ConfigError(f"{FORCE_VAR} must be 'gemini' or 'openai'; got {force!r}")

    def gemini() -> GeminiClient:
        if not env.get("GEMINI_API_KEY"):
            raise ConfigError("missing GEMINI_API_KEY")
        return GeminiClient.from_key(env["GEMINI_API_KEY"], llm)

    def openai() -> OpenAIClient:
        if not env.get("OPENAI_API_KEY"):
            raise ConfigError("missing OPENAI_API_KEY")
        return OpenAIClient.from_key(env["OPENAI_API_KEY"], llm, env.get("OPENAI_BASE_URL") or None)

    if force == "openai":
        client = openai()
        return LlmProviders(client, None, client)
    primary = gemini()
    fallback = openai() if force is None and env.get("OPENAI_API_KEY") else None
    return LlmProviders(primary, fallback, primary)
