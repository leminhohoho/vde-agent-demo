"""Agent memory of the Insight Agent.

`InsightMemory` / `NoOpMemory` (spec §9.5; pipeline step 2 `load()` and step 10 `save_refs()`):
the interface of the v2 pipeline. Memory only holds references and presentation preferences, never
numbers or conclusions, and a memory failure never fails the task. Methods are `async` (sdk R10,
docs/OPEN_QUESTIONS.md Q5). `NoOpMemory` serves tests and `memory.enabled = false`; the store over
`ctx.memory` is `CtxMemory` below.

`MemoryMiddleware` (legacy LangChain agent, removed in P4): this agent's own context engineering
over `ctx.memory`.

The Backend only stores and ranks notes. What is worth remembering, how it is found again and how
it reaches the model are this agent's decisions:

- before the turn: embed the inbound message, fetch the nearest earlier findings for this user and
  append them to the system prompt of every model call of the turn;
- after the turn: ask the model for at most `MAX_FINDINGS` durable findings of this answer, embed
  each, skip near-duplicates of what is already known, save the rest as `finding` notes.

Memory is best effort: any failure is logged and the turn goes on (the answer is already emitted
when the extraction runs). One instance per turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import Field, ValidationError
from vdagent_sdk import InvocationContext, Memory, Note

from .contracts import AuthorizedScope, Contract, InsightRef, MemoryContext, RecentRef, UserPref
from .settings import MemoryConfig

log = logging.getLogger(__name__)


class InsightMemory(Protocol):
    async def load(
        self, conversation_id: str | None, user_id: str, snapshot_id: str, authorized_scope: AuthorizedScope
    ) -> MemoryContext:
        """Drop expired records, INSIGHT_REFs of another snapshot (E19) and subjects outside the scope."""
        ...

    async def save_refs(self, conversation_id: str | None, refs: list[InsightRef]) -> None:
        """One INSIGHT_REF per KEY insight, only after a VALID or PARTIAL artifact."""
        ...


class NoOpMemory:
    """Remembers nothing: every load is `MemoryContext.empty()`."""

    async def load(
        self, conversation_id: str | None, user_id: str, snapshot_id: str, authorized_scope: AuthorizedScope
    ) -> MemoryContext:
        return MemoryContext.empty()

    async def save_refs(self, conversation_id: str | None, refs: list[InsightRef]) -> None:
        return None


# ---- CtxMemory: the store over the sdk's ctx.memory (spec 9.5, D-40, D-42) -------------------------

INSIGHT_REF, TOPIC_SUMMARY, USER_PREF = "INSIGHT_REF", "TOPIC_SUMMARY", "USER_PREF"
RECORD_SCHEMA = "insight.memory.v1"
SCAN_LIMIT = 500
"""Notes read per load: well above `max_refs_per_conversation` + summaries + preferences."""
MAX_TOPICS = 10


class MemoryRecord(Contract):
    """The JSON in a note's `text`: fixed schema per kind, no free text (spec 9.5)."""

    schema_: Literal["insight.memory.v1"] = Field(alias="schema")
    kind: Literal["INSIGHT_REF", "TOPIC_SUMMARY", "USER_PREF"]
    scope: Literal["CONVERSATION", "USER"] = "USER"
    scope_key: str | None = None
    snapshot_id: str | None = None
    created_at: str | None = None
    expires_at: str | None = None
    authorized_scope: AuthorizedScope | None = None
    """The writer's authorization; a reader with less is not shown the record (E19)."""
    payload: dict[str, Any]


class TopicSummary(Contract):
    topics: list[str] = Field(max_length=MAX_TOPICS)


def _within(inner: AuthorizedScope | None, outer: AuthorizedScope) -> bool:
    return inner is not None and set(inner.project_ids) <= set(outer.project_ids) and set(inner.zone_ids) <= set(outer.zone_ids)


def topic_of(ref: InsightRef) -> str:
    return f"{ref.subject.type} {ref.subject.label}: {ref.cause_code or ref.insight_type}"


class CtxMemory:
    """`InsightMemory` over the sdk `Memory` of one turn (scoped to user x agent by the Backend).

    Writes only INSIGHT_REF (one per KEY insight) and a deterministic TOPIC_SUMMARY: past
    `max_refs_per_conversation`, the oldest refs fold into "<subject>: <cause>" topics (no LLM,
    D-40, D-42). USER_PREF is read if present, never written (the extract job is out of the demo).
    `save_refs` reuses the snapshot and authorization given to `load` (one instance per task).
    """

    def __init__(self, memory: Memory, cfg: MemoryConfig, clock: Callable[[], datetime]) -> None:
        self._memory, self._cfg, self._clock = memory, cfg, clock
        self._snapshot_id: str | None = None
        self._scope: AuthorizedScope | None = None

    async def _records(self) -> list[tuple[Note, MemoryRecord]]:
        out: list[tuple[Note, MemoryRecord]] = []
        for note in await self._memory.recent(SCAN_LIMIT):  # newest first
            try:
                out.append((note, MemoryRecord.model_validate(json.loads(note.text))))
            except (ValueError, ValidationError):
                continue
        return out

    async def load(
        self, conversation_id: str | None, user_id: str, snapshot_id: str, authorized_scope: AuthorizedScope
    ) -> MemoryContext:
        self._snapshot_id, self._scope = snapshot_id, authorized_scope
        now = self._clock()
        refs: list[RecentRef] = []
        topics: list[str] = []
        pref: UserPref | None = None
        stale = 0
        for _, rec in await self._records():
            if rec.expires_at and datetime.fromisoformat(rec.expires_at) < now:
                continue
            if rec.kind == USER_PREF:
                if pref is None:
                    try:
                        pref = UserPref.model_validate(rec.payload)
                    except ValidationError:
                        continue
                continue
            if conversation_id is None or rec.scope != "CONVERSATION" or rec.scope_key != conversation_id:
                continue
            if rec.kind == TOPIC_SUMMARY:
                if not topics and rec.snapshot_id == snapshot_id and _within(rec.authorized_scope, authorized_scope):
                    try:
                        topics = TopicSummary.model_validate(rec.payload).topics
                    except ValidationError:
                        continue
                continue
            try:
                ref = InsightRef.model_validate(rec.payload)
            except ValidationError:
                continue
            if rec.snapshot_id != snapshot_id or not _within(rec.authorized_scope, authorized_scope):
                stale += 1  # E19
                continue
            if len(refs) < self._cfg.max_refs_per_conversation:
                refs.append(
                    RecentRef(
                        insight_id=ref.insight_id,
                        subject=ref.subject,
                        insight_type=ref.insight_type,
                        cause_code=ref.cause_code,
                        level=ref.level,
                    )
                )
        return MemoryContext(recent_refs=refs, topic_summary=topics, user_pref=pref or UserPref(), stale_refs_dropped=stale)

    def _record(self, kind: str, conversation_id: str, payload: dict[str, Any]) -> str:
        now = self._clock()
        rec = MemoryRecord.model_validate(
            {
                "schema": RECORD_SCHEMA, "kind": kind, "scope": "CONVERSATION", "scope_key": conversation_id,
                "snapshot_id": self._snapshot_id, "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=self._cfg.conversation_ttl_days)).isoformat(),
                "authorized_scope": self._scope, "payload": payload,
            }
        )  # fmt: skip
        return rec.model_dump_json(by_alias=True)

    async def save_refs(self, conversation_id: str | None, refs: list[InsightRef]) -> None:
        if conversation_id is None or self._snapshot_id is None or not refs:
            return
        for ref in refs:
            await self._memory.save(self._record(INSIGHT_REF, conversation_id, ref.model_dump(mode="json")), INSIGHT_REF)
        await self._compact(conversation_id)

    async def _compact(self, conversation_id: str) -> None:
        mine = [
            (note, rec)
            for note, rec in await self._records()
            if rec.scope == "CONVERSATION" and rec.scope_key == conversation_id and rec.snapshot_id == self._snapshot_id
        ]
        refs = [(n, r) for n, r in mine if r.kind == INSIGHT_REF]
        excess = len(refs) - self._cfg.max_refs_per_conversation
        if excess <= 0:
            return
        folded = list(reversed(refs[-excess:]))  # oldest first
        summaries = [(n, r) for n, r in mine if r.kind == TOPIC_SUMMARY]
        topics = TopicSummary.model_validate(summaries[0][1].payload).topics if summaries else []
        for _, rec in folded:
            topic = topic_of(InsightRef.model_validate(rec.payload))
            if topic not in topics:
                topics.append(topic)
        payload = TopicSummary(topics=topics[-MAX_TOPICS:]).model_dump(mode="json")
        await self._memory.save(self._record(TOPIC_SUMMARY, conversation_id, payload), TOPIC_SUMMARY)
        for note, _ in [*summaries, *folded]:
            await self._memory.delete(note.id)


RECALL_LIMIT = 5
MAX_FINDINGS = 3
DUPLICATE_DISTANCE = 0.1
"""Cosine distance below which a new finding counts as already known."""
RECALL_HEADING = "## What you already found for this user (earlier tasks)"
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def render_recall(notes: list[Note]) -> str:
    if not notes:
        return ""
    lines = [f"- ({n.created_at[:10]}) {n.text}" for n in notes]
    return "\n".join([RECALL_HEADING, *lines])


def parse_findings(text: str) -> list[str]:
    """The extraction reply: a JSON list of strings, optionally in a code fence."""
    data = json.loads(_FENCE.sub("", text.strip()))
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise ValueError(f"expected a JSON list of strings, got {text[:200]!r}")
    return [item.strip() for item in data if item.strip()][:MAX_FINDINGS]


class MemoryMiddleware(AgentMiddleware):
    def __init__(self, ctx: InvocationContext, model: BaseChatModel, embeddings: Embeddings, extract_prompt: str) -> None:
        super().__init__()
        self._ctx = ctx
        self._model = model
        self._embeddings = embeddings
        self._extract_prompt = extract_prompt
        self._recall = ""

    def _inbound(self) -> str:
        return str(self._ctx.history[-1]["content"])

    async def abefore_agent(self, state: Any, runtime: Any) -> None:
        try:
            inbound = self._inbound()
            vector = await self._embeddings.aembed_query(inbound)
            notes = await self._ctx.memory.search(inbound, RECALL_LIMIT, embedding=vector)
            self._recall = render_recall(notes)
            log.info("memory: recalled %s", [(n.id, round(n.score or 0.0, 3)) for n in notes] or "nothing")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("memory recall failed; continuing without it: %s", exc)

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        if self._recall:
            base = request.system_message.text if request.system_message else ""
            request = request.override(system_message=SystemMessage(f"{base}\n\n{self._recall}".strip()))
        return await handler(request)

    async def aafter_agent(self, state: Any, runtime: Any) -> None:
        answer = next((m.text for m in reversed(state["messages"]) if isinstance(m, AIMessage)), "")
        try:
            await self._remember(answer)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("memory extraction failed; nothing saved: %s", exc)

    async def _remember(self, answer: str) -> None:
        reply = await self._model.ainvoke(
            [
                SystemMessage(self._extract_prompt),
                HumanMessage(f"Request:\n{self._inbound()}\n\nFinal answer:\n{answer}"),
            ]
        )
        for finding in parse_findings(reply.text):
            vector = await self._embeddings.aembed_query(finding)
            nearest = await self._ctx.memory.search(finding, 1, embedding=vector)
            if nearest and nearest[0].score is not None and nearest[0].score < DUPLICATE_DISTANCE:
                log.info("memory: skipping known finding %r (like note %d)", finding, nearest[0].id)
                continue
            note_id = await self._ctx.memory.save(finding, "finding", vector)
            log.info("memory: saved finding %d: %s", note_id, finding)
