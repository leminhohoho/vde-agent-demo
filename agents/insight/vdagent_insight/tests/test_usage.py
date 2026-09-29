"""LlmUsage normalisation and cost (spec §7.5): TC-25, TC-26, TC-28."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from ..llm.usage import compute_cost, from_gemini, from_openai, over_daily_budget, task_cost, usage_warnings
from .builders import llm

CFG = llm()


def gemini_meta(prompt: int, cached: int | None, candidates: int, thoughts: int | None) -> SimpleNamespace:
    return SimpleNamespace(
        prompt_token_count=prompt,
        cached_content_token_count=cached,
        candidates_token_count=candidates,
        thoughts_token_count=thoughts,
    )


def openai_usage(inp: int, cached: int, out: int, reasoning: int) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=inp,
        input_tokens_details=SimpleNamespace(cached_tokens=cached),
        output_tokens=out,
        output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning),
    )


def test_tc25_gemini_usage_and_cost_with_hidden_thinking_alert() -> None:
    u = from_gemini(gemini_meta(10_000, 3_000, 1_200, 800), "gemini-3.5-flash-lite", "MAIN", 900, "STOP", CFG)
    assert (u.provider, u.input_tokens, u.cached_input_tokens, u.output_tokens, u.thinking_tokens) == (
        "gemini",
        10_000,
        3_000,
        1_200,
        800,
    )
    assert u.cost_usd == Decimal("0.00719")
    assert usage_warnings(u, CFG) == ["HIDDEN_THINKING"]


def test_tc26_openai_output_excludes_reasoning_and_uses_its_price() -> None:
    u = from_openai(openai_usage(2_000, 0, 1_500, 0), "gpt-6-luna", "MAIN", 1_200, "completed", CFG)
    assert (u.provider, u.output_tokens, u.thinking_tokens) == ("openai", 1_500, 0)
    assert u.cost_usd == (Decimal(2_000) * Decimal("0.10") + Decimal(1_500) * Decimal("0.50")) / Decimal(1_000_000)
    with_reasoning = from_openai(openai_usage(100, 40, 300, 120), "gpt-6-luna", "REPAIR", 5, "completed", CFG)
    assert (with_reasoning.output_tokens, with_reasoning.thinking_tokens, with_reasoning.cached_input_tokens) == (180, 120, 40)


def test_tc28_an_unpriced_model_costs_null_and_warns() -> None:
    u = from_gemini(gemini_meta(100, None, 10, None), "gemini-unknown", "MAIN", 10, "STOP", CFG)
    assert u.cost_usd is None and (u.cached_input_tokens, u.thinking_tokens) == (0, 0)
    assert usage_warnings(u, CFG) == ["PRICING_MISSING"]


def test_thinking_is_expected_on_repair_only() -> None:
    u = from_gemini(gemini_meta(100, 0, 10, 900), "gemini-3.5-flash-lite", "REPAIR", 10, "STOP", CFG)
    assert usage_warnings(u, CFG) == []


def test_cost_formula_is_exact_decimal() -> None:
    price = CFG.price_for("gemini-3.5-flash-lite")
    assert price is not None
    assert compute_cost(price, 1_000_000, 0, 0, 0) == Decimal("0.30")
    assert compute_cost(price, 1_000_000, 1_000_000, 0, 0) == Decimal("0.03")
    assert compute_cost(price, 0, 0, 500_000, 500_000) == Decimal("2.50")


def test_task_cost_sums_the_priced_calls_and_the_daily_budget_only_warns() -> None:
    a = from_gemini(gemini_meta(10_000, 3_000, 1_200, 800), "gemini-3.5-flash-lite", "MAIN", 1, "STOP", CFG)
    b = from_gemini(gemini_meta(100, 0, 10, 0), "gemini-unknown", "REPAIR", 1, "STOP", CFG)
    assert task_cost([a, b]) == Decimal("0.00719") and task_cost([b]) is None and task_cost([]) is None
    assert over_daily_budget(Decimal("5.01"), CFG) and not over_daily_budget(Decimal("5.0"), CFG)
