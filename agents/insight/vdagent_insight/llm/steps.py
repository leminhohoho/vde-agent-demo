"""The LLM steps of one task (spec §6.1 steps 5–8, §6.4, §8): pre-flight → [LLM-1] → validate →
at most one [LLM-R] → narrate (TEMPLATE per failing item).

- [LLM-1]: `call_with_fallback` with reasoning off, `transient_retries` retries, then the fallback
  provider (D-35). E08 all the way → no draft → TEMPLATE, no repair.
- [LLM-R]: only when the draft has violations (step 7) or the answer was E09; exactly one call
  (`repair.max_attempts` is 1), reasoning low, to the provider that answered, with the previous
  output, the coded violations and the candidates. If the repair fails too, the first draft (if
  any) is narrated and its failing items fall back to TEMPLATE.
- Every call's `LlmUsage` is returned (errors included) for the task cost; usage warnings
  (PRICING_MISSING, HIDDEN_THINKING) are logged as WARNING.
- `raw_outputs` keeps the model outputs for replay (spec 9.4).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from ..candidates import CandidateContext
from ..contracts import InsightCandidate, LlmInsightDraft, LlmUsage, MemoryContext, RejectedCandidate
from ..narrate import Narration, check_draft, narrate
from ..settings import LlmConfig
from ..validation import Violation
from .base import LlmClient, TokenCounter
from .calls import call_with_fallback
from .preflight import preflight
from .prompt import candidate_aliases, normalised_slot_count, repair_prompt, resolve_aliases, system_prompt, user_prompt
from .usage import usage_warnings

log = logging.getLogger("vdagent.plugin.vdagent_insight")


@dataclass(frozen=True)
class LlmProviders:
    primary: LlmClient
    fallback: LlmClient | None = None
    counter: TokenCounter | None = None


@dataclass
class LlmStepsResult:
    narration: Narration
    kept: list[InsightCandidate]
    rejected: list[RejectedCandidate]
    """Rejected by the pre-flight (CONTEXT_BUDGET)."""
    usages: list[LlmUsage] = field(default_factory=list)
    main_error: Literal["E08", "E09"] | None = None
    repaired: bool = False
    """The repair call answered with a usable draft."""
    events: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    input_tokens: int = 0
    raw_outputs: list[str] = field(default_factory=list)
    slot_normalisations: int = 0
    """Slot names written as `{{name}}` that had to be normalised (accepted, counted, logged)."""


def _draft(output: object) -> LlmInsightDraft | None:
    return output if isinstance(output, LlmInsightDraft) else None


async def run_llm_steps(
    ctx: CandidateContext,
    candidates: list[InsightCandidate],
    providers: LlmProviders,
    llm: LlmConfig,
    memory: MemoryContext,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> LlmStepsResult:
    cfg, limits = ctx.cfg, llm.limits
    system = system_prompt(cfg, llm)

    def render_user(cands: list[InsightCandidate]) -> str:
        return user_prompt(ctx.request, cands, memory, cfg)

    pf = await preflight(
        candidates,
        system=system,
        render_user=render_user,
        max_candidates=limits.max_candidates_in_context,
        max_input_tokens=limits.max_input_tokens,
        counter=providers.counter,
    )
    by_id = {c.candidate_id: c for c in pf.kept}
    aliases = candidate_aliases(pf.kept)
    retries, backoff_s = limits.transient_retries, limits.transient_backoff_ms / 1000
    main = await call_with_fallback(
        providers.primary, providers.fallback, system=system, user=render_user(pf.kept),
        schema=LlmInsightDraft, call_type="MAIN", reasoning="off", retries=retries, backoff_s=backoff_s, sleep=sleep,
    )  # fmt: skip
    result = LlmStepsResult(
        narration=Narration(), kept=pf.kept, rejected=pf.rejected, usages=list(main.usages),
        main_error=main.error, events=list(main.events), input_tokens=pf.input_tokens,
    )  # fmt: skip
    answered = _draft(main.output)  # as the model wrote it, with aliases
    draft = resolve_aliases(answered, aliases) if answered else None
    if answered is not None:
        result.slot_normalisations += normalised_slot_count(answered)
        result.raw_outputs.append(answered.model_dump_json())
    elif main.raw is not None:
        result.raw_outputs.append(main.raw)

    violations: dict[int, list[Violation]] = check_draft(draft, by_id, cfg, ctx.request) if draft else {}
    needs_repair = main.client is not None and (main.error == "E09" or bool(violations))
    if needs_repair and llm.repair.max_attempts >= 1:
        assert main.client is not None
        previous = answered.model_dump_json() if answered else (main.raw or "")
        if not violations:
            violations = {0: [Violation("SCHEMA", "E09", "the answer did not match the schema or was cut off")]}
        rsys, ruser = repair_prompt(cfg, llm, previous, violations, pf.kept)
        repair = await call_with_fallback(
            main.client, None, system=rsys, user=ruser, schema=LlmInsightDraft,
            call_type="REPAIR", reasoning="low", retries=retries, backoff_s=backoff_s, sleep=sleep,
        )  # fmt: skip
        result.usages += repair.usages
        result.events += [f"REPAIR_{e}" for e in repair.events] or ["REPAIR"]
        repaired = _draft(repair.output)
        if repaired is not None:
            draft, result.repaired = resolve_aliases(repaired, aliases), True
            result.slot_normalisations += normalised_slot_count(repaired)
            result.raw_outputs.append(repaired.model_dump_json())
        elif repair.raw is not None:
            result.raw_outputs.append(repair.raw)

    if result.slot_normalisations:
        log.warning("INSIGHT_SLOT_NAME_NORMALISED count=%d", result.slot_normalisations)
    result.narration = narrate(pf.kept, draft, cfg, ctx.view, ctx.request, max_items=limits.max_selected_insights)
    for u in result.usages:
        for warning in usage_warnings(u, llm):
            log.warning("insight LLM usage warning %s (%s %s %s)", warning, u.provider, u.model_id, u.call_type)
            if warning not in result.warnings:
                result.warnings.append(warning)
    return result
