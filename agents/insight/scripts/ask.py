"""Terminal demo of the Insight Agent without the Backend or Docker.

Usage (repo root):
  uv run python agents/insight/scripts/ask.py "Vì sao tòa Sapphire 1 có nhiều căn bán chậm?"
  uv run python agents/insight/scripts/ask.py --no-llm "Vì sao căn SAPPHIRE1-16.231 bán chậm?"
  uv run python agents/insight/scripts/ask.py --source fixtures --events "thống kê tồn kho The Beverly"

The question goes through exactly what the Orchestrator's message would: bridge.py (free text or a
JSON InsightTaskRequest → request) → ExportArtifactReader → run_task (real LLM from
agents/insight/.env, or TEMPLATE with --no-llm) → store (var/insight_artifacts.db) → the reply.
Prints the reply as the Orchestrator would receive it, then artifact id, status, LLM calls, cost and
latency. Keys are never printed. Memory lives only for the run (the Backend keeps it in ctx.memory).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vdagent_sdk import Message, Note  # noqa: E402

from vdagent_insight.bridge import ARTIFACT_ID, InsightAgent  # noqa: E402
from vdagent_insight.runtime import build_runtime  # noqa: E402
from vdagent_insight.settings import read_env  # noqa: E402


class RunMemory:
    """The sdk `Memory` for one terminal run."""

    def __init__(self) -> None:
        self.notes: list[Note] = []

    async def save(self, text: str, kind: str = "note", embedding: Any = None) -> int:
        self.notes.append(Note(id=len(self.notes) + 1, kind=kind, text=text, created_at=""))
        return len(self.notes)

    async def search(self, query: str, limit: int = 5, embedding: Any = None) -> list[Note]:
        return []

    async def recent(self, limit: int = 10) -> list[Note]:
        return list(reversed(self.notes))[:limit]

    async def delete(self, note_id: int) -> bool:
        before = len(self.notes)
        self.notes = [n for n in self.notes if n.id != note_id]
        return len(self.notes) < before


class TerminalCtx:
    def __init__(self, text: str, invocation_id: str) -> None:
        self.invocation_id = invocation_id
        self.task_id = "t_terminal"
        self.user_id = "u_terminal"
        self.summary = ""
        self.history: list[Message] = [{"role": "user", "content": f"[from: orchestrator] {text}"}]
        self.peers: list[Any] = []
        self.mcp: Any = None
        self.max_steps = 8
        self.memory = RunMemory()
        self.replies: list[str] = []

    async def emit_assistant(self, content: str, tool_calls: Any = ()) -> None:
        self.replies.append(content)

    async def emit_tool_result(self, tool_call_id: str, content: str) -> None:
        raise RuntimeError("insight never calls tools")

    async def call_agent(self, tool_call_id: str, target: str, message: str) -> str:
        raise RuntimeError("insight never calls other agents")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", help="free text, or a JSON InsightTaskRequest")
    ap.add_argument("--no-llm", action="store_true", help="TEMPLATE mode, no LLM call")
    ap.add_argument("--source", choices=["export", "fixtures"], help="data pack (default: export if present)")
    ap.add_argument("--provider", choices=["gemini", "openai"], help="force one provider")
    ap.add_argument("--invocation", help="reuse an invocation id (same id → the stored artifact, TC-29)")
    ap.add_argument("--events", action="store_true", help="print the INSIGHT_* JSON events")
    args = ap.parse_args()

    env = dict(read_env())
    if args.no_llm:
        env["INSIGHT_LLM"] = "off"
    if args.source:
        env["INSIGHT_ARTIFACT_SOURCE"] = args.source
    if args.provider:
        env["INSIGHT_FORCE_PROVIDER"] = args.provider
    logging.basicConfig(level=logging.INFO if args.events else logging.WARNING, format="%(message)s", stream=sys.stderr)
    log = logging.getLogger("vdagent.plugin.vdagent_insight")

    runtime = build_runtime(env, log)
    events: list[dict[str, Any]] = []

    def sink(event: str, fields: dict[str, Any]) -> None:
        events.append({"event": event, **fields})
        if args.events:
            print(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str), file=sys.stderr)

    runtime = dataclasses.replace(runtime, events=sink)
    ctx = TerminalCtx(args.question, args.invocation or f"inv_{uuid.uuid4().hex[:12]}")
    started = time.monotonic()
    await InsightAgent(runtime).invoke(ctx)  # type: ignore[arg-type]
    wall_ms = int((time.monotonic() - started) * 1000)

    (reply,) = ctx.replies
    print(reply)
    done = next((e for e in reversed(events) if e["event"] == "INSIGHT_TASK_COMPLETED"), None)
    print("\n---")
    print(f"source: {runtime.source} · LLM: {'on' if runtime.providers else 'off (TEMPLATE)'} · invocation: {ctx.invocation_id}")
    print(f"artifact: {', '.join(dict.fromkeys(ARTIFACT_ID.findall(reply))) or '-'}")
    if done:
        print(
            f"status: {done['status']} · reused: {done.get('reused')} · LLM calls: {done.get('llm_calls', 0)}"
            f" · cost: {done.get('task_cost_usd') or 0} USD · task: {done.get('duration_ms', '-')} ms"
        )
    print(f"wall time (data pack load included): {wall_ms} ms · reply: {len(reply)} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
