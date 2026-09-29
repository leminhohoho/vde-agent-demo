"""Pre-flight (pipeline step 5, spec §6.4): never send a prompt over budget.

1. Priority order, every T7 kept, cut to `max_candidates_in_context` (`candidates.priority`), the
   rest rejected CONTEXT_BUDGET.
2. Count the input tokens of system + user with the provider's own counter; when it fails (or
   there is none) estimate `ceil(chars / 3)`.
3. Over `max_input_tokens`: drop the lowest-priority non-T7 candidate (CONTEXT_BUDGET) and count
   again, until it fits or only T7 candidates are left.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass, field

from ..candidates.priority import CONTEXT_BUDGET, select_for_context
from ..contracts import InsightCandidate, Level, RejectedCandidate
from .base import TokenCounter


@dataclass
class Preflight:
    kept: list[InsightCandidate]
    rejected: list[RejectedCandidate] = field(default_factory=list)
    input_tokens: int = 0
    estimated: bool = False
    """True when the token count is the chars/3 estimate."""


async def _count(counter: TokenCounter | None, system: str, user: str) -> tuple[int, bool]:
    if counter is not None:
        try:
            return await counter.count_tokens(system=system, user=user), False
        except asyncio.CancelledError:
            raise
        except Exception:  # any counting failure falls back to the estimate (spec 6.4)
            pass
    return math.ceil((len(system) + len(user)) / 3), True


async def preflight(
    candidates: list[InsightCandidate],
    *,
    system: str,
    render_user: Callable[[list[InsightCandidate]], str],
    max_candidates: int,
    max_input_tokens: int,
    counter: TokenCounter | None,
    scope_level: Level = "UNIT",
) -> Preflight:
    kept, rejected = select_for_context(candidates, max_candidates, scope_level=scope_level)
    tokens, estimated = await _count(counter, system, render_user(kept))
    while tokens > max_input_tokens:
        droppable = [c for c in kept if c.task != "T7"]
        if not droppable:
            break
        lowest = droppable[-1]  # `kept` is in priority order
        kept = [c for c in kept if c.candidate_id != lowest.candidate_id]
        rejected.append(RejectedCandidate(candidate_id=lowest.candidate_id, reason_code=CONTEXT_BUDGET))
        tokens, estimated = await _count(counter, system, render_user(kept))
    return Preflight(kept=kept, rejected=rejected, input_tokens=tokens, estimated=estimated)
