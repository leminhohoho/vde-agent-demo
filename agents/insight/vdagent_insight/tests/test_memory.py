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
