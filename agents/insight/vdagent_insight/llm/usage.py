"""LlmUsage normalisation and cost (spec §7.5). Pure.

Each adapter hands its SDK usage object here and gets one `LlmUsage`:

| Field | Gemini `usage_metadata` | OpenAI Responses `usage` |
|---|---|---|
| input_tokens | prompt_token_count | input_tokens |
| cached_input_tokens | cached_content_token_count | input_tokens_details.cached_tokens |
| output_tokens | candidates_token_count | output_tokens − reasoning_tokens |
| thinking_tokens | thoughts_token_count | output_tokens_details.reasoning_tokens |

cost = ((input − cached)·P_in + cached·P_cached + (output + thinking)·P_out) / 10⁶, in `Decimal`,
prices from `pricing_usd_per_1m` of the exact model. Unpriced model → `cost_usd = None` and the
PRICING_MISSING warning; thinking above `hidden_thinking_alert_tokens` on a call that runs with
reasoning off (MAIN, MEMORY) → HIDDEN_THINKING. Warnings are logged, never fatal.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..contracts import CallType, LlmUsage, Provider
from ..settings import LlmConfig, ModelPrice

MILLION = Decimal(1_000_000)
REASONING_OFF = frozenset({"MAIN", "MEMORY"})


def compute_cost(price: ModelPrice, input_tokens: int, cached: int, output: int, thinking: int) -> Decimal:
    return ((input_tokens - cached) * price.input + cached * price.cached_input + (output + thinking) * price.output) / MILLION


def _n(value: Any) -> int:
    return int(value or 0)


def _usage(
    provider: Provider, model_id: str, call_type: CallType, latency_ms: int, finish_reason: str,
    input_tokens: int, cached: int, output: int, thinking: int, cfg: LlmConfig,
) -> LlmUsage:  # fmt: skip
    price = cfg.price_for(model_id)
    return LlmUsage(
        provider=provider,
        model_id=model_id,
        call_type=call_type,
        input_tokens=input_tokens,
        cached_input_tokens=cached,
        output_tokens=output,
        thinking_tokens=thinking,
        cost_usd=None if price is None else compute_cost(price, input_tokens, cached, output, thinking),
        latency_ms=latency_ms,
        finish_reason=finish_reason,
    )


def from_gemini(meta: Any, model_id: str, call_type: CallType, latency_ms: int, finish_reason: str, cfg: LlmConfig) -> LlmUsage:
    return _usage(
        "gemini", model_id, call_type, latency_ms, finish_reason,
        _n(getattr(meta, "prompt_token_count", 0)), _n(getattr(meta, "cached_content_token_count", 0)),
        _n(getattr(meta, "candidates_token_count", 0)), _n(getattr(meta, "thoughts_token_count", 0)), cfg,
    )  # fmt: skip


def from_openai(usage: Any, model_id: str, call_type: CallType, latency_ms: int, finish_reason: str, cfg: LlmConfig) -> LlmUsage:
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    reasoning = _n(getattr(output_details, "reasoning_tokens", 0))
    return _usage(
        "openai", model_id, call_type, latency_ms, finish_reason,
        _n(getattr(usage, "input_tokens", 0)), _n(getattr(input_details, "cached_tokens", 0)),
        _n(getattr(usage, "output_tokens", 0)) - reasoning, reasoning, cfg,
    )  # fmt: skip


def usage_warnings(usage: LlmUsage, cfg: LlmConfig) -> list[str]:
    out = []
    if usage.cost_usd is None:
        out.append("PRICING_MISSING")
    if usage.call_type in REASONING_OFF and usage.thinking_tokens > cfg.budget.hidden_thinking_alert_tokens:
        out.append("HIDDEN_THINKING")
    return out


def task_cost(usages: list[LlmUsage]) -> Decimal | None:
    """Sum of the priced calls; None when no call was priced."""
    priced = [u.cost_usd for u in usages if u.cost_usd is not None]
    return sum(priced, Decimal(0)) if priced else None


def over_daily_budget(spent_today: Decimal, cfg: LlmConfig) -> bool:
    """Only a WARNING (P3 brief): the caller logs it; nothing is blocked."""
    return spent_today > cfg.budget.daily_usd
