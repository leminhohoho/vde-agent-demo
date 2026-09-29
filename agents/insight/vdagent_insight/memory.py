"""Agent memory of the Insight Agent (spec §9.5; pipeline step 2 `load()`, step 10 `save_refs()`).

Memory only holds references and presentation preferences, never numbers or conclusions, and a
memory failure never fails the task. Methods are `async` (sdk R10, docs/OPEN_QUESTIONS.md Q5).

- `InsightMemory`: the interface the pipeline uses.
- `NoOpMemory`: remembers nothing (tests, `memory.enabled = false`).
- `CtxMemory`: the store over the sdk's `ctx.memory` (one instance per turn): JSON records with a
  fixed schema per kind in the note text, filtered by expiry, snapshot, conversation and
  authorization on load. A record with an unreadable expiry is skipped (and logged), never fatal.
  `save_refs` first deletes expired records and references of another snapshot, so the store does
  not grow and the scan window (`SCAN_LIMIT`) keeps holding what is still valid.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from pydantic import Field, ValidationError
from vdagent_sdk import Memory, Note

from .contracts import AuthorizedScope, Contract, InsightRef, MemoryContext, RecentRef, UserPref
from .settings import MemoryConfig

log = logging.getLogger("vdagent.plugin.vdagent_insight")


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
MAX_RECENT_REFS = 20
"""`MemoryContext.recent_refs` holds at most this many (contract); the config cannot raise it."""
SNAPSHOT_KINDS = frozenset({"INSIGHT_REF", "TOPIC_SUMMARY"})


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


def _expired(rec: MemoryRecord, now: datetime) -> bool | None:
    """True/False, or None when `expires_at` is unreadable (not ISO, or without a time zone)."""
    if not rec.expires_at:
        return False
    try:
        expires = datetime.fromisoformat(rec.expires_at)
        return expires < now
    except (ValueError, TypeError):
        return None


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
        limit = min(self._cfg.max_refs_per_conversation, MAX_RECENT_REFS)
        for note, rec in await self._records():
            expired = _expired(rec, now)
            if expired is None:
                log.warning("insight memory: note %s has an unreadable expires_at %r; skipped", note.id, rec.expires_at)
                continue
            if expired:
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
            if len(refs) < limit:
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
        await self._purge()
        for ref in refs:
            await self._memory.save(self._record(INSIGHT_REF, conversation_id, ref.model_dump(mode="json")), INSIGHT_REF)
        await self._compact(conversation_id)

    async def _purge(self) -> None:
        """Delete expired records (or with an unreadable expiry) and refs/summaries of another snapshot."""
        now = self._clock()
        gone = [
            note
            for note, rec in await self._records()
            if _expired(rec, now) is not False or (rec.kind in SNAPSHOT_KINDS and rec.snapshot_id != self._snapshot_id)
        ]
        for note in gone:
            await self._memory.delete(note.id)
        if gone:
            log.info("insight memory: purged %d expired or stale records", len(gone))

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
