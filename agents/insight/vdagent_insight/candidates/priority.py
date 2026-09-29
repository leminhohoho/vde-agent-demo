"""Candidate priority and the context-budget cut (spec §6.4 steps 1–2, §9.5).

Pure. `priority = attribution_score / severity_rank`; candidates without a rank take the
configured default of their task (`priority_without_rank`: T2/T3 0.5, T5 0.3, T7 0). A subject
discussed earlier in the conversation gets `memory.recent_subject_boost` on top; that changes the
order only, never confidence or KEY.

`select_for_context` keeps every T7 candidate, fills the rest of `max_candidates_in_context` by
priority (ties broken by `candidate_id`, so the cut is replayable) and rejects the others with
CONTEXT_BUDGET. Token counting (step 5 part 3) belongs to llm/preflight.py.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from ..contracts import CandidateTask, InsightCandidate, RejectedCandidate
from ..settings import SemanticParams

CONTEXT_BUDGET = "CONTEXT_BUDGET"


def base_priority(
    task: CandidateTask, attribution_score: Decimal | None, severity_rank: int | None, params: SemanticParams
) -> Decimal:
    if attribution_score is not None and severity_rank:
        return attribution_score / severity_rank
    defaults = params.priority_without_rank
    return {"T2": defaults.T2, "T3": defaults.T3, "T5": defaults.T5, "T7": defaults.T7}.get(task, defaults.T7)


def boosted(priority: Decimal, subject_id: str, recent_subject_ids: frozenset[str], boost: Decimal) -> Decimal:
    return priority + boost if subject_id in recent_subject_ids else priority


def _order(c: InsightCandidate) -> tuple[Decimal, str]:
    return (-c.priority, c.candidate_id)


def select_for_context(
    candidates: Sequence[InsightCandidate], max_candidates: int
) -> tuple[list[InsightCandidate], list[RejectedCandidate]]:
    ranked = sorted(candidates, key=_order)
    limitations = [c for c in ranked if c.task == "T7"]
    room = max(max_candidates - len(limitations), 0)
    others = [c for c in ranked if c.task != "T7"]
    keep_ids = {c.candidate_id for c in limitations} | {c.candidate_id for c in others[:room]}
    kept = [c for c in ranked if c.candidate_id in keep_ids]
    rejected = [RejectedCandidate(candidate_id=c.candidate_id, reason_code=CONTEXT_BUDGET) for c in others[room:]]
    return kept, rejected
