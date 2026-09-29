"""P4-10 check: how many drafted items of a tower question fall back to a template (live, costs cents).

Usage (repo root):  uv run python agents/insight/scripts/tower_eval.py [--runs 3] [--zone ZN-SAPPHIRE1]

Builds the tower task from the data pack (as the bridge would), runs steps 5–8 with the real
providers `--runs` times and prints, per run: items drafted, items that fell back to a template after
the repair, the violation codes of the first answer and of the final one, and the cost. Keys are
never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vdagent_insight.bridge import load_bridge_config  # noqa: E402
from vdagent_insight.candidates import build_context, generate_candidates  # noqa: E402
from vdagent_insight.contracts import AnalysisScope, InsightTaskRequest, LlmInsightDraft, MemoryContext  # noqa: E402
from vdagent_insight.llm.prompt import candidate_aliases, resolve_aliases  # noqa: E402
from vdagent_insight.llm.steps import run_llm_steps  # noqa: E402
from vdagent_insight.llm.usage import task_cost  # noqa: E402
from vdagent_insight.narrate import check_draft  # noqa: E402
from vdagent_insight.runtime import build_runtime  # noqa: E402
from vdagent_insight.settings import read_env  # noqa: E402

AUTH = {"project_ids": ["PRJ-VHOP"], "zone_ids": []}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--zone", default="ZN-SAPPHIRE1")
    ap.add_argument("--show", action="store_true", help="print the failing templates")
    args = ap.parse_args()
    import logging

    rt = build_runtime(read_env(), logging.getLogger("tower_eval"))
    assert rt.providers is not None, "no LLM key"
    manifest = await rt.reader.manifest()
    scope = AnalysisScope(level="ZONE", zone_ids=[args.zone])
    arts = await rt.reader.prepare(scope)
    tasks = load_bridge_config().tasks["SLOW_MOVING_INVESTIGATION"]["ZONE"]
    request = InsightTaskRequest.model_validate(
        {
            "run_id": "0f8fad5b-d9cb-469f-a165-70867728950e", "task_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
            "attempt": 1, "fencing_token": 1, "intent": "SLOW_MOVING_INVESTIGATION", "tasks": tasks,
            "question_normalized": "Tại sao tòa Sapphire 1 có nhiều căn bán chậm?", "analysis_scope": scope.model_dump(),
            "snapshot_id": manifest.snapshot_id, "semantic_config_version": manifest.semantic_version,
            "user_context": {"user_id": "u", "role": "SALES_MANAGER", "authorized_scope": AUTH},
            "input_artifact_refs": [
                {"artifact_id": a.artifact_id, "artifact_type": a.artifact_type, "version": 1, "status": "VALID",
                 "content_hash": a.content_hash} for a in arts
            ],
        }
    )  # fmt: skip
    ctx = build_context(request, arts, rt.reader.cfg, rt.as_of or manifest_as_of())
    batch = generate_candidates(ctx, rt.llm.limits.max_candidates_in_context)
    total_items = total_fallback = 0
    total_cost = Decimal(0)
    for run in range(1, args.runs + 1):
        steps = await run_llm_steps(ctx, batch.candidates, rt.providers, rt.llm, MemoryContext.empty())
        by_id = {c.candidate_id: c for c in steps.kept}
        aliases = candidate_aliases(steps.kept)
        try:
            first = LlmInsightDraft.model_validate_json(steps.raw_outputs[0]) if steps.raw_outputs else None
        except ValueError:  # the first answer was cut off or not JSON (E09): the repair answered
            first = None
            print(json.dumps({"run": run, "first_answer": "E09 (unparsable)", "main_error": steps.main_error}))
        if first is None and steps.repaired:
            first = LlmInsightDraft.model_validate_json(steps.raw_outputs[-1])
        first_v = check_draft(resolve_aliases(first, aliases), by_id, ctx.cfg, request) if first else {}
        drafted = len(first.selected) if first else 0
        fallback = len(steps.narration.violations)
        cost = task_cost(steps.usages) or Decimal(0)
        total_items, total_fallback, total_cost = total_items + drafted, total_fallback + fallback, total_cost + cost
        codes = Counter(v.code for vs in first_v.values() for v in vs)
        final = Counter(v.code for vs in steps.narration.violations.values() for v in vs)
        print(json.dumps({"run": run, "drafted": drafted, "fallback": fallback, "first_answer": dict(codes),
                          "final": dict(final), "repaired": steps.repaired, "calls": len(steps.usages),
                          "cost_usd": str(cost)}, ensure_ascii=False))  # fmt: skip
        if args.show and first:
            for i, vs in first_v.items():
                item = first.selected[i]
                print("   ", item.template, "|", [(s.slot, s.ref) for s in item.slots], "|", [v.detail for v in vs])
    print(json.dumps({"runs": args.runs, "drafted": total_items, "fallback": total_fallback, "cost_usd": str(total_cost)}))
    return 0


def manifest_as_of():  # pragma: no cover - only when INSIGHT_AS_OF=now
    from datetime import datetime

    return datetime.now().astimezone()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
