"""The task pipeline `run_task` (steps 0–10): idempotency, input checks E01–E04, deadline, persist,
usage, memory, and TC-08→11, 16, 19, 20, 29, 31, 33 end to end."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..agent import InsightDeps, TaskResult, idempotency_key, run_task
from ..artifacts import FixtureArtifactReader, content_hash
from ..contracts import AuthorizedScope, CallType, InsightRef, InsightTaskRequest, LlmUsage, MemoryContext, RecentRef, Subject
from ..llm import FakeLlmClient, FakeReply
from ..llm.base import Reasoning
from ..llm.steps import LlmProviders
from ..memory import InsightMemory, NoOpMemory
from ..settings import SemanticConfigRegistry
from ..store import InsightStore
from .builders import (
    AS_OF,
    DictReader,
    cause,
    dataset,
    diagnostic,
    dq,
    dq_field,
    input_artifact,
    inventory,
    llm,
    market,
    scope,
    task,
    unit,
    zone,
)
from .test_llm_steps import draft, usage
from .test_t3_pattern import orientation_dataset
from .test_t5_market import window
from .test_tc_cases import FIXTURES

LLM = llm()
MART = "dm_unit_friction_diagnostics"


class Events:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event: str, fields: dict[str, Any]) -> None:
        self.items.append((event, fields))

    def names(self) -> list[str]:
        return [e for e, _ in self.items]

    def of(self, event: str) -> dict[str, Any]:
        return next(f for e, f in self.items if e == event)


def deps(
    tmp_path: Path,
    reader: Any,
    providers: LlmProviders | None = None,
    memory: InsightMemory | None = None,
    events: Events | None = None,
) -> InsightDeps:
    store = InsightStore(tmp_path / "insight.db", clock=lambda: AS_OF)
    return InsightDeps(
        reader=reader,
        registry=SemanticConfigRegistry(),
        llm=LLM,
        store=store,
        usage=store,
        memory=memory or NoOpMemory(),
        providers=providers,
        clock=lambda: AS_OF,
        events=events or Events(),
    )


def overdue_unit(i: int = 11, code: str = "SAPPHIRE1-16.231", **diag: Any) -> Any:
    return dataset([unit(i, unit_code=code)], [inventory(i, 145)], [diagnostic(i, 145, **diag)], [cause(i, "OVERPRICED_VS_PEER")])


def fake(*replies: FakeReply) -> FakeLlmClient:
    return FakeLlmClient(list(replies))


def ok_llm() -> FakeLlmClient:
    return fake(FakeReply(draft("c1"), usage("MAIN", "0.001")))


async def run(tmp_path: Path, request: InsightTaskRequest, reader: Any, **kw: Any) -> tuple[TaskResult, InsightDeps]:
    d = deps(tmp_path, reader, **kw)
    return await run_task(request, d), d


# ---- Happy path, persist, usage ------------------------------------------------------------------


async def test_an_llm_answer_is_validated_persisted_and_costed(tmp_path: Path) -> None:
    request, reader = task(overdue_unit())
    events = Events()
    result, d = await run(tmp_path, request, reader, providers=LlmProviders(ok_llm()), events=events)
    env = result.envelope
    assert result.status == "SUCCEEDED" and result.error_code is None and env is not None and env.status == "VALID"
    assert env.payload.summary.narrative_mode == "LLM" and env.producer.model_id == "gemini-3.5-flash-lite"
    assert env.producer.prompt_version == LLM.prompt_version and env.producer.agent.startswith("insight_agent@")
    assert env.content_hash == content_hash(env.payload.model_dump(mode="json"))
    assert env.snapshot_refs == [request.snapshot_id] and env.semantic_config_version == request.semantic_config_version
    assert [r.artifact_type for r in env.input_artifact_refs] == ["metric", "dq", "dataset", "insight_candidates"]
    assert env.evidence_refs == sorted({f"{e.artifact_id}#{e.path}" for i in env.payload.insights for e in i.evidence_refs})
    assert env.producer.replay is not None and env.producer.replay.prompt_sha256 and env.producer.replay.raw_outputs
    assert result.task_cost_usd == Decimal("0.001") and [u.call_type for u in env.producer.llm_usage] == ["MAIN"]
    assert await d.store.get(env.artifact_id) == env
    cands = env.input_artifact_refs[-1]
    stored = await d.store.get_json(cands.artifact_id)
    assert stored is not None and content_hash(stored) == cands.content_hash and stored["candidates"]
    assert await d.store.usage_count(request.task_id) == 1
    assert events.names()[0] == "INSIGHT_TASK_STARTED" and events.names()[-1] == "INSIGHT_TASK_COMPLETED"
    for name in ("INSIGHT_MEMORY_READ", "INSIGHT_CANDIDATES_GENERATED", "INSIGHT_LLM_CALLED", "INSIGHT_ARTIFACT_PERSISTED"):
        assert name in events.names()
    done = events.of("INSIGHT_TASK_COMPLETED")
    assert done["status"] == "VALID" and done["task_cost_usd"] == "0.001" and done["llm_calls"] == 1
    assert done["narrative_mode"] == "LLM" and done["insights_out"] == len(env.payload.insights)


async def test_without_providers_the_task_runs_in_template_mode(tmp_path: Path) -> None:
    request, reader = task(overdue_unit())
    events = Events()
    result, _ = await run(tmp_path, request, reader, events=events)
    assert result.status == "PARTIAL" and result.envelope is not None
    assert result.envelope.payload.summary.narrative_mode == "TEMPLATE" and result.envelope.producer.model_id is None
    assert result.task_cost_usd is None and "INSIGHT_FALLBACK_TEMPLATE" in events.names()
    assert result.envelope.producer.replay is not None and result.envelope.producer.replay.prompt_sha256 is None


async def test_the_fixture_reader_runs_tc01_end_to_end(tmp_path: Path) -> None:
    folder = FIXTURES / "tc01"
    request = InsightTaskRequest.model_validate_json((folder / "request.json").read_text(encoding="utf-8"))
    result, _ = await run(tmp_path, request, FixtureArtifactReader(folder / "artifacts"))
    assert result.envelope is not None
    keys = [i for i in result.envelope.payload.insights if i.materiality == "KEY"]
    assert any("SAPPHIRE1-16.231" in k.claim.rendered_text and k.cause_code == "OVERPRICED_VS_PEER" for k in keys)


# ---- Step 0: idempotency (TC-29, E17) ------------------------------------------------------------


async def test_tc29_the_same_request_twice_returns_the_stored_artifact_without_llm(tmp_path: Path) -> None:
    request, reader = task(overdue_unit())
    client = ok_llm()
    d = deps(tmp_path, reader, providers=LlmProviders(client))
    first = await run_task(request, d)
    second = await run_task(request, d)
    assert second.reused and not first.reused and second.envelope == first.envelope
    assert second.task_cost_usd == Decimal(0) and len(client.calls) == 1
    assert await d.store.usage_count(request.task_id) == 1


def test_the_idempotency_key_follows_run_task_refs_and_prompt_version() -> None:
    request, _ = task(overdue_unit())
    key = idempotency_key(request, "p1")
    assert key == idempotency_key(request, "p1") and len(key) == 64
    assert key != idempotency_key(request, "p2")
    assert key != idempotency_key(request.model_copy(update={"task_id": "00000000-0000-4000-8000-000000000001"}), "p1")
    refs = [r.model_copy(update={"version": 2}) for r in request.input_artifact_refs]
    assert key != idempotency_key(request.model_copy(update={"input_artifact_refs": refs}), "p1")


# ---- Step 1: input checks (E01–E04) --------------------------------------------------------------


async def rejected(tmp_path: Path, request: InsightTaskRequest, reader: Any, code: str) -> Events:
    events = Events()
    result, d = await run(tmp_path, request, reader, events=events)
    assert (result.status, result.error_code, result.envelope) == ("FAILED", code, None)
    assert result.error_message
    assert events.of("INSIGHT_INPUT_REJECTED")["error_code"] == code
    assert await d.store.find(idempotency_key(request, LLM.prompt_version)) is None
    return events


async def test_tc11_an_input_of_another_snapshot_stops_with_e03(tmp_path: Path) -> None:
    request, reader = task(overdue_unit())
    old_metric = input_artifact("ART-METRIC", "metric", {"metrics": []}, snapshot_id="SNAP-20260531-01")
    refs = [
        r.model_copy(update={"content_hash": old_metric.content_hash}) if r.artifact_type == "metric" else r
        for r in request.input_artifact_refs
    ]
    arts = [old_metric, *(await asyncio.gather(*(reader.read(r.artifact_id) for r in refs[1:])))]
    await rejected(tmp_path, request.model_copy(update={"input_artifact_refs": refs}), DictReader(arts), "E03")


async def test_e03_another_semantic_version_or_an_unknown_one(tmp_path: Path) -> None:
    request, _ = task(overdue_unit())
    await rejected(tmp_path / "a", request, DictReader([]), "E02")  # nothing readable
    other = input_artifact("ART-DQ", "dq", dq(), version="2.0.0")
    request2, reader = task(overdue_unit())
    refs = [
        r.model_copy(update={"content_hash": other.content_hash}) if r.artifact_type == "dq" else r
        for r in request2.input_artifact_refs
    ]
    arts = [other if r.artifact_type == "dq" else await reader.read(r.artifact_id) for r in refs]
    await rejected(tmp_path / "b", request2.model_copy(update={"input_artifact_refs": refs}), DictReader(arts), "E03")
    await rejected(tmp_path / "c", request2.model_copy(update={"semantic_config_version": "9.9.9"}), reader, "E03")


async def test_e02_missing_dq_or_metric_or_a_changed_artifact(tmp_path: Path) -> None:
    request, reader = task(overdue_unit())
    no_dq = [r for r in request.input_artifact_refs if r.artifact_type != "dq"]
    await rejected(tmp_path / "a", request.model_copy(update={"input_artifact_refs": no_dq}), reader, "E02")
    no_metric = [r for r in request.input_artifact_refs if r.artifact_type != "metric"]
    await rejected(tmp_path / "b", request.model_copy(update={"input_artifact_refs": no_metric}), reader, "E02")
    tampered = [
        r.model_copy(update={"content_hash": "b" * 64}) if r.artifact_type == "dataset" else r
        for r in request.input_artifact_refs
    ]
    await rejected(tmp_path / "c", request.model_copy(update={"input_artifact_refs": tampered}), reader, "E02")


async def test_tc16_a_unit_outside_the_authorized_zone_stops_with_e04_and_a_security_event(tmp_path: Path) -> None:
    other = zone(zone_key=2, zone_id="ZN-RUBY-01", zone_name="Tòa Ruby 1")
    data = dataset(
        [unit(1), unit(2, zone_key=2, unit_code="RUBY1-02.002")], [inventory(1, 120), inventory(2, 130)], zones=[zone(), other]
    )
    auth = AuthorizedScope(project_ids=[], zone_ids=["ZN-AQUA-01"])
    request, reader = task(data, analysis_scope=scope("UNIT", unit_ids=["U002"]))
    request = request.model_copy(update={"user_context": request.user_context.model_copy(update={"authorized_scope": auth})})
    events = await rejected(tmp_path, request, reader, "E04")
    assert "INSIGHT_SECURITY_EVENT" in events.names()
    allowed = request.model_copy(update={"analysis_scope": scope("UNIT", unit_ids=["U001"])})
    result, _ = await run(tmp_path / "ok", allowed, reader)
    assert result.envelope is not None
    assert not any("RUBY1" in i.claim.rendered_text for i in result.envelope.payload.insights)


async def test_e04_a_project_scope_wider_than_the_authorized_zone(tmp_path: Path) -> None:
    request, reader = task(overdue_unit())
    auth = AuthorizedScope(project_ids=[], zone_ids=["ZN-AQUA-01"])
    request = request.model_copy(update={"user_context": request.user_context.model_copy(update={"authorized_scope": auth})})
    await rejected(tmp_path, request, reader, "E04")


# ---- Deadline (E16) --------------------------------------------------------------------------------


class SlowClient:
    async def generate_structured(
        self, *, system: str, user: str, schema: type[BaseModel], call_type: CallType, reasoning: Reasoning
    ) -> tuple[BaseModel, LlmUsage]:
        await asyncio.sleep(5)
        raise AssertionError("unreachable")


async def test_e16_the_deadline_gives_a_partial_template_artifact(tmp_path: Path) -> None:
    request, reader = task(overdue_unit(), constraints={"deadline_ms": 300})
    result, _ = await run(tmp_path, request, reader, providers=LlmProviders(SlowClient()))
    env = result.envelope
    assert result.status == "PARTIAL" and env is not None and env.status == "PARTIAL"
    assert env.payload.summary.narrative_mode == "TEMPLATE" and env.payload.insights
    assert [lim.code for lim in env.limitations] == ["DEADLINE_EXCEEDED"]


# ---- Drill-down (spec 9.3) -------------------------------------------------------------------------


async def test_a_drill_down_with_other_content_supersedes_its_parent(tmp_path: Path) -> None:
    request, reader = task(overdue_unit())
    d = deps(tmp_path, reader)
    parent = await run_task(request, d)
    assert parent.envelope is not None
    request2, reader2 = task(overdue_unit(price_spread_vs_peer_pct="20.00"), task_id="00000000-0000-4000-8000-000000000002")
    d2 = deps(tmp_path, reader2)
    child = await run_task(request2.model_copy(update={"parent_insight_ref": parent.envelope.artifact_id}), d2)
    assert child.envelope is not None and child.envelope.version == parent.envelope.version + 1
    assert await d2.store.superseded_by(parent.envelope.artifact_id) == child.envelope.artifact_id


# ---- TC-08 → TC-10, TC-19, TC-20 -------------------------------------------------------------------


async def test_tc08_few_peers_keep_the_insight_with_a_limitation(tmp_path: Path) -> None:
    request, reader = task(overdue_unit(is_peer_sample_constrained=True, peer_count=3))
    result, _ = await run(tmp_path, request, reader, providers=LlmProviders(ok_llm()))
    env = result.envelope
    assert env is not None
    (ins,) = [i for i in env.payload.insights if i.cause_code == "OVERPRICED_VS_PEER"]
    assert ins.confidence.level in ("MEDIUM", "LOW") and "PEER_SAMPLE_CONSTRAINED" in ins.confidence.reasons and ins.limitations
    assert "PEER_SAMPLE_CONSTRAINED" in [lim.code for lim in env.payload.limitations]


async def test_tc09_insight_does_not_depend_on_compare(tmp_path: Path) -> None:
    """Same inputs in two runs (Compare ok / Compare failed): Insight never reads a comparison."""
    request, reader = task(overdue_unit())
    reads: list[str] = []

    class Spy:
        async def read(self, artifact_id: str) -> Any:
            reads.append(artifact_id)
            return await reader.read(artifact_id)

    first, _ = await run(tmp_path / "a", request, Spy())
    second, _ = await run(tmp_path / "b", request.model_copy(update={"run_id": "00000000-0000-4000-8000-00000000000a"}), Spy())
    assert first.envelope is not None and second.envelope is not None
    assert first.envelope.content_hash == second.envelope.content_hash
    assert set(reads) == {"ART-METRIC", "ART-DQ", "ART-DATASET"}


async def test_tc10_dq_fail_on_the_price_field_gives_no_price_key_insight(tmp_path: Path) -> None:
    fail = dq([dq_field(MART, "price_spread_vs_peer_pct", "FAIL", missing="30")])
    request, reader = task(overdue_unit(), fail)
    result, _ = await run(tmp_path, request, reader)
    env = result.envelope
    assert env is not None and env.status != "VALID"
    assert not [i for i in env.payload.insights if i.materiality == "KEY" and i.cause_code == "OVERPRICED_VS_PEER"]
    assert any(i.insight_type == "DATA_LIMITATION" for i in env.payload.insights)
    assert "EVIDENCE_FIELD_MISSING" in [r.reason_code for r in env.payload.rejected_candidates]


async def test_tc19_pattern_by_orientation_end_to_end(tmp_path: Path) -> None:
    data = orientation_dataset({"W": list(range(150, 200, 2)), "E": list(range(30, 60, 2)), "NE": [100, 110]})
    request, reader = task(data, tasks=("T3", "T7"), intent="PERFORMANCE_METRIC_LOOKUP")
    result, _ = await run(tmp_path, request, reader)
    env = result.envelope
    assert env is not None
    (pattern,) = [i for i in env.payload.insights if i.subject.id == "balcony_orientation=W"]
    assert pattern.insight_type == "PATTERN" and "đi kèm" in pattern.claim.rendered_text
    assert ("C-T3-balcony_orientation-NE", "GROUP_TOO_SMALL") in [
        (r.candidate_id, r.reason_code) for r in env.payload.rejected_candidates
    ]


async def test_tc20_market_context_is_context_only(tmp_path: Path) -> None:
    data = dataset([unit(1)], [inventory(1, 120)])
    request, reader = task(data, market_payload=market(window()), tasks=("T1", "T5", "T6", "T7"))
    result, _ = await run(tmp_path, request, reader)
    env = result.envelope
    assert env is not None
    (ctx,) = [i for i in env.payload.insights if i.insight_type == "MARKET_CONTEXT"]
    assert ctx.cause_code is None and ctx.recommendation is None and ctx.materiality != "KEY"


# ---- Memory (TC-31, TC-33) --------------------------------------------------------------------------


class RecordingMemory:
    def __init__(self, context: MemoryContext | None = None, fail: bool = False) -> None:
        self.context = context or MemoryContext.empty()
        self.fail = fail
        self.saved: list[tuple[str | None, list[InsightRef]]] = []

    async def load(
        self, conversation_id: str | None, user_id: str, snapshot_id: str, authorized_scope: AuthorizedScope
    ) -> MemoryContext:
        if self.fail:
            raise RuntimeError("memory store down")
        return self.context

    async def save_refs(self, conversation_id: str | None, refs: list[InsightRef]) -> None:
        if self.fail:
            raise RuntimeError("memory store down")
        self.saved.append((conversation_id, refs))


async def test_tc31_an_old_number_from_memory_is_blocked(tmp_path: Path) -> None:
    subject = Subject(type="unit", id="U011", label="SAPPHIRE1-16.231")
    ref = RecentRef(
        insight_id="INS-OLD", subject=subject, insight_type="ROOT_CAUSE_SIGNAL", cause_code="OVERPRICED_VS_PEER", level="UNIT"
    )
    memory = RecordingMemory(MemoryContext(recent_refs=[ref], topic_summary=[], stale_refs_dropped=0))
    old = draft("c1", "Căn {{unit}} đã tồn 150 ngày; có khả năng liên quan tới {{cause_label}}.")
    client = fake(FakeReply(old, usage("MAIN")), FakeReply(old, usage("REPAIR")))
    request, reader = task(overdue_unit(), conversation_id="00000000-0000-4000-8000-0000000000c1")
    result, _ = await run(tmp_path, request, reader, providers=LlmProviders(client), memory=memory)
    env = result.envelope
    assert env is not None and not any("150" in i.claim.rendered_text for i in env.payload.insights)
    assert "U011" in client.calls[0].user  # the reference reached the prompt, as data
    ((conversation, refs),) = memory.saved
    assert conversation == request.conversation_id and refs
    for r in refs:
        dumped = r.model_dump_json()
        assert "rendered_text" not in dumped and "numeric_bindings" not in dumped and "145" not in dumped


async def test_tc33_memory_failures_never_fail_the_task(tmp_path: Path) -> None:
    events = Events()
    request, reader = task(overdue_unit())
    result, _ = await run(
        tmp_path, request, reader, providers=LlmProviders(ok_llm()), memory=RecordingMemory(fail=True), events=events
    )
    assert result.status == "SUCCEEDED" and result.envelope is not None and result.envelope.status == "VALID"
    assert "INSIGHT_MEMORY_WRITE_FAILED" in events.names()
    assert events.of("INSIGHT_MEMORY_READ")["error_code"] == "E18"
    assert all(u.call_type != "MEMORY" for u in result.envelope.producer.llm_usage)


async def test_tc32_stale_refs_are_dropped_and_the_result_is_as_without_memory(tmp_path: Path) -> None:
    from ..memory import CtxMemory
    from .test_memory import OLD, ListMemory, ref, remember

    store = ListMemory()
    request, reader = task(overdue_unit(), conversation_id="00000000-0000-4000-8000-0000000000c1")
    await remember(store, [ref(1), ref(2), ref(3)], snapshot=OLD, scope=request.user_context.authorized_scope)
    events = Events()
    with_memory, _ = await run(tmp_path / "a", request, reader, memory=CtxMemory(store, LLM.memory, lambda: AS_OF), events=events)
    without, _ = await run(tmp_path / "b", request, reader)
    assert events.of("INSIGHT_MEMORY_READ")["stale_refs_dropped"] == 3
    assert with_memory.envelope is not None and without.envelope is not None
    assert with_memory.envelope.content_hash == without.envelope.content_hash


async def test_a_pinned_as_of_decides_freshness_instead_of_the_wall_clock(tmp_path: Path) -> None:
    request, reader = task(overdue_unit())
    late = AS_OF + timedelta(days=90)
    stale_deps = dataclasses.replace(deps(tmp_path / "a", reader), clock=lambda: late)
    stale = await run_task(request, stale_deps)
    pinned = await run_task(request, dataclasses.replace(deps(tmp_path / "b", reader), clock=lambda: late, as_of=AS_OF))
    assert stale.envelope is not None and pinned.envelope is not None
    flags = [c for i in stale.envelope.payload.insights for c in i.confidence.reasons]
    assert "STALE_SNAPSHOT" in flags
    assert "STALE_SNAPSHOT" not in [c for i in pinned.envelope.payload.insights for c in i.confidence.reasons]
    assert pinned.envelope.producer.replay is not None and pinned.envelope.producer.replay.as_of == AS_OF.isoformat()


async def test_a_zone_question_leads_with_the_zone_cause_distribution(tmp_path: Path) -> None:
    ids = range(1, 7)
    data = dataset(
        [unit(i) for i in ids], [inventory(i, 150 + i) for i in ids], [diagnostic(i, 150 + i) for i in ids],
        [cause(i, "OVERPRICED_VS_PEER") for i in ids],
    )  # fmt: skip
    request, reader = task(data, tasks=("T1", "T2", "T6", "T7"), analysis_scope=scope("ZONE", zone_ids=["ZN-AQUA-01"]))
    result, _ = await run(tmp_path, request, reader)
    env = result.envelope
    assert env is not None
    first = env.payload.insights[0]
    assert (first.insight_type, first.level, first.materiality) == ("CAUSE_DISTRIBUTION", "ZONE", "KEY")
    assert env.payload.summary.headline_insight_ids[0] == first.insight_id


async def test_the_model_id_is_kept_when_some_items_are_templated(tmp_path: Path) -> None:
    two = {**draft("c1"), "selected": [*draft("c1")["selected"], *draft("c2", "Căn {{unit}} cao hơn peer 20%.")["selected"]]}
    data = dataset(
        [unit(11, unit_code="SAPPHIRE1-16.231"), unit(12, unit_code="SAPPHIRE1-16.232")],
        [inventory(11, 145), inventory(12, 150)], [diagnostic(11, 145), diagnostic(12, 150)],
        [cause(11, "OVERPRICED_VS_PEER"), cause(12, "OVERPRICED_VS_PEER")],
    )  # fmt: skip
    client = fake(FakeReply(two, usage("MAIN")), FakeReply(two, usage("REPAIR")))
    request, reader = task(data, analysis_scope=scope("UNIT", unit_ids=["U011", "U012"]))
    result, _ = await run(tmp_path, request, reader, providers=LlmProviders(client))
    env = result.envelope
    assert env is not None and env.payload.summary.narrative_mode == "TEMPLATE"
    assert env.producer.model_id == "gemini-3.5-flash-lite"


async def test_q4_limitations_come_only_from_insights_never_from_rejected_candidates(tmp_path: Path) -> None:
    """Live P5 Q4: limitations of candidates that were not selected (rejected T3 groups) showed up."""
    data = orientation_dataset({"W": list(range(150, 200, 2)), "E": list(range(30, 60, 2)), "NE": [100, 110]})
    request, reader = task(data, tasks=("T3", "T7"), intent="PERFORMANCE_METRIC_LOOKUP")
    result, _ = await run(tmp_path, request, reader)
    env = result.envelope
    assert env is not None and ("C-T3-balcony_orientation-NE", "GROUP_TOO_SMALL") in [
        (r.candidate_id, r.reason_code) for r in env.payload.rejected_candidates
    ]
    ids = {i.insight_id for i in env.payload.insights}
    assert all(set(lim.affected) <= ids for lim in env.payload.limitations)
