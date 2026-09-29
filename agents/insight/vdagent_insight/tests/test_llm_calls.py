"""Retry and provider fallback of one LLM call point (spec §6, E08/E09, D-35): TC-18."""

from __future__ import annotations

from decimal import Decimal

from ..contracts import LlmInsightDraft, LlmUsage
from ..llm import FakeLlmClient, FakeReply, LlmSchemaError, LlmTransientError
from ..llm.calls import CallOutcome, call_with_fallback

DRAFT = {"selected": [], "skipped": []}


def usage(cost: str = "0.001") -> LlmUsage:
    return LlmUsage(
        provider="gemini", model_id="m", call_type="MAIN", input_tokens=10, cached_input_tokens=0, output_tokens=1,
        thinking_tokens=0, cost_usd=Decimal(cost), latency_ms=1, finish_reason="STOP",
    )  # fmt: skip


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


async def run(primary: FakeLlmClient, fallback: FakeLlmClient | None, sleeps: Sleeps) -> CallOutcome:
    return await call_with_fallback(
        primary, fallback, system="S", user="U", schema=LlmInsightDraft, call_type="MAIN", reasoning="off",
        retries=1, backoff_s=1.0, sleep=sleeps,
    )  # fmt: skip


async def test_tc18_everything_down_ends_in_e08_after_one_retry_and_one_fallback() -> None:
    primary = FakeLlmClient([LlmTransientError("timeout"), LlmTransientError("timeout")])
    fallback = FakeLlmClient([LlmTransientError("503")])
    sleeps = Sleeps()
    out = await run(primary, fallback, sleeps)
    assert (out.output, out.error, out.client) == (None, "E08", None)
    assert (len(primary.calls), len(fallback.calls), sleeps.calls) == (2, 1, [1.0])
    assert out.events == ["PRIMARY_E08", "RETRY", "PRIMARY_E08", "FALLBACK", "FALLBACK_E08"]


async def test_a_retry_that_succeeds_stays_on_the_primary() -> None:
    primary = FakeLlmClient([LlmTransientError("429"), FakeReply(DRAFT, usage())])
    out = await run(primary, FakeLlmClient([]), Sleeps())
    assert isinstance(out.output, LlmInsightDraft) and out.client is primary and out.error is None
    assert [u.cost_usd for u in out.usages] == [Decimal("0.001")]


async def test_the_fallback_answers_when_the_primary_stays_down() -> None:
    fallback = FakeLlmClient([FakeReply(DRAFT, usage("0.002"))])
    out = await run(FakeLlmClient([LlmTransientError("x"), LlmTransientError("x")]), fallback, Sleeps())
    assert out.client is fallback and isinstance(out.output, LlmInsightDraft)


async def test_a_schema_error_is_returned_at_once_with_the_provider_that_answered() -> None:
    bad = LlmSchemaError("truncated", usage("0.003"), raw='{"selected": [')
    primary = FakeLlmClient([bad])
    fallback = FakeLlmClient([])
    out = await run(primary, fallback, Sleeps())
    assert (out.error, out.client, out.raw) == ("E09", primary, '{"selected": [')
    assert [u.cost_usd for u in out.usages] == [Decimal("0.003")] and fallback.calls == []


async def test_no_fallback_configured_means_e08_after_the_retry() -> None:
    out = await run(FakeLlmClient([LlmTransientError("x"), LlmTransientError("x")]), None, Sleeps())
    assert out.error == "E08" and out.events[-1] == "PRIMARY_E08"
