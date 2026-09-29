"""The Insight task pipeline (spec §6, steps 0–10): `run_task(request, deps)`.

Ports & adapters (docs/OPEN_QUESTIONS.md Q1): the pipeline knows nothing about `ctx`; bridge.py
turns an invocation into an `InsightTaskRequest`, calls `run_task` and emits the reply.

0. Idempotency: key = sha256(run_id | task_id | hash(input_artifact_refs) | prompt_version); a
   stored artifact with that key is returned as is, no LLM call, cost 0 (TC-29, E17).
1. Input checks, deterministic, never retried: every ref readable, VALID/PARTIAL, of its type and
   with the same content hash as the ref and as its payload, metric + dq + dataset present (E02);
   one snapshot and one semantic_config_version, known to the registry (E03); the analysis scope
   and every unit it reaches inside `authorized_scope` (E04, plus a security event). → FAILED, no
   artifact.
2. Config by version; `memory.load()` (bounded by `memory.job_timeout_s`; any failure → empty
   MemoryContext, E18).
3–4. Sufficiency Gate + Candidate Engine (candidates package); memory subjects only boost priority.
5–8. LLM steps (llm/steps.py) under the task deadline (`constraints.deadline_ms`, D-16); no
   provider, no candidate or deadline → TEMPLATE; the deadline adds the DEADLINE_EXCEEDED
   limitation and makes the artifact PARTIAL (E16).
9. assess.py: confidence, KEY, status.
10. Persist the `insight_candidates` artifact (D-31) and the envelope (canonical SHA-256), record
   `LlmUsage`, warn over the daily budget, supersede the parent of a drill-down (spec 9.3), then
   `memory.save_refs()` for KEY insights (failures only log INSIGHT_MEMORY_WRITE_FAILED).

Every step reports `INSIGHT_*` events (spec 10.2) to `deps.events`; `json_event_sink` writes them
as one JSON log line each (D-50). Only this module does I/O; steps 3–9 stay pure.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from .artifacts import ArtifactNotFound, ArtifactReader, content_hash
from .assess import assess
from .candidates import CandidateContext, build_context, generate_candidates
from .contracts import (
    ArtifactEnvelope,
    AuthorizedScope,
    InputArtifact,
    InsightCandidate,
    InsightRef,
    InsightTaskRequest,
    Limitation,
    LlmUsage,
    MemoryContext,
    Producer,
    ReadArtifactRef,
    RejectedCandidate,
    ReplayInfo,
)
from .llm.prompt import system_prompt, user_prompt
from .llm.steps import LlmProviders, LlmStepsResult, run_llm_steps
from .llm.usage import UsageSink, over_daily_budget, task_cost
from .memory import InsightMemory
from .narrate import Narration, narrate
from .settings import ConfigError, LlmConfig, SemanticConfig, SemanticConfigRegistry
from .store import InsightStore
from .validation import scan_injection
from .view import DatasetView

AGENT = "insight_agent"
AGENT_VERSION = "2.0.0"
REQUIRED_TYPES = ("metric", "dq", "dataset")
RAW_OUTPUT_MAX = 20_000
DEADLINE = "DEADLINE_EXCEEDED"

log = logging.getLogger("vdagent.plugin.vdagent_insight")

EventSink = Callable[[str, dict[str, Any]], None]
TaskStatus = Literal["SUCCEEDED", "PARTIAL", "FAILED"]


def json_event_sink(logger: logging.Logger = log) -> EventSink:
    """One JSON line per event (spec 10.1); the question text is never logged."""

    def sink(event: str, fields: dict[str, Any]) -> None:
        level = logging.WARNING if event in WARNING_EVENTS else logging.INFO
        logger.log(level, json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))

    return sink


WARNING_EVENTS = frozenset(
    {
        "INSIGHT_INPUT_REJECTED",
        "INSIGHT_SECURITY_EVENT",
        "INSIGHT_LLM_FAILED",
        "INSIGHT_VALIDATION_FAILED",
        "INSIGHT_FALLBACK_TEMPLATE",
        "INSIGHT_MEMORY_WRITE_FAILED",
        "INSIGHT_BUDGET_ALERT",
    }
)


@dataclass(frozen=True)
class InsightDeps:
    reader: ArtifactReader
    registry: SemanticConfigRegistry
    llm: LlmConfig
    store: InsightStore
    usage: UsageSink
    memory: InsightMemory
    providers: LlmProviders | None = None
    """None → TEMPLATE mode (no key configured, or `--no-llm`)."""
    clock: Callable[[], datetime] = datetime.now
    events: EventSink = field(default_factory=json_event_sink)


@dataclass(frozen=True)
class TaskResult:
    status: TaskStatus
    envelope: ArtifactEnvelope | None
    error_code: str | None = None
    error_message: str | None = None
    task_cost_usd: Decimal | None = None
    reused: bool = False
    latency_ms: int = 0


class InputRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


def idempotency_key(request: InsightTaskRequest, prompt_version: str) -> str:
    refs = content_hash([r.model_dump(mode="json") for r in request.input_artifact_refs])
    raw = f"{request.run_id}|{request.task_id}|{refs}|{prompt_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---- Step 1 --------------------------------------------------------------------------------------


async def read_inputs(request: InsightTaskRequest, reader: ArtifactReader) -> list[InputArtifact]:
    """E02 / E03 checks of spec 3.4 on every input artifact (a comparison is never read, TC-09)."""
    artifacts: list[InputArtifact] = []
    for ref in request.input_artifact_refs:
        try:
            art = await reader.read(ref.artifact_id)
        except ArtifactNotFound:
            raise InputRejected("E02", f"không đọc được artifact {ref.artifact_id}") from None
        if art.status == "INVALID" or art.artifact_type != ref.artifact_type:
            raise InputRejected("E02", f"artifact {ref.artifact_id} không hợp lệ ({art.status}, {art.artifact_type})")
        if not (art.content_hash == ref.content_hash == content_hash(art.payload)):
            raise InputRejected("E02", f"content_hash của {ref.artifact_id} không khớp")
        if art.snapshot_id != request.snapshot_id or art.semantic_config_version != request.semantic_config_version:
            raise InputRejected(
                "E03",
                f"{ref.artifact_id} thuộc snapshot {art.snapshot_id} / config {art.semantic_config_version}, "
                f"yêu cầu {request.snapshot_id} / {request.semantic_config_version}",
            )
        artifacts.append(art)
    missing = [t for t in REQUIRED_TYPES if not any(a.artifact_type == t for a in artifacts)]
    if missing:
        raise InputRejected("E02", f"thiếu artifact {', '.join(missing)}")
    return artifacts


def scope_violations(request: InsightTaskRequest, view: DatasetView) -> list[str]:
    """Ids of the analysis scope outside `authorized_scope` (E04); pure."""
    auth: AuthorizedScope = request.user_context.authorized_scope
    projects, zones = set(auth.project_ids), set(auth.zone_ids)
    scope = request.analysis_scope
    zone_project = {z.zone_id: p.project_id for z in view.zones for p in view.projects if p.project_key == z.project_key}
    out = [p for p in scope.project_ids if p not in projects]
    out += [z for z in scope.zone_ids if z not in zones and zone_project.get(z) not in projects]
    for u in view.units_in_scope(scope):
        project_ok = u.project is not None and u.project.project_id in projects
        zone_ok = u.zone is not None and u.zone.zone_id in zones
        if not (project_ok or zone_ok):
            out.append(u.unit.unit_id)
    return list(dict.fromkeys(out))


# ---- Steps 2 and 10: memory ----------------------------------------------------------------------


async def load_memory(request: InsightTaskRequest, deps: InsightDeps, emit: EventSink) -> MemoryContext:
    if not deps.llm.memory.enabled:
        return MemoryContext.empty()
    try:
        memory = await asyncio.wait_for(
            deps.memory.load(
                request.conversation_id, request.user_context.user_id, request.snapshot_id, request.user_context.authorized_scope
            ),
            float(deps.llm.memory.job_timeout_s),
        )
    except Exception as exc:  # E18: never fails the task
        emit("INSIGHT_MEMORY_READ", {"error_code": "E18", "detail": type(exc).__name__, "refs": 0, "stale_refs_dropped": 0})
        return MemoryContext.empty()
    emit("INSIGHT_MEMORY_READ", {"refs": len(memory.recent_refs), "stale_refs_dropped": memory.stale_refs_dropped})
    return memory


async def save_memory(request: InsightTaskRequest, envelope: ArtifactEnvelope, deps: InsightDeps, emit: EventSink) -> None:
    if not deps.llm.memory.enabled or envelope.status == "INVALID":
        return
    refs = [
        InsightRef(
            insight_id=i.insight_id, artifact_id=envelope.artifact_id, subject=i.subject, insight_type=i.insight_type,
            cause_code=i.cause_code, level=i.level, materiality=i.materiality,
        )  # fmt: skip
        for i in envelope.payload.insights
        if i.materiality == "KEY"
    ]
    if not refs:
        return
    try:
        await asyncio.wait_for(deps.memory.save_refs(request.conversation_id, refs), float(deps.llm.memory.job_timeout_s))
    except Exception as exc:
        emit("INSIGHT_MEMORY_WRITE_FAILED", {"detail": type(exc).__name__})
        return
    emit("INSIGHT_MEMORY_WRITTEN", {"refs": len(refs)})


# ---- Steps 5–8 -------------------------------------------------------------------------------------


@dataclass
class Narrated:
    narration: Narration
    kept: list[InsightCandidate]
    rejected: list[RejectedCandidate] = field(default_factory=list)
    steps: LlmStepsResult | None = None
    deadline_hit: bool = False
    prompt_sha256: str | None = None


def _template(ctx: CandidateContext, candidates: list[InsightCandidate], deps: InsightDeps) -> Narration:
    return narrate(candidates, None, ctx.cfg, ctx.view, ctx.request, max_items=deps.llm.limits.max_selected_insights)


async def narrate_task(
    ctx: CandidateContext, candidates: list[InsightCandidate], memory: MemoryContext, deps: InsightDeps, remaining_s: float
) -> Narrated:
    if deps.providers is None or not candidates:
        return Narrated(_template(ctx, candidates, deps), candidates)
    try:
        steps = await asyncio.wait_for(
            run_llm_steps(ctx, candidates, deps.providers, deps.llm, memory), max(remaining_s, 0.001)
        )
    except TimeoutError:
        return Narrated(_template(ctx, candidates, deps), candidates, deadline_hit=True)
    prompt = system_prompt(ctx.cfg, deps.llm) + "\n" + user_prompt(ctx.request, steps.kept, memory, ctx.cfg)
    return Narrated(
        steps.narration, steps.kept, steps.rejected, steps, prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    )


def _llm_events(n: Narrated, emit: EventSink) -> None:
    steps = n.steps
    if steps is not None:
        for u in steps.usages:
            emit("INSIGHT_LLM_CALLED", _usage_fields(u))
        if steps.main_error:
            emit("INSIGHT_LLM_FAILED", {"error_code": steps.main_error, "events": steps.events})
    codes = sorted({v.code for vs in n.narration.violations.values() for v in vs})
    if codes:
        emit("INSIGHT_VALIDATION_FAILED", {"error_codes": codes, "items": len(n.narration.violations)})
    templated = sum(1 for i in n.narration.items if i.source == "TEMPLATE")
    if n.narration.narrative_mode == "TEMPLATE" or n.deadline_hit:
        reason = "E16" if n.deadline_hit else ("NO_PROVIDER" if steps is None else (steps.main_error or "VALIDATION"))
        emit("INSIGHT_FALLBACK_TEMPLATE", {"reason": reason, "items": templated})


def _usage_fields(u: LlmUsage) -> dict[str, Any]:
    return {**u.model_dump(mode="json"), "cost_usd": None if u.cost_usd is None else str(u.cost_usd)}


# ---- Step 10 ----------------------------------------------------------------------------------------


def _read_ref(art: InputArtifact) -> ReadArtifactRef:
    return ReadArtifactRef.model_validate(
        {"artifact_id": art.artifact_id, "artifact_type": art.artifact_type, "version": art.version, "content_hash": art.content_hash}
    )


def _replay(n: Narrated, deps: InsightDeps, as_of: datetime) -> ReplayInfo:
    llm = deps.llm
    raw = [r[:RAW_OUTPUT_MAX] for r in (n.steps.raw_outputs if n.steps else [])]
    params = {
        "primary": f"{llm.primary.provider}/{llm.primary.model_id} thinking={llm.primary.thinking_level}",
        "fallback": f"{llm.fallback.provider}/{llm.fallback.model_id} effort={llm.fallback.reasoning_effort}",
        "repair": f"reasoning={llm.repair.reasoning} max_attempts={llm.repair.max_attempts}",
        "max_output_tokens": str(llm.limits.max_output_tokens),
        "llm_config_version": llm.version,
    }
    return ReplayInfo(prompt_sha256=n.prompt_sha256, raw_outputs=raw, params=params, as_of=as_of.isoformat())


def _task_status(status: str) -> TaskStatus:
    return {"VALID": "SUCCEEDED", "PARTIAL": "PARTIAL"}.get(status, "FAILED")  # type: ignore[return-value]


async def run_task(request: InsightTaskRequest, deps: InsightDeps) -> TaskResult:
    started = time.monotonic()
    base = {"run_id": request.run_id, "task_id": request.task_id, "attempt": request.attempt, "agent": AGENT}

    def emit(event: str, fields: dict[str, Any]) -> None:
        deps.events(event, {**base, "ts": deps.clock().isoformat(), **fields})

    def elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    emit("INSIGHT_TASK_STARTED", {"intent": request.intent, "tasks": list(request.tasks), "level": request.analysis_scope.level})

    # 0. idempotency
    key = idempotency_key(request, deps.llm.prompt_version)
    stored = await deps.store.find(key)
    if stored is not None:
        emit("INSIGHT_TASK_COMPLETED", {"status": stored.status, "reused": True, "task_cost_usd": "0", "llm_calls": 0})
        return TaskResult(_task_status(stored.status), stored, task_cost_usd=Decimal(0), reused=True, latency_ms=elapsed_ms())

    # 1–2. input checks, config, memory
    try:
        try:
            cfg: SemanticConfig = deps.registry.get(request.semantic_config_version)
        except ConfigError:
            raise InputRejected("E03", f"không có semantic_config version {request.semantic_config_version}") from None
        artifacts = await read_inputs(request, deps.reader)
        as_of = deps.clock()
        ctx = build_context(request, artifacts, cfg, as_of)
        outside = scope_violations(request, ctx.view)
        if outside:
            emit("INSIGHT_SECURITY_EVENT", {"error_code": "E04", "outside_scope": len(outside)})
            raise InputRejected("E04", "phạm vi phân tích vượt quyền của người dùng")
    except InputRejected as exc:
        emit("INSIGHT_INPUT_REJECTED", {"error_code": exc.code, "detail": exc.message, "duration_ms": elapsed_ms()})
        return TaskResult("FAILED", None, exc.code, exc.message, latency_ms=elapsed_ms())

    memory = await load_memory(request, deps, emit)
    labels = [request.question_normalized, *(u.unit.unit_code for u in ctx.view.units)]
    labels += [z.zone_name for z in ctx.view.zones] + [p.project_name for p in ctx.view.projects]
    if scan_injection(labels, cfg):
        emit("INSIGHT_SECURITY_EVENT", {"error_code": "E13"})

    # 3–4. gate + candidates (memory subjects only boost priority)
    ctx = build_context(
        request, artifacts, cfg, as_of,
        recent_subject_ids=frozenset(r.subject.id for r in memory.recent_refs),
        recent_subject_boost=deps.llm.memory.recent_subject_boost,
    )  # fmt: skip
    batch = generate_candidates(ctx, deps.llm.limits.max_candidates_in_context)
    emit(
        "INSIGHT_CANDIDATES_GENERATED",
        {"by_task": dict(sorted(Counter(c.task for c in batch.candidates).items())), "rejected": len(batch.rejected)},
    )

    # 5–8. LLM steps under the deadline, else TEMPLATE
    remaining = request.constraints.deadline_ms / 1000 - (time.monotonic() - started)
    n = await narrate_task(ctx, batch.candidates, memory, deps, remaining)
    _llm_events(n, emit)

    # 9. assess
    result = assess(ctx, n.kept, [*batch.rejected, *n.rejected], n.narration, show_recommendation=memory.user_pref.show_recommendation)
    status = result.status
    limitations: list[Limitation] = []
    if n.deadline_hit:
        limitations.append(Limitation(code=DEADLINE, message=cfg.language.limitation_messages[DEADLINE], affected=[]))
        status = "PARTIAL" if status == "VALID" else status

    # 10. persist
    usages = n.steps.usages if n.steps else []
    body = {
        "candidates": [c.model_dump(mode="json") for c in batch.candidates],
        "rejected": [r.model_dump(mode="json") for r in [*batch.rejected, *n.rejected]],
    }
    cands_hash = content_hash(body)
    cands_ref = ReadArtifactRef(
        artifact_id=f"ART-INSIGHT-CANDIDATES-{key[:16]}", artifact_type="insight_candidates", version=1, content_hash=cands_hash
    )
    await deps.store.save_json(cands_ref.artifact_id, "insight_candidates", body, cands_hash)

    parent = await deps.store.get(request.parent_insight_ref) if request.parent_insight_ref else None
    payload = result.payload
    evidence = sorted({f"{e.artifact_id}#{e.path}" for i in payload.insights for e in i.evidence_refs})
    envelope = ArtifactEnvelope(
        artifact_id=f"ART-INSIGHT-{key[:16]}",
        version=parent.version + 1 if parent else 1,
        status=status,
        producer=Producer(
            agent=f"{AGENT}@{AGENT_VERSION}",
            prompt_version=deps.llm.prompt_version,
            model_id=usages[-1].model_id if usages and payload.summary.narrative_mode == "LLM" else None,
            llm_usage=list(usages),
            replay=_replay(n, deps, as_of),
        ),
        snapshot_refs=[request.snapshot_id],
        semantic_config_version=request.semantic_config_version,
        input_artifact_refs=[*(_read_ref(a) for a in artifacts), cands_ref],
        evidence_refs=evidence,
        content_hash=content_hash(payload.model_dump(mode="json")),
        limitations=limitations,
        payload=payload,
    )
    saved = await deps.store.save(envelope, key)
    if saved.artifact_id != envelope.artifact_id:  # E17: another worker wrote first
        emit("INSIGHT_TASK_COMPLETED", {"status": saved.status, "reused": True, "error_code": "E17"})
        return TaskResult(_task_status(saved.status), saved, task_cost_usd=Decimal(0), reused=True, latency_ms=elapsed_ms())
    if parent is not None and parent.content_hash != envelope.content_hash:
        await deps.store.supersede(parent.artifact_id, envelope.artifact_id)
    cost = task_cost(list(usages))
    if usages:
        await deps.usage.record(request.task_id, list(usages))
        spent = await deps.usage.spent_today()
        if over_daily_budget(spent, deps.llm):
            emit("INSIGHT_BUDGET_ALERT", {"spent_today_usd": str(spent), "daily_usd": str(deps.llm.budget.daily_usd)})
    emit("INSIGHT_ARTIFACT_PERSISTED", {"artifact_id": envelope.artifact_id, "status": status, "content_hash": envelope.content_hash})

    await save_memory(request, envelope, deps, emit)
    emit(
        "INSIGHT_TASK_COMPLETED",
        {
            "status": status,
            "reused": False,
            "duration_ms": elapsed_ms(),
            "task_cost_usd": None if cost is None else str(cost),
            "llm_calls": len(usages),
            "narrative_mode": payload.summary.narrative_mode,
            "candidates_in": len(batch.candidates),
            "candidates_sent": len(n.kept) if n.steps else 0,
            "insights_out": len(payload.insights),
        },
    )
    return TaskResult(_task_status(status), envelope, task_cost_usd=cost, latency_ms=elapsed_ms())
