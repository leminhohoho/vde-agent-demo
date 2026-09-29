"""D-30 probe: can the configured models answer with strict structured output?

Usage (repo root):  uv run python agents/insight/scripts/llm_probe.py

Reads agents/insight/.env (names only are printed, never values) and config/llm.yaml, then makes
ONE tiny call per provider:
- Gemini (primary) through google-genai: response_json_schema + thinking_level minimal;
- OpenAI (fallback) through the Responses API (OPENAI_BASE_URL if set): json_schema strict +
  reasoning.effort none.
Prints: answered or not, structured output valid or not, usage (input / cached / output /
thinking tokens), latency, cost (Decimal, from the config price table). If a model id is not
available, lists the model ids the provider returns and stops; it never picks another model.
Cost of a run: a few hundred tokens per provider (well under 0.01 USD).
"""

from __future__ import annotations

import json
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vdagent_insight.settings import CONFIG_DIR, load_llm_config  # noqa: E402

ENV_VARS = ("GEMINI_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "LLM_MODEL")
PROMPT = "Trả lời bằng JSON: ok=true và cau là một câu tiếng Việt ngắn chào Sales Ops."
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "cau": {"type": "string"}},
    "required": ["ok", "cau"],
    "additionalProperties": False,
}
MAX_OUTPUT_TOKENS = 200
MILLION = Decimal(1_000_000)


def redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def cost(model_id: str, inp: int, cached: int, out: int, thinking: int) -> Decimal | None:
    price = load_llm_config(CONFIG_DIR / "llm.yaml").price_for(model_id)
    if price is None:
        return None
    return ((inp - cached) * price.input + cached * price.cached_input + (out + thinking) * price.output) / MILLION


def structured_ok(text: str) -> bool:
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return False
    return isinstance(data, dict) and set(data) == {"ok", "cau"} and isinstance(data["ok"], bool) and isinstance(data["cau"], str)


def report(name: str, **fields: Any) -> None:
    print(f"[{name}] " + ", ".join(f"{k}={v}" for k, v in fields.items()))


def probe_gemini(env: dict[str, str], model_id: str, secrets: list[str]) -> Decimal:
    from google import genai
    from google.genai import errors, types

    client = genai.Client(api_key=env["GEMINI_API_KEY"])
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=SCHEMA,
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL),
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    start = time.perf_counter()
    try:
        resp = client.models.generate_content(model=model_id, contents=PROMPT, config=config)
    except errors.APIError as exc:
        report("gemini", model=model_id, answered=False, error=f"{exc.code} {redact(str(exc.message), secrets)[:300]}")
        if exc.code == 404:
            ids = sorted(m.name or "" for m in client.models.list() if "gemini" in (m.name or ""))
            print("[gemini] available models:", ", ".join(ids))
        return Decimal(0)
    latency = int((time.perf_counter() - start) * 1000)
    u = resp.usage_metadata
    inp, cached = (u.prompt_token_count or 0), (u.cached_content_token_count or 0)
    out, thinking = (u.candidates_token_count or 0), (u.thoughts_token_count or 0)
    finish = resp.candidates[0].finish_reason if resp.candidates else None
    c = cost(model_id, inp, cached, out, thinking)
    report(
        "gemini",
        model=model_id,
        answered=True,
        structured_valid=structured_ok(resp.text or ""),
        finish_reason=getattr(finish, "value", finish),
        input_tokens=inp,
        cached_input_tokens=cached,
        output_tokens=out,
        thinking_tokens=thinking,
        latency_ms=latency,
        cost_usd=c,
    )
    print("[gemini] text:", (resp.text or "")[:200])
    return c or Decimal(0)


def probe_openai(env: dict[str, str], model_id: str, secrets: list[str]) -> Decimal:
    import openai

    base_url = env.get("OPENAI_BASE_URL") or None
    client = openai.OpenAI(api_key=env["OPENAI_API_KEY"], base_url=base_url)
    start = time.perf_counter()
    try:
        resp = client.responses.create(
            model=model_id,
            input=PROMPT,
            text={"format": {"type": "json_schema", "name": "probe", "schema": SCHEMA, "strict": True}},
            reasoning={"effort": "none"},
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
    except openai.APIStatusError as exc:
        report(
            "openai",
            model=model_id,
            base_url="custom" if base_url else "default",
            answered=False,
            error=f"{exc.status_code} {redact(str(exc.message), secrets)[:300]}",
        )
        if exc.status_code == 404:
            try:
                ids = sorted(m.id for m in client.models.list() if "gpt" in m.id)
                print("[openai] available models:", ", ".join(ids))
            except openai.APIError as list_exc:
                print("[openai] cannot list models:", redact(str(list_exc), secrets)[:200])
        return Decimal(0)
    except openai.APIConnectionError as exc:
        report("openai", model=model_id, answered=False, error=f"connection: {redact(str(exc), secrets)[:200]}")
        return Decimal(0)
    latency = int((time.perf_counter() - start) * 1000)
    u = resp.usage
    inp = u.input_tokens if u else 0
    cached = u.input_tokens_details.cached_tokens if u and u.input_tokens_details else 0
    reasoning = u.output_tokens_details.reasoning_tokens if u and u.output_tokens_details else 0
    out = (u.output_tokens if u else 0) - reasoning
    c = cost(model_id, inp, cached, out, reasoning)
    report(
        "openai",
        model=model_id,
        base_url="custom" if base_url else "default",
        answered=True,
        structured_valid=structured_ok(resp.output_text),
        status=resp.status,
        input_tokens=inp,
        cached_input_tokens=cached,
        output_tokens=out,
        thinking_tokens=reasoning,
        latency_ms=latency,
        cost_usd=c,
    )
    print("[openai] text:", resp.output_text[:200])
    return c or Decimal(0)


def main() -> None:
    raw = dotenv_values(ROOT / ".env")
    env = {k: v for k, v in raw.items() if v}
    print("[env] present:", [k for k in ENV_VARS if k in env], "missing:", [k for k in ENV_VARS if k not in env])
    cfg = load_llm_config(CONFIG_DIR / "llm.yaml")
    secrets = [env.get("GEMINI_API_KEY", ""), env.get("OPENAI_API_KEY", "")]
    total = Decimal(0)
    if "GEMINI_API_KEY" in env:
        total += probe_gemini(env, cfg.primary.model_id, secrets)
    if "OPENAI_API_KEY" in env:
        total += probe_openai(env, cfg.fallback.model_id, secrets)
    print(f"[total] cost_usd={total}")


if __name__ == "__main__":
    main()
