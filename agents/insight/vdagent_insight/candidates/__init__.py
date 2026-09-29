"""Insight Candidate Engine (pipeline steps 3–5, spec §5.5, §6.2, §6.4 parts 1–2).

Pure (luật 11), `decimal.Decimal` throughout:

- `build_context`: parse the input artifacts by type, run the Sufficiency Gate (gate.py).
- `generate_candidates`: T1, T2, T3, T5, T7 in that fixed order, then the context-budget cut
  (priority.py): every T7 kept, the rest by priority, the others rejected CONTEXT_BUDGET.

Modules: t1_unit, t2_distribution, t3_pattern, t5_market, t7_limitation, stats (Wilson, seeded
bootstrap), priority, common (context, batch, BR-03/BR-04 checks).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from ..contracts import DatasetPayload, DqPayload, InputArtifact, InsightTaskRequest, MarketContextPayload, MetricPayload
from ..gate import run_gate
from ..settings import SemanticConfig
from ..view import DatasetView
from .common import CandidateBatch, CandidateContext
from .priority import select_for_context
from .t1_unit import t1_candidates
from .t2_distribution import t2_candidates
from .t3_pattern import t3_candidates
from .t5_market import t5_candidates
from .t7_limitation import t7_candidates

__all__ = ["CandidateBatch", "CandidateContext", "build_context", "generate_candidates"]


def _first(artifacts: Sequence[InputArtifact], artifact_type: str) -> InputArtifact | None:
    return next((a for a in artifacts if a.artifact_type == artifact_type), None)


def build_context(
    request: InsightTaskRequest,
    artifacts: Sequence[InputArtifact],
    cfg: SemanticConfig,
    as_of: datetime,
    *,
    recent_subject_ids: frozenset[str] = frozenset(),
    recent_subject_boost: Decimal = Decimal(0),
) -> CandidateContext:
    """Artifacts must already be validated (step 1: E02 for a missing metric/dq/dataset)."""
    dataset_art, dq_art = _first(artifacts, "dataset"), _first(artifacts, "dq")
    metric_art, market_art = _first(artifacts, "metric"), _first(artifacts, "market_context")
    dataset = DatasetPayload.model_validate(dataset_art.payload) if dataset_art else DatasetPayload(source_refs=[])
    if dq_art is None:
        raise ValueError("E02 REQUIRED_ARTIFACT_MISSING: no dq artifact")
    dq = DqPayload.model_validate(dq_art.payload)
    view = DatasetView(dataset)
    gate = run_gate(view, dq, request.analysis_scope, cfg, as_of)
    return CandidateContext(
        request=request,
        cfg=cfg,
        view=view,
        gate=gate,
        dataset_id=dataset_art.artifact_id if dataset_art else "dataset",
        dq_id=dq_art.artifact_id,
        metric=MetricPayload.model_validate(metric_art.payload) if metric_art else None,
        metric_id=metric_art.artifact_id if metric_art else None,
        market=MarketContextPayload.model_validate(market_art.payload) if market_art else None,
        market_id=market_art.artifact_id if market_art else None,
        recent_subject_ids=recent_subject_ids,
        recent_subject_boost=recent_subject_boost,
    )


def generate_candidates(ctx: CandidateContext, max_candidates: int) -> CandidateBatch:
    """`max_candidates` = `llm.limits.max_candidates_in_context` (config/llm.yaml)."""
    batch = CandidateBatch()
    for task in (t1_candidates, t2_candidates, t3_candidates, t5_candidates, t7_candidates):
        batch.extend(task(ctx))
    kept, cut = select_for_context(batch.candidates, max_candidates)
    return CandidateBatch(candidates=kept, rejected=batch.rejected + cut)
