"""Agent memory of the Insight Agent.

`InsightMemory` / `NoOpMemory` (spec §9.5; pipeline step 2 `load()` and step 10 `save_refs()`):
the interface of the v2 pipeline. Memory only holds references and presentation preferences, never
numbers or conclusions, and a memory failure never fails the task. Methods are `async` (sdk R10,
docs/OPEN_QUESTIONS.md Q5). `NoOpMemory` serves tests and `memory.enabled = false`; the store over
`ctx.memory` comes in phase P4.

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
from typing import Any, Protocol

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from vdagent_sdk import InvocationContext, Note

from .contracts import AuthorizedScope, InsightRef, MemoryContext

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
