"""Live runs against the real providers (phase P3): TC-01 (SAPPHIRE1-16.231) and TC-04 (The Beverly).

Run:  INSIGHT_LIVE=1 uv run pytest agents/insight -m live -s
Force one provider (no fallback):  INSIGHT_FORCE_PROVIDER=openai (or gemini)
Each run appends a JSON line (per case: main error, repair, narrative mode, cost, latency, sample
sentences) to the file named by INSIGHT_LIVE_LOG, when set. Budget: a few thousand tokens per case.
"""

from __future__ import annotations

import dataclasses
import json
import logging
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


async def test_live_bridge_end_to_end_on_the_data_pack(tmp_path: Path) -> None:
    """P4: the Orchestrator's free text through the bridge, the data pack, the real LLM and the store."""
    from ..bridge import MAX_REPLY_CHARS, InsightAgent
    from ..runtime import build_runtime
    from .conftest import DATAPACK_DIR
    from .test_bridge import JSON_BLOCK, FakeCtx

    source = "export" if (DATAPACK_DIR / "snapshot_manifest.csv").is_file() else "fixtures"
    env = {
        **live_env(),
        "INSIGHT_ARTIFACT_SOURCE": source,
        "INSIGHT_EXPORT_DIR": str(DATAPACK_DIR),
        "INSIGHT_STORE_PATH": str(tmp_path / "insight.db"),
        "INSIGHT_FORCE_PROVIDER": os.environ.get("INSIGHT_FORCE_PROVIDER", ""),
    }
    events: list[dict[str, object]] = []
    rt = dataclasses.replace(build_runtime(env, logging.getLogger("live")), events=lambda e, f: events.append({"event": e, **f}))
    ctx = FakeCtx("Vì sao tòa Sapphire 1 có nhiều căn bán chậm?", invocation_id="inv_live")
    await InsightAgent(rt).invoke(ctx)  # type: ignore[arg-type]
    (reply,) = ctx.emitted
    (raw,) = JSON_BLOCK.findall(reply)
    data = json.loads(raw)
    done = next(e for e in events if e["event"] == "INSIGHT_TASK_COMPLETED")
    assert len(reply) <= MAX_REPLY_CHARS and data["status"] in ("VALID", "PARTIAL") and data["insights"]
    assert VI_DIACRITIC.search(reply) and "{{" not in reply
    cost = Decimal(str(done["task_cost_usd"] or "0"))
    assert cost < MAX_COST_PER_CASE
    totals = ("status", "narrative_mode", "llm_calls", "task_cost_usd", "duration_ms", "candidates_sent", "insights_out")
    calls = ("provider", "call_type", "input_tokens", "output_tokens", "thinking_tokens", "latency_ms")
    summary = {
        "case": "bridge-sapphire1",
        "source": source,
        **{k: done.get(k) for k in totals},
        "llm": [{k: e.get(k) for k in calls} for e in events if e["event"] == "INSIGHT_LLM_CALLED"],
        "reply": reply,
    }
    print(json.dumps(summary, ensure_ascii=False))
    log = os.environ.get("INSIGHT_LIVE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
