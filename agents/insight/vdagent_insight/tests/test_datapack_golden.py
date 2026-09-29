"""Auto golden on the full data pack (D-77): every overdue unit through the pipeline (steps 1–9,
TEMPLATE narration, no LLM), one UNIT task per unit, grouped by tower.

For each unit the bridge (`unit_diagnostic_causes`) is the golden answer:
- every cause Insight states is a bridge cause of that unit (nothing invented);
- every allowed bridge cause is either stated or rejected with a coded reason (nothing dropped
  silently);
- the stated root causes follow `severity_rank`, so the primary cause leads when it is stated.
The pack ships 1 139 overdue units; the test fails if the count changes.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

import pytest

from ..artifacts import content_hash
from ..assess import assess
from ..candidates import build_context, generate_candidates
from ..contracts import AnalysisScope, DatasetPayload, InsightTaskRequest
from ..export_reader import ExportArtifactReader
from ..narrate import narrate
from ..view import DatasetView
from .builders import llm, semantic
from .conftest import DATAPACK_DIR

AS_OF = datetime.fromisoformat("2026-07-01T08:00:00+07:00")
OVERDUE_UNITS = 1139
TASKS = ["T1", "T5", "T6", "T7"]
AUTHORIZED = {"project_ids": ["PRJ-VHOP", "PRJ-VHOP-BEVERLY"], "zone_ids": []}


def unit_request(unit_id: str, arts: list, snapshot: str, version: str) -> InsightTaskRequest:  # type: ignore[type-arg]
    return InsightTaskRequest.model_validate(
        {
            "run_id": "0f8fad5b-d9cb-469f-a165-70867728950e", "task_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
            "attempt": 1, "fencing_token": 1, "intent": "SLOW_MOVING_INVESTIGATION", "tasks": TASKS,
            "question_normalized": f"Vì sao căn {unit_id} bán chậm?",
            "analysis_scope": {"level": "UNIT", "unit_ids": [unit_id]},
            "snapshot_id": snapshot, "semantic_config_version": version,
            "user_context": {"user_id": "u", "role": "SALES_OPS", "authorized_scope": AUTHORIZED},
            "input_artifact_refs": [
                {"artifact_id": a.artifact_id, "artifact_type": a.artifact_type, "version": 1, "status": "VALID",
                 "content_hash": a.content_hash}
                for a in arts
            ],
        }
    )  # fmt: skip


@pytest.mark.datapack
async def test_every_overdue_unit_states_exactly_its_bridge_causes() -> None:
    cfg, limits = semantic(), llm().limits
    reader = ExportArtifactReader(DATAPACK_DIR, cfg)
    manifest = await reader.manifest()
    everything = await reader.prepare(AnalysisScope(level="PROJECT"))
    view = DatasetView(DatasetPayload.model_validate(everything[2].payload))
    overdue = [u for u in view.units if DatasetView.is_overdue(u, cfg.params.overdue_threshold_days)]
    assert len(overdue) == OVERDUE_UNITS

    per_tower: dict[str, Counter[str]] = defaultdict(Counter)
    problems: list[str] = []
    for u in overdue:
        tower = u.zone.zone_name if u.zone else "?"
        arts = await reader.prepare(AnalysisScope(level="UNIT", unit_ids=[u.unit.unit_id]))
        request = unit_request(u.unit.unit_id, arts, manifest.snapshot_id, manifest.semantic_version)
        assert all(a.content_hash == content_hash(a.payload) for a in arts)
        ctx = build_context(request, arts, cfg, AS_OF)
        batch = generate_candidates(ctx, limits.max_candidates_in_context)
        narration = narrate(batch.candidates, None, cfg, ctx.view, request, max_items=limits.max_selected_insights)
        payload = assess(ctx, batch.candidates, batch.rejected, narration).payload

        bridge = [row.cause_code for row, _ in u.causes if row.cause_code in cfg.allowed_cause_codes]
        stated = [i.cause_code for i in payload.insights if i.insight_type == "ROOT_CAUSE_SIGNAL" and i.cause_code]
        rejected = {r.candidate_id.rsplit("-", 1)[-1] for r in payload.rejected_candidates if r.candidate_id.startswith("C-T1-")}
        invented = set(stated) - set(bridge)
        silent = set(bridge) - set(stated) - rejected
        order_ok = stated == [c for c in bridge if c in stated]
        if invented or silent or not order_ok:
            problems.append(f"{u.unit.unit_code}: bridge={bridge} stated={stated} rejected={sorted(rejected)}")
        per_tower[tower]["units"] += 1
        per_tower[tower]["causes_stated"] += len(stated)
        per_tower[tower]["causes_rejected"] += len(set(bridge) - set(stated))
        per_tower[tower]["primary_stated"] += bool(stated) and stated[0] == bridge[0]

    summary = {t: dict(c) for t, c in sorted(per_tower.items())}
    print("\nper tower:", summary)
    assert not problems, f"{len(problems)} units differ from the bridge, e.g. {problems[:5]}"
    assert sum(c["units"] for c in per_tower.values()) == OVERDUE_UNITS
