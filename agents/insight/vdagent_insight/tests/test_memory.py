"""Agent memory interface (spec §9.5). Phase 0: only `NoOpMemory`."""

from __future__ import annotations

from ..contracts import AuthorizedScope, InsightRef, MemoryContext, Subject
from ..memory import InsightMemory, NoOpMemory

SCOPE = AuthorizedScope(project_ids=["PRJ-X"], zone_ids=[])


async def test_noop_memory_loads_an_empty_context_and_forgets_what_is_saved() -> None:
    memory: InsightMemory = NoOpMemory()
    ref = InsightRef(
        insight_id="INS-001",
        artifact_id="ART-I-1",
        subject=Subject(type="unit", id="U011", label="A-05.03"),
        insight_type="ROOT_CAUSE_SIGNAL",
        cause_code="OVERPRICED_VS_PEER",
        level="UNIT",
        materiality="KEY",
    )
    assert await memory.load("conv-1", "u_1", "SNAP-20260630-01", SCOPE) == MemoryContext.empty()
    await memory.save_refs("conv-1", [ref])
    assert await memory.load("conv-1", "u_1", "SNAP-20260630-01", SCOPE) == MemoryContext.empty()
    assert await memory.load(None, "u_1", "SNAP-20260630-01", SCOPE) == MemoryContext.empty()


# ---- CtxMemory: agent memory over the sdk's ctx.memory (spec 9.5, D-40, D-42) --------------------

from datetime import datetime, timedelta  # noqa: E402
from typing import Any  # noqa: E402

from vdagent_sdk import Note  # noqa: E402

from ..memory import INSIGHT_REF, TOPIC_SUMMARY, USER_PREF, CtxMemory  # noqa: E402
from .builders import AS_OF, llm  # noqa: E402

SNAP, OLD = "SNAP-20260630-01", "SNAP-20260531-01"
CONV = "00000000-0000-4000-8000-0000000000c1"


class ListMemory:
    """The sdk `Memory` of one (user, agent) scope, in a list (newest last)."""

    def __init__(self) -> None:
        self.notes: list[Note] = []

    async def save(self, text: str, kind: str = "note", embedding: Any = None) -> int:
        note_id = len(self.notes) + 1 + sum(1 for n in self.notes if n.id < 0)
        self.notes.append(Note(id=note_id, kind=kind, text=text, created_at=AS_OF.isoformat()))
        return note_id

    async def search(self, query: str, limit: int = 5, embedding: Any = None) -> list[Note]:
        return []

    async def recent(self, limit: int = 10) -> list[Note]:
        return list(reversed(self.notes))[:limit]

    async def delete(self, note_id: int) -> bool:
        before = len(self.notes)
        self.notes = [n for n in self.notes if n.id != note_id]
        return len(self.notes) < before


def ref(i: int, subject_type: str = "unit", cause: str | None = "OVERPRICED_VS_PEER") -> InsightRef:
    return InsightRef(
        insight_id=f"INS-{i:03d}", artifact_id="ART-INSIGHT-1",
        subject=Subject(type=subject_type, id=f"U{i:03d}", label=f"A-{i:02d}.01"),
        insight_type="ROOT_CAUSE_SIGNAL", cause_code=cause, level="UNIT", materiality="KEY",
    )  # fmt: skip


def ctx_memory(store: ListMemory, now: datetime = AS_OF) -> CtxMemory:
    return CtxMemory(store, llm().memory, clock=lambda: now)


async def remember(store: ListMemory, refs: list[InsightRef], snapshot: str = SNAP, scope: AuthorizedScope = SCOPE) -> None:
    m = ctx_memory(store)
    await m.load(CONV, "u_1", snapshot, scope)
    await m.save_refs(CONV, refs)


async def test_refs_are_saved_as_schema_json_and_read_back_newest_first() -> None:
    store = ListMemory()
    await remember(store, [ref(1), ref(2)])
    assert [n.kind for n in store.notes] == [INSIGHT_REF, INSIGHT_REF]
    for n in store.notes:
        assert "rendered_text" not in n.text and "numeric_bindings" not in n.text
    got = await ctx_memory(store).load(CONV, "u_1", SNAP, SCOPE)
    assert [r.insight_id for r in got.recent_refs] == ["INS-002", "INS-001"] and got.stale_refs_dropped == 0


async def test_tc32_refs_of_another_snapshot_are_dropped_and_counted() -> None:
    store = ListMemory()
    await remember(store, [ref(1), ref(2), ref(3)], snapshot=OLD)
    got = await ctx_memory(store).load(CONV, "u_1", SNAP, SCOPE)
    assert got.recent_refs == [] and got.stale_refs_dropped == 3
    assert got.model_copy(update={"stale_refs_dropped": 0}) == MemoryContext.empty()


async def test_expired_refs_other_conversations_and_no_conversation_are_skipped() -> None:
    store = ListMemory()
    await remember(store, [ref(1)])
    later = AS_OF + timedelta(days=llm().memory.conversation_ttl_days + 1)
    assert (await ctx_memory(store, later).load(CONV, "u_1", SNAP, SCOPE)).recent_refs == []
    other = "00000000-0000-4000-8000-0000000000c2"
    assert (await ctx_memory(store).load(other, "u_1", SNAP, SCOPE)).recent_refs == []
    assert await ctx_memory(store).load(None, "u_1", SNAP, SCOPE) == MemoryContext.empty()
    m = ctx_memory(store)
    await m.load(None, "u_1", SNAP, SCOPE)
    await m.save_refs(None, [ref(2)])
    assert len(store.notes) == 1  # no conversation → nothing written


async def test_refs_written_under_a_wider_authorization_are_dropped() -> None:
    store = ListMemory()
    wide = AuthorizedScope(project_ids=["PRJ-X", "PRJ-Y"], zone_ids=[])
    await remember(store, [ref(1)], scope=wide)
    got = await ctx_memory(store).load(CONV, "u_1", SNAP, SCOPE)
    assert got.recent_refs == [] and got.stale_refs_dropped == 1


async def test_over_max_refs_the_oldest_fold_into_a_deterministic_topic_summary() -> None:
    store = ListMemory()
    cap = llm().memory.max_refs_per_conversation
    await remember(store, [ref(i) for i in range(1, cap + 4)])
    kinds = [n.kind for n in store.notes]
    assert kinds.count(INSIGHT_REF) == cap and kinds.count(TOPIC_SUMMARY) == 1
    got = await ctx_memory(store).load(CONV, "u_1", SNAP, SCOPE)
    assert len(got.recent_refs) == cap
    assert got.topic_summary == [
        "unit A-01.01: OVERPRICED_VS_PEER",
        "unit A-02.01: OVERPRICED_VS_PEER",
        "unit A-03.01: OVERPRICED_VS_PEER",
    ]
    store2 = ListMemory()
    await remember(store2, [ref(i) for i in range(1, cap + 4)])
    assert [n.text for n in store2.notes] == [n.text for n in store.notes]  # deterministic


async def test_user_pref_is_read_and_bad_records_are_ignored() -> None:
    store = ListMemory()
    await store.save('{"schema": "insight.memory.v1", "kind": "USER_PREF", "payload": {"show_recommendation": false}}', USER_PREF)
    await store.save("free text the agent never wrote", INSIGHT_REF)
    await store.save('{"schema": "insight.memory.v1", "kind": "USER_PREF", "payload": {"verbosity": "HUGE"}}', USER_PREF)
    got = await ctx_memory(store).load(CONV, "u_1", SNAP, SCOPE)
    assert got.user_pref.show_recommendation is False and got.recent_refs == []
