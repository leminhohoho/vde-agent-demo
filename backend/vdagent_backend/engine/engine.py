"""Invocation engine (§4): scheduling, per-invocation turns over the agent hub, call checks,
compaction, failure / cancel / restart handling, and SSE publication.

Concurrency model (single process, single event loop):
- One `Stack` per (user, agent): `running` is the lock holder, `queue` the FIFO of waiting
  invocations (§4.2). Only the holder's asyncio task writes that stack's messages (I1).
- One asyncio task per running invocation drives its turn channel; `AgentCallResult`s for its
  child calls are pushed onto its outbound queue by whichever task finishes the child.
- The per-user `WaitGraph` gets edge `caller → target` synchronously with the call checks, so the
  graph stays acyclic (I3).
- Cancellation is cooperative: `cancel_task` flags the runs and cancels their in-flight turn or
  compaction; each run then patches its own stack (I2) before releasing it. DB writes are never
  interrupted.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from vdagent_backend.config import Config
from vdagent_backend.db import repo
from vdagent_backend.engine.hub import AgentError, AgentHub
from vdagent_backend.engine.waitgraph import WaitGraph
from vdagent_backend.events import EventBus
from vdagent_backend.ids import new_id
from vdagent_backend.tokens import TokenRegistry
from vdagent_proto import agent_pb2 as pb

log = logging.getLogger(__name__)

SEND_TO_AGENT = "send_to_agent"
RESTARTED = "backend restarted"
CANCELLED = "cancelled"


class UnknownAgentError(Exception):
    pass


class AgentUnavailableError(Exception):
    pass


class TaskNotFoundError(Exception):
    pass


class TaskFinishedError(Exception):
    pass


class ProtocolError(Exception):
    """Agent broke the frame rules of §5.3; the invocation fails with `protocol error: …`."""


class _Cancelled(Exception):
    """Raised inside a run at a checkpoint once its task has been cancelled."""


@dataclass(eq=False)
class Run:
    """In-memory state of one queued or running invocation."""

    id: str
    user_id: str
    agent: str
    task_id: str
    caller: str
    depth: int
    inbound_text: str
    created_at: str = ""
    parent: Run | None = None
    tool_call_id: str | None = None

    token: str | None = None
    rpc: Any = None  # in-flight turn channel or compaction task, cancelled on task cancel
    outbound: asyncio.Queue[pb.BackendFrame | None] = field(default_factory=asyncio.Queue)
    task: asyncio.Task[None] | None = None
    cancel_requested: bool = False
    finished: bool = False  # left the stream; child results are discarded from here on
    done: asyncio.Event = field(default_factory=asyncio.Event)

    # §5.3 protocol state for the latest assistant message
    tool_calls: dict[str, str] = field(default_factory=dict)  # tool_call id → tool name
    unresolved: set[str] = field(default_factory=set)
    called: set[str] = field(default_factory=set)
    # accepted child calls awaiting their result: tool_call id → child run (one wait-for edge each)
    children: dict[str, Run] = field(default_factory=dict)

    def check_cancel(self) -> None:
        if self.cancel_requested:
            raise _Cancelled


@dataclass(eq=False)
class Stack:
    running: Run | None = None
    queue: deque[Run] = field(default_factory=deque)


def _to_proto(row: Mapping[str, Any]) -> pb.Message:
    """A stored message as LLM history; inbound messages are rendered `[from: <sender>] <text>`."""
    role = row["role"]
    if role == "user":
        return pb.Message(role=pb.USER, content=f"[from: {row['sender']}] {row['content']}")
    if role == "assistant":
        calls = json.loads(row["tool_calls_json"]) if row["tool_calls_json"] else []
        return pb.Message(
            role=pb.ASSISTANT,
            content=row["content"],
            tool_calls=[pb.ToolCall(id=c["id"], name=c["name"], arguments_json=c["arguments_json"]) for c in calls],
        )
    return pb.Message(role=pb.TOOL, content=row["content"], tool_call_id=row["tool_call_id"] or "")


class Engine:
    def __init__(self, cfg: Config, db: AsyncEngine, bus: EventBus, tokens: TokenRegistry, hub: AgentHub) -> None:
        self.cfg = cfg
        self.db = db
        self.bus = bus
        self.tokens = tokens
        self.hub = hub
        self._stacks: dict[tuple[str, str], Stack] = {}
        self._graphs: dict[str, WaitGraph] = {}
        self._cancelled: set[str] = set()  # task ids cancelled while this process runs
        self._cancelling: dict[str, asyncio.Future[None]] = {}
        self._stopping = False

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Startup recovery, then accept agent sessions."""
        await self.recover()
        await self.hub.start(self.cfg.agent_listen, self._on_health_change)

    async def stop(self) -> None:
        """Abandon running invocations (startup recovery fails them next boot) and close the hub."""
        self._stopping = True
        tasks = [s.running.task for s in self._stacks.values() if s.running is not None and s.running.task]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.hub.close()

    async def recover(self) -> None:
        """§4.6: every queued/running invocation → failed (stack patched); every running task → failed."""
        for row in await repo.inflight_invocations(self.db):
            await self._patch_stack(row["user_id"], row["agent"], row["task_id"], row["id"], RESTARTED)
            await repo.finish_invocation(self.db, row["id"], "failed", error=RESTARTED)
        await repo.fail_running_tasks(self.db)

    # ------------------------------------------------------------------ queries for the API

    def graph(self, user_id: str) -> WaitGraph:
        graph = self._graphs.get(user_id)
        if graph is None:
            graph = self._graphs[user_id] = WaitGraph()
        return graph

    def agent_status(self, user_id: str, agent: str) -> dict[str, Any]:
        stack = self._stacks.get((user_id, agent))
        return {
            "agent": agent,
            "healthy": self.hub.is_healthy(agent),
            "busy": stack is not None and stack.running is not None,
            "queue_len": len(stack.queue) if stack is not None else 0,
        }

    def agents(self, user_id: str) -> list[dict[str, Any]]:
        out = []
        for name, spec in self.cfg.agents.items():
            status = self.agent_status(user_id, name)
            out.append(
                {
                    "name": name,
                    "description": spec.description,
                    "healthy": status["healthy"],
                    "busy": status["busy"],
                    "queue_len": status["queue_len"],
                }
            )
        return out

    def pending(self, user_id: str, agent: str) -> list[dict[str, Any]]:
        """Queued inbound messages of stack (user, agent), in FIFO order."""
        stack = self._stacks.get((user_id, agent))
        if stack is None:
            return []
        return [
            {"invocation_id": r.id, "caller": r.caller, "inbound_text": r.inbound_text, "created_at": r.created_at}
            for r in stack.queue
        ]

    # ------------------------------------------------------------------ triggers

    async def post_message(self, user_id: str, agent: str, content: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Human trigger (§4.1): new task + queued root invocation. Returns (task row, invocation row)."""
        if agent not in self.cfg.agents:
            raise UnknownAgentError(agent)
        if not self.hub.is_healthy(agent):
            raise AgentUnavailableError(agent)
        task, inv = await repo.create_task(self.db, user_id, agent, content)
        self._publish_task(task)
        self._publish_invocation(inv)
        run = Run(
            id=inv["id"],
            user_id=user_id,
            agent=agent,
            task_id=task["id"],
            caller="user",
            depth=0,
            inbound_text=content,
            created_at=inv["created_at"],
        )
        self._enqueue(run)
        return task, inv

    async def cancel_task(self, user_id: str, task_id: str) -> dict[str, Any]:
        """§4.6 cancel. Returns the task row afterwards."""
        task = await repo.get_task(self.db, task_id, user_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        in_progress = self._cancelling.get(task_id)
        if in_progress is not None:
            await asyncio.shield(in_progress)
            return await self._task_row(task_id)
        if task["status"] != "running":
            raise TaskFinishedError(task_id)

        done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._cancelling[task_id] = done
        self._cancelled.add(task_id)
        try:
            for (uid, agent), stack in self._stacks.items():
                if uid == user_id and any(r.task_id == task_id for r in stack.queue):
                    stack.queue = deque(r for r in stack.queue if r.task_id != task_id)
                    self._publish_status(uid, agent)
            running = [
                s.running
                for (uid, _), s in self._stacks.items()
                if uid == user_id and s.running is not None and s.running.task_id == task_id
            ]
            for run in running:
                run.cancel_requested = True
                if run.rpc is not None:
                    run.rpc.cancel()
            await asyncio.gather(*(r.done.wait() for r in running))
            for row in await repo.cancel_task_invocations(self.db, task_id):
                self._publish_invocation(row)
            row = await repo.finish_task(self.db, task_id, "cancelled")
            if row is None:  # finished on its own while we were cancelling
                current = await self._task_row(task_id)
                if current["status"] != "cancelled":
                    raise TaskFinishedError(task_id)
                return current
            self._publish_task(row)
            return row
        finally:
            del self._cancelling[task_id]
            done.set_result(None)

    async def _task_row(self, task_id: str) -> dict[str, Any]:
        row = await repo.get_task(self.db, task_id)
        assert row is not None
        return row

    # ------------------------------------------------------------------ scheduling

    def _stack(self, user_id: str, agent: str) -> Stack:
        stack = self._stacks.get((user_id, agent))
        if stack is None:
            stack = self._stacks[(user_id, agent)] = Stack()
        return stack

    def _enqueue(self, run: Run) -> None:
        if run.task_id in self._cancelled:
            return  # the cancel in progress marks its row cancelled
        self._stack(run.user_id, run.agent).queue.append(run)
        self._pump(run.user_id, run.agent)

    def _pump(self, user_id: str, agent: str) -> None:
        stack = self._stack(user_id, agent)
        if self._stopping:
            return
        while stack.running is None and stack.queue:
            run = stack.queue.popleft()
            if run.task_id in self._cancelled:
                continue
            stack.running = run
            run.task = asyncio.create_task(self._run(run), name=f"invocation {run.id}")
        self._publish_status(user_id, agent)

    def _release(self, run: Run) -> None:
        stack = self._stack(run.user_id, run.agent)
        if stack.running is run:
            stack.running = None
        self._pump(run.user_id, run.agent)

    # ------------------------------------------------------------------ one invocation

    async def _run(self, run: Run) -> None:
        try:
            final, reason = await self._drive(run)
            self._settle(run)
            if run.cancel_requested:
                await self._patch_stack(run.user_id, run.agent, run.task_id, run.id, CANCELLED)
                row = await repo.finish_invocation(self.db, run.id, "cancelled")
                if row is not None:
                    self._publish_invocation(row)
            elif reason is not None:
                await self._conclude_failed(run, reason)
            else:
                assert final is not None
                await self._conclude_completed(run, final)
        except asyncio.CancelledError:
            raise  # engine shutdown: startup recovery takes care of the rows
        except Exception:
            log.exception("invocation %s: bookkeeping failed", run.id)
        finally:
            self._settle(run)
            self._release(run)
            run.done.set()

    async def _drive(self, run: Run) -> tuple[str | None, str | None]:
        """Prepare and run the turn. Returns (final content, None) or (None, failure reason)."""
        try:
            return await self._execute(run), None
        except _Cancelled:
            return None, CANCELLED
        except asyncio.CancelledError:
            if run.cancel_requested:  # local cancel of the in-flight turn or compaction
                return None, CANCELLED
            raise
        except ProtocolError as e:
            return None, f"protocol error: {e}"
        except AgentError as e:
            return None, f"{e.code}: {e.detail}"
        except Exception as e:
            log.exception("invocation %s crashed", run.id)
            return None, f"internal error: {e}"
        finally:
            run.outbound.put_nowait(None)
            if run.rpc is not None and not run.rpc.done():
                run.rpc.cancel()
            run.rpc = None

    async def _execute(self, run: Run) -> str:
        row = await repo.mark_invocation_running(self.db, run.id)
        self._publish_invocation(row)
        run.check_cancel()

        await self._compact(run)
        run.check_cancel()

        await self._append(run, role="user", sender=run.caller, content=run.inbound_text)
        run.token = self.tokens.issue(run.user_id, run.agent, run.id)
        summary = await repo.get_summary(self.db, run.user_id, run.agent)
        history = await repo.stack_history(self.db, run.user_id, run.agent)
        run.check_cancel()

        start = pb.InvokeStart(
            invocation_id=run.id,
            task_id=run.task_id,
            user_id=run.user_id,
            summary=summary or "",
            history=[_to_proto(m) for m in history],
            peers=[pb.Peer(name=n, description=s.description) for n, s in self.cfg.agents.items() if n != run.agent],
            mcp_url=self.cfg.mcp_public_url,
            mcp_token=run.token,
            max_steps=self.cfg.max_steps,
        )
        run.outbound.put_nowait(pb.BackendFrame(start=start))
        turn = self.hub.invoke(run.agent, run.outbound)
        run.rpc = turn
        while True:
            frame = await turn.read()
            kind = frame.WhichOneof("kind")
            if kind == "message":
                await self._on_message(run, frame.message)
            elif kind == "call":
                await self._on_call(run, frame.call)
            elif kind == "final":
                return self._on_final(run, frame.final.content)
            else:
                raise ProtocolError("empty agent frame")
            run.check_cancel()

    async def _on_message(self, run: Run, msg: pb.Message) -> None:
        if msg.role == pb.ASSISTANT:
            if run.unresolved:
                raise ProtocolError(f"assistant message while tool calls are unresolved: {sorted(run.unresolved)}")
            ids = [tc.id for tc in msg.tool_calls]
            if any(not i for i in ids) or len(set(ids)) != len(ids):
                raise ProtocolError("assistant tool_calls need unique, non-empty ids")
            run.tool_calls = {tc.id: tc.name for tc in msg.tool_calls}
            run.unresolved = set(ids)
            run.called = set()
            calls = [{"id": tc.id, "name": tc.name, "arguments_json": tc.arguments_json} for tc in msg.tool_calls]
            await self._append(run, role="assistant", content=msg.content, tool_calls=calls or None)
        elif msg.role == pb.TOOL:
            if msg.tool_call_id not in run.unresolved:
                raise ProtocolError(f"tool message for unknown or already resolved tool call '{msg.tool_call_id}'")
            run.unresolved.discard(msg.tool_call_id)
            await self._append(run, role="tool", content=msg.content, tool_call_id=msg.tool_call_id)
        else:
            raise ProtocolError(f"unexpected message role {pb.Role.Name(msg.role)}")

    async def _on_call(self, run: Run, req: pb.AgentCallRequest) -> None:
        tcid = req.tool_call_id
        name = run.tool_calls.get(tcid)
        if name is None:
            raise ProtocolError(f"call for unknown tool call '{tcid}'")
        if name != SEND_TO_AGENT:
            raise ProtocolError(f"call for tool call '{tcid}' which is not {SEND_TO_AGENT}")
        if tcid not in run.unresolved:
            raise ProtocolError(f"call for already resolved tool call '{tcid}'")
        if tcid in run.called:
            raise ProtocolError(f"duplicate call for tool call '{tcid}'")
        run.called.add(tcid)

        target = req.target
        fields: dict[str, Any] = {
            "task_id": run.task_id,
            "user_id": run.user_id,
            "agent": target,
            "caller": run.agent,
            "parent_id": run.id,
            "tool_call_id": tcid,
            "depth": run.depth + 1,
            "inbound_text": req.message,
        }
        error = self._call_error(run, target)
        if error is not None:
            row = await repo.insert_invocation(self.db, id=new_id("inv"), status="rejected", error=error, **fields)
            self._publish_invocation(row)
            self._send_result(run, tcid, ok=False, content=error)
            return

        # Accept: the wait-for edge is added in the same synchronous step as the checks (I3).
        child = Run(
            id=new_id("inv"),
            user_id=run.user_id,
            agent=target,
            task_id=run.task_id,
            caller=run.agent,
            depth=run.depth + 1,
            inbound_text=req.message,
            parent=run,
            tool_call_id=tcid,
        )
        self.graph(run.user_id).add(run.agent, target)
        run.children[tcid] = child
        try:
            row = await repo.insert_invocation(self.db, id=child.id, status="queued", **fields)
        except BaseException:
            self._drop_call(run, tcid)
            raise
        child.created_at = row["created_at"]
        self._publish_invocation(row)
        self._enqueue(child)

    def _call_error(self, run: Run, target: str) -> str | None:
        """§4.4 checks in table order; the tool-error content, or None to accept."""
        if target not in self.cfg.agents:
            return f"error: unknown agent '{target}'"
        if target == run.agent:
            return "error: you cannot call yourself"
        if run.depth + 1 > self.cfg.max_depth:
            return "error: call depth limit reached; answer your caller with what you have"
        if not self.hub.is_healthy(target):
            return f"error: {target} is unavailable"
        if self.graph(run.user_id).has_path(target, run.agent):
            return f"error: calling {target} would deadlock (it is waiting on you); answer with what you have"
        return None

    def _on_final(self, run: Run, content: str) -> str:
        if run.unresolved:
            raise ProtocolError(f"final while tool calls are unresolved: {sorted(run.unresolved)}")
        return content

    # ------------------------------------------------------------------ child results

    def _send_result(self, run: Run, tool_call_id: str, *, ok: bool, content: str) -> None:
        if run.finished or run.task_id in self._cancelled:
            return  # parent stream gone / cancelled task: result discarded
        run.outbound.put_nowait(
            pb.BackendFrame(call_result=pb.AgentCallResult(tool_call_id=tool_call_id, ok=ok, content=content))
        )

    def _drop_call(self, run: Run, tool_call_id: str) -> Run | None:
        child = run.children.pop(tool_call_id, None)
        if child is not None:
            self.graph(run.user_id).remove(run.agent, child.agent)
        return child

    def _deliver(self, child: Run, *, ok: bool, content: str) -> None:
        parent = child.parent
        if parent is None or child.tool_call_id is None:
            return
        if parent.children.get(child.tool_call_id) is not child:
            return  # parent already ended; its edges are gone
        self._drop_call(parent, child.tool_call_id)
        self._send_result(parent, child.tool_call_id, ok=ok, content=content)

    def _settle(self, run: Run) -> None:
        """The run left its stream: no more results, token revoked, outgoing edges removed."""
        run.finished = True
        self.tokens.revoke(run.token)
        run.token = None
        for tcid in list(run.children):
            self._drop_call(run, tcid)

    # ------------------------------------------------------------------ outcomes

    async def _conclude_completed(self, run: Run, final: str) -> None:
        row = await repo.finish_invocation(self.db, run.id, "completed", result_text=final)
        if row is not None:
            self._publish_invocation(row)
        if run.parent is not None:
            self._deliver(run, ok=True, content=final)
        else:
            await self._finish_task(run.task_id, "completed")

    async def _conclude_failed(self, run: Run, reason: str) -> None:
        log.warning("invocation %s (%s) failed: %s", run.id, run.agent, reason)
        await self._patch_stack(run.user_id, run.agent, run.task_id, run.id, reason)
        row = await repo.finish_invocation(self.db, run.id, "failed", error=reason)
        if row is not None:
            self._publish_invocation(row)
        if run.parent is not None:
            self._deliver(run, ok=False, content=f"error: {run.agent} failed: {reason}")
        else:
            await self._finish_task(run.task_id, "failed")

    async def _finish_task(self, task_id: str, status: str) -> None:
        if task_id in self._cancelled:
            return  # the cancel owns the task's final status
        row = await repo.finish_task(self.db, task_id, status)
        if row is not None:
            self._publish_task(row)

    async def _patch_stack(self, user_id: str, agent: str, task_id: str, invocation_id: str, reason: str) -> None:
        """§4.6 steps 1–2 for an invocation that wrote to its stack: synthesise the missing tool
        results (I2), then `[turn failed: <reason>]`. No-op for invocations that never started."""
        msgs = await repo.invocation_messages(self.db, invocation_id)
        if not msgs:
            return
        answered = {m["tool_call_id"] for m in msgs if m["role"] == "tool"}
        missing = [
            call["id"]
            for m in msgs
            if m["role"] == "assistant" and m["tool_calls_json"]
            for call in json.loads(m["tool_calls_json"])
            if call["id"] not in answered
        ]
        ids = dict(user_id=user_id, agent=agent, task_id=task_id, invocation_id=invocation_id)
        for tool_call_id in dict.fromkeys(missing):
            await self._append_message(
                **ids, role="tool", content=f"error: turn aborted ({reason})", tool_call_id=tool_call_id
            )
        await self._append_message(**ids, role="assistant", content=f"[turn failed: {reason}]")

    # ------------------------------------------------------------------ compaction

    async def _compact(self, run: Run) -> None:
        """§4.5: fold finished, other-task, uncompacted messages into the stack summary."""
        rows = await repo.compaction_candidates(self.db, run.user_id, run.agent, run.task_id)
        if not rows:
            return
        previous = await repo.get_summary(self.db, run.user_id, run.agent) or ""
        request = pb.CompactRequest(previous_summary=previous, messages=[_to_proto(r) for r in rows])
        compaction = asyncio.ensure_future(self.hub.compact(run.agent, request))
        run.rpc = compaction
        try:
            resp = await compaction
        except asyncio.CancelledError:
            raise
        except Exception as e:
            reason = f"{e.code}: {e.detail}" if isinstance(e, AgentError) else repr(e)
            log.warning("compaction of stack (%s, %s) failed, continuing: %s", run.user_id, run.agent, reason)
            return
        finally:
            run.rpc = None
        await repo.apply_compaction(self.db, run.user_id, run.agent, resp.summary, [r["id"] for r in rows])

    # ------------------------------------------------------------------ persistence + events

    async def _append(self, run: Run, **fields: Any) -> None:
        await self._append_message(
            user_id=run.user_id, agent=run.agent, task_id=run.task_id, invocation_id=run.id, **fields
        )

    async def _append_message(self, **fields: Any) -> None:
        row = await repo.append_message(self.db, **fields)
        self.bus.publish(row["user_id"], "message.appended", {"agent": row["agent"], "message": repo.message_dto(row)})

    def _publish_invocation(self, row: Mapping[str, Any]) -> None:
        self.bus.publish(row["user_id"], "invocation.updated", {"invocation": repo.invocation_dto(row)})

    def _publish_task(self, row: Mapping[str, Any]) -> None:
        self.bus.publish(row["user_id"], "task.updated", {"task": repo.task_dto(row)})

    def _publish_status(self, user_id: str, agent: str) -> None:
        self.bus.publish(user_id, "agent.status", self.agent_status(user_id, agent))

    def _on_health_change(self, agent: str, _healthy: bool) -> None:
        for user_id in self.bus.user_ids():
            self._publish_status(user_id, agent)
