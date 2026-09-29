"""Live runs against the real providers (phase P3): TC-01 (SAPPHIRE1-16.231) and TC-04 (The Beverly).

Run:  INSIGHT_LIVE=1 uv run pytest agents/insight -m live -s
Force one provider (no fallback):  INSIGHT_FORCE_PROVIDER=openai (or gemini)
Each run appends a JSON line (per case: main error, repair, narrative mode, cost, latency, sample
sentences) to the file named by INSIGHT_LIVE_LOG, when set. Budget: a few thousand tokens per case.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from ..artifacts import FixtureArtifactReader
from ..assess import assess
from ..candidates import build_context, generate_candidates
from ..contracts import InsightTaskRequest, MemoryContext
from ..llm.providers import build_providers
from ..llm.steps import run_llm_steps
from ..llm.usage import task_cost
from ..settings import CONFIG_DIR, SemanticConfigRegistry, load_llm_config
from .conftest import live_env

pytestmark = pytest.mark.live
FIXTURES = Path(__file__).parent / "fixtures"
AS_OF = datetime.fromisoformat("2026-07-01T00:00:00+07:00")
VI_DIACRITIC = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.IGNORECASE)
MAX_COST_PER_CASE = Decimal("0.05")


@pytest.mark.parametrize("case", ["tc01", "tc04"])
async def test_live_pipeline_to_step_9(case: str) -> None:
    force = os.environ.get("INSIGHT_FORCE_PROVIDER", "")
    llm = load_llm_config(CONFIG_DIR / "llm.yaml")
    providers = build_providers({**live_env(), "INSIGHT_FORCE_PROVIDER": force}, llm)

    folder = FIXTURES / case
    request = InsightTaskRequest.model_validate_json((folder / "request.json").read_text(encoding="utf-8"))
    reader = FixtureArtifactReader(folder / "artifacts")
    artifacts = [await reader.read(r.artifact_id) for r in request.input_artifact_refs]
    ctx = build_context(request, artifacts, SemanticConfigRegistry(CONFIG_DIR).get(request.semantic_config_version), AS_OF)
    batch = generate_candidates(ctx, llm.limits.max_candidates_in_context)
    steps = await run_llm_steps(ctx, batch.candidates, providers, llm, MemoryContext.empty())
    result = assess(ctx, steps.kept, [*batch.rejected, *steps.rejected], steps.narration)

    by_id = {c.candidate_id: c for c in steps.kept}
    for item in steps.narration.items:
        for binding in item.claim.numeric_bindings:  # numbers come from candidates only
            assert any(
                b.metric_ref == binding.metric_ref and b.value == binding.value
                for i in item.candidate_ids
                for b in by_id[i].slots.values()
            )
        assert VI_DIACRITIC.search(item.claim.rendered_text) and "{{" not in item.claim.rendered_text
    assert steps.usages and all(u.cost_usd is not None for u in steps.usages)
    if force:
        assert {u.provider for u in steps.usages} == {force}
    cost = task_cost(steps.usages) or Decimal(0)
    assert cost < MAX_COST_PER_CASE

    summary = {
        "case": case,
        "forced_provider": force or None,
        "slot_normalisations": steps.slot_normalisations,
        "main_error": steps.main_error,
        "repaired": steps.repaired,
        "narrative_mode": result.payload.summary.narrative_mode,
        "items": {s: sum(i.source == s for i in steps.narration.items) for s in ("LLM", "TEMPLATE")},
        "violations": {i: [v.code for v in vs] for i, vs in steps.narration.violations.items()},
        "events": steps.events,
        "status": result.status,
        "calls": [
            (u.provider, u.call_type, u.input_tokens, u.cached_input_tokens, u.output_tokens, u.thinking_tokens, u.latency_ms)
            for u in steps.usages
        ],
        "cost_usd": str(cost),
        "latency_ms": sum(u.latency_ms for u in steps.usages),
        "sentences": [i.claim.rendered_text for i in result.payload.insights[:3]],
    }
    print(json.dumps(summary, ensure_ascii=False))
    log = os.environ.get("INSIGHT_LIVE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
