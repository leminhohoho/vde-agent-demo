"""Invocation engine (§4, §13 "Backend engine") against fake in-process gRPC agents."""

from __future__ import annotations

import asyncio
import json
import sqlite3

import grpc
import pytest

from conftest import ALICE, Harness, Session, wait_for
from vdagent_backend.db import repo
from vdagent_backend.engine import Engine, TaskFinishedError
from vdagent_proto import agent_pb2 as pb


def _roles(stack: list[dict]) -> list[tuple[str, str]]:
    return [(m["role"], m["content"]) for m in stack]


def _assert_i2(stack: list[dict]) -> None:
    """Every assistant tool call has exactly one tool result before the next assistant message."""
    open_calls: set[str] = set()
    for m in stack:
        if m["role"] == "assistant":
            assert not open_calls, f"unanswered tool calls {open_calls} before {m['content']!r}"
            open_calls = {c["id"] for c in json.loads(m["tool_calls_json"] or "[]")}
        elif m["role"] == "tool":
            assert m["tool_call_id"] in open_calls, m
            open_calls.discard(m["tool_call_id"])
    assert not open_calls


# --------------------------------------------------------------------------- happy path


async def test_delegation_round_trip_builds_both_stacks(harness: Harness) -> None:
    async def orchestrator(s: Session) -> None:
        res = await s.ask("data", "revenue by region")
        assert res.ok
        await s.final(f"answer uses {res.content}")

    async def data(s: Session) -> None:
        await s.final("dataset ds_000000000042")

    harness.on("orchestrator", orchestrator)
    harness.on("data", data)
    task_id = await harness.post("orchestrator", "compare regions")
    await harness.wait_task(task_id, "completed")

    orch = await harness.stack("orchestrator")
    assert [(m["role"], m["sender"]) for m in orch] == [("user", "user"), ("assistant", None), ("tool", None), ("assistant", None)]
    assert orch[2]["content"] == "dataset ds_000000000042"
    assert orch[3]["content"] == "answer uses dataset ds_000000000042"
    data_stack = await harness.stack("data")
    assert (data_stack[0]["role"], data_stack[0]["sender"], data_stack[0]["content"]) == ("user", "orchestrator", "revenue by region")

    start = harness.agents["data"].starts[0]
    assert start.history[-1].content == "[from: orchestrator] revenue by region"
    assert sorted(p.name for p in start.peers) == ["compare", "insight", "orchestrator", "report"]
    assert start.mcp_url == harness.cfg.mcp_public_url and start.mcp_token
    assert harness.tokens.resolve(start.mcp_token) is None  # revoked once the turn ended

    invs = {i["agent"]: i for i in await harness.invocations(task_id)}
    assert invs["data"]["parent_id"] == invs["orchestrator"]["id"]
    assert invs["data"]["depth"] == 1 and invs["data"]["result_text"] == "dataset ds_000000000042"


# --------------------------------------------------------------------------- scheduling


async def test_human_message_queues_behind_running_agent_call(harness: Harness) -> None:
    gate = asyncio.Event()

    async def orchestrator(s: Session) -> None:
        await s.ask("data", "first")
        await s.final("ok")

    async def data(s: Session) -> None:
        if "first" in s.inbound:
            await gate.wait()
        await s.final("done")

    harness.on("orchestrator", orchestrator)
    harness.on("data", data)
    t1 = await harness.post("orchestrator", "go")
    await wait_for(lambda: _started(harness, "data", 1))
    t2 = await harness.post("data", "second")
    t3 = await harness.post("data", "third")

    pending = harness.engine.pending(ALICE, "data")
    assert [(p["caller"], p["inbound_text"]) for p in pending] == [("user", "second"), ("user", "third")]
    assert harness.engine.agent_status(ALICE, "data") == {"agent": "data", "healthy": True, "busy": True, "queue_len": 2}
    await asyncio.sleep(0.1)
    assert len(harness.agents["data"].starts) == 1  # still blocked behind the running call

    gate.set()
    await harness.wait_task(t1, "completed")
    await harness.wait_task(t2, "completed")
    await harness.wait_task(t3, "completed")
    inbound = [(m["sender"], m["content"]) for m in await harness.stack("data") if m["role"] == "user"]
    assert inbound == [("orchestrator", "first"), ("user", "second"), ("user", "third")]


async def _started(h: Harness, agent: str, n: int) -> bool:
    return len(h.agents[agent].starts) >= n


async def test_parallel_calls_to_different_targets_run_concurrently(harness: Harness) -> None:
    both_running = {"data": asyncio.Event(), "compare": asyncio.Event()}

    async def orchestrator(s: Session) -> None:
        calls = [s.send_to("data", "d"), s.send_to("compare", "c")]
        await s.assistant(calls=calls)
        for tcid, _, args in calls:
            await s.call(tcid, args["agent"], args["message"])
        for _ in calls:
            res = await s.result()
            await s.tool(res.tool_call_id, res.content)
        await s.final("both")

    def worker(me: str, other: str):
        async def run(s: Session) -> None:
            both_running[me].set()
            await asyncio.wait_for(both_running[other].wait(), 2)  # deadlocks if serialised
            await s.final(f"{me} ok")

        return run

    harness.on("orchestrator", orchestrator)
    harness.on("data", worker("data", "compare"))
    harness.on("compare", worker("compare", "data"))
    task_id = await harness.post("orchestrator", "go")
    await harness.wait_task(task_id, "completed")
    tools = sorted(m["content"] for m in await harness.stack("orchestrator") if m["role"] == "tool")
    assert tools == ["compare ok", "data ok"]


# --------------------------------------------------------------------------- call checks


async def _rejection(harness: Harness, target: str) -> tuple[pb.AgentCallResult, dict]:
    """Orchestrator → data; data then calls `target`. Returns data's call result and its row."""
    seen: list[pb.AgentCallResult] = []

    async def orchestrator(s: Session) -> None:
        await s.ask("data", "work")
        await s.final("ok")

    async def data(s: Session) -> None:
        seen.append(await s.ask(target, "help"))
        await s.final("data done")

    harness.on("orchestrator", orchestrator)
    harness.on("data", data)
    task_id = await harness.post("orchestrator", "go")
    await harness.wait_task(task_id, "completed")
    rows = [i for i in await harness.invocations(task_id) if i["caller"] == "data"]
    assert len(rows) == 1
    return seen[0], rows[0]


@pytest.mark.parametrize(
    ("target", "error"),
    [
        ("nobody", "error: unknown agent 'nobody'"),
        ("data", "error: you cannot call yourself"),
        ("orchestrator", "error: calling orchestrator would deadlock (it is waiting on you); answer with what you have"),
    ],
    ids=["unknown", "self", "ancestor-cycle"],
)
async def test_call_rejections(harness: Harness, target: str, error: str) -> None:
    res, row = await _rejection(harness, target)
    assert (res.ok, res.content) == (False, error)
    assert (row["status"], row["error"], row["agent"], row["depth"]) == ("rejected", error, target, 2)
    assert harness.agents["orchestrator"].starts.__len__() == 1


async def test_call_to_unhealthy_agent_is_rejected(harness: Harness) -> None:
    await harness.agents["compare"].set_healthy(False)
    await harness.clients.check_all()
    res, row = await _rejection(harness, "compare")
    assert (res.ok, res.content, row["status"]) == (False, "error: compare is unavailable", "rejected")
    assert harness.agents["compare"].starts == []


@pytest.mark.parametrize("cfg_overrides", [{"max_depth": 1}])
async def test_call_depth_limit(harness: Harness) -> None:
    res, row = await _rejection(harness, "compare")
    assert res.content == "error: call depth limit reached; answer your caller with what you have"
    assert row["status"] == "rejected"


async def test_cross_task_wait_for_cycle_is_rejected(harness: Harness) -> None:
    """§4.4 example: task 1 Orchestrator → Data → Compare while task 2 runs Compare → Data."""
    compare_gate = asyncio.Event()
    compare_results: list[pb.AgentCallResult] = []

    async def orchestrator(s: Session) -> None:
        await s.ask("data", "task1 data")
        await s.final("t1 done")

    async def data(s: Session) -> None:
        res = await s.ask("compare", "task1 compare")
        await s.final(f"data got {res.content}")

    async def compare(s: Session) -> None:
        if "task2" in s.inbound:
            await compare_gate.wait()
            compare_results.append(await s.ask("data", "task2 needs data"))
        await s.final("compare done")

    harness.on("orchestrator", orchestrator)
    harness.on("data", data)
    harness.on("compare", compare)

    t2 = await harness.post("compare", "task2 start")  # compare stack now held by task 2
    await wait_for(lambda: _started(harness, "compare", 1))
    t1 = await harness.post("orchestrator", "task1 start")

    async def data_waits_on_compare() -> bool:
        return any(i["caller"] == "data" and i["status"] == "queued" for i in await harness.invocations(t1))

    await wait_for(data_waits_on_compare)
    compare_gate.set()

    await harness.wait_task(t2, "completed")
    await harness.wait_task(t1, "completed")
    assert [(r.ok, r.content) for r in compare_results] == [
        (False, "error: calling data would deadlock (it is waiting on you); answer with what you have")
    ]
    assert [i["status"] for i in await harness.invocations(t2) if i["caller"] == "compare"] == ["rejected"]


# --------------------------------------------------------------------------- failures


async def test_protocol_violation_fails_invocation_and_patches_stack(harness: Harness) -> None:
    parent_results: list[pb.AgentCallResult] = []

    async def orchestrator(s: Session) -> None:
        parent_results.append(await s.ask("data", "work"))
        await s.final("orchestrator survived")

    async def data(s: Session) -> None:
        await s.assistant(calls=[("q1", "run_query", {"sql": "SELECT 1"})])
        await s.final("too early", with_message=False)  # q1 is unresolved → §5.3 violation

    harness.on("orchestrator", orchestrator)
    harness.on("data", data)
    task_id = await harness.post("orchestrator", "go")
    await harness.wait_task(task_id, "completed")

    row = next(i for i in await harness.invocations(task_id) if i["agent"] == "data")
    assert row["status"] == "failed" and row["error"].startswith("protocol error: final while tool calls are unresolved")
    stack = await harness.stack("data")
    reason = row["error"]
    assert _roles(stack)[1:] == [
        ("assistant", ""),
        ("tool", f"error: turn aborted ({reason})"),
        ("assistant", f"[turn failed: {reason}]"),
    ]
    _assert_i2(stack)
    assert [(r.ok, r.content) for r in parent_results] == [(False, f"error: data failed: {reason}")]


async def test_child_abort_gives_parent_error_result(harness: Harness) -> None:
    parent_results: list[pb.AgentCallResult] = []

    async def orchestrator(s: Session) -> None:
        parent_results.append(await s.ask("data", "work"))
        await s.final("handled")

    async def data(s: Session) -> None:
        await s.context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "LLM timed out")

    harness.on("orchestrator", orchestrator)
    harness.on("data", data)
    task_id = await harness.post("orchestrator", "go")
    await harness.wait_task(task_id, "completed")
    assert [(r.ok, r.content) for r in parent_results] == [
        (False, "error: data failed: DEADLINE_EXCEEDED: LLM timed out")
    ]
    assert _roles(await harness.stack("data"))[-1] == ("assistant", "[turn failed: DEADLINE_EXCEEDED: LLM timed out]")


async def test_root_failure_fails_task(harness: Harness) -> None:
    async def orchestrator(s: Session) -> None:
        await s.call("nope", "data", "no such tool call")

    harness.on("orchestrator", orchestrator)
    task_id = await harness.post("orchestrator", "go")
    await harness.wait_task(task_id, "failed")
    (row,) = await harness.invocations(task_id)
    assert row["status"] == "failed" and row["error"] == "protocol error: call for unknown tool call 'nope'"


# --------------------------------------------------------------------------- cancel


async def test_cancel_removes_queued_cancels_running_and_patches_stacks(harness: Harness) -> None:
    async def orchestrator(s: Session) -> None:
        calls = [s.send_to("data", "one"), s.send_to("data", "two")]  # same target → second is queued
        await s.assistant(calls=calls)
        for tcid, _, args in calls:
            await s.call(tcid, "data", args["message"])
        await asyncio.Event().wait()

    async def data(s: Session) -> None:
        if s.inbound.endswith("after cancel"):
            await s.final("fresh")
            return
        await s.assistant(calls=[("q1", "run_query", {"sql": "SELECT 1"})])
        await asyncio.Event().wait()

    harness.on("orchestrator", orchestrator)
    harness.on("data", data)
    task_id = await harness.post("orchestrator", "go")

    async def both_accepted() -> bool:
        stack = await harness.stack("data")
        return len(harness.engine.pending(ALICE, "data")) == 1 and any(m["role"] == "assistant" for m in stack)

    await wait_for(both_accepted)
    task = await harness.engine.cancel_task(ALICE, task_id)
    assert task["status"] == "cancelled"

    invs = await harness.invocations(task_id)
    assert {i["status"] for i in invs} == {"cancelled"}
    queued = next(i for i in invs if i["inbound_text"] == "two")
    assert queued["started_at"] is None
    assert harness.engine.pending(ALICE, "data") == []
    assert len(harness.agents["data"].starts) == 1

    for agent in ("orchestrator", "data"):
        stack = await harness.stack(agent)
        _assert_i2(stack)
        assert _roles(stack)[-1] == ("assistant", "[turn failed: cancelled]")
    assert [m["content"] for m in await harness.stack("orchestrator") if m["role"] == "tool"] == [
        "error: turn aborted (cancelled)"
    ] * 2

    # Locks were released: both stacks accept new work.
    t2 = await harness.post("data", "after cancel")
    await harness.wait_task(t2, "completed")
    with pytest.raises(TaskFinishedError):
        await harness.engine.cancel_task(ALICE, task_id)


# --------------------------------------------------------------------------- restart


async def test_startup_recovery_fails_inflight_work_and_patches_stacks(harness: Harness) -> None:
    with sqlite3.connect(harness.cfg.backend_db) as conn:
        conn.execute("INSERT INTO tasks (id, user_id, root_agent, status) VALUES ('t_1', ?, 'orchestrator', 'running')", (ALICE,))
        conn.executemany(
            "INSERT INTO invocations (id, task_id, user_id, agent, caller, parent_id, depth, inbound_text, status)"
            " VALUES (?, 't_1', ?, ?, ?, ?, ?, ?, ?)",
            [
                ("inv_root", ALICE, "orchestrator", "user", None, 0, "go", "running"),
                ("inv_child", ALICE, "data", "orchestrator", "inv_root", 1, "work", "queued"),
            ],
        )
        conn.executemany(
            "INSERT INTO messages (user_id, agent, seq, task_id, invocation_id, role, sender, content, tool_calls_json)"
            " VALUES (?, 'orchestrator', ?, 't_1', 'inv_root', ?, ?, ?, ?)",
            [
                (ALICE, 1, "user", "user", "go", None),
                (ALICE, 2, "assistant", None, "", json.dumps([{"id": "c1", "name": "send_to_agent", "arguments_json": "{}"}])),
            ],
        )
    conn.close()

    await harness.engine.recover()

    assert (await repo.get_task(harness.db, "t_1"))["status"] == "failed"
    invs = {i["id"]: i for i in await harness.invocations("t_1")}
    assert {(i["status"], i["error"]) for i in invs.values()} == {("failed", "backend restarted")}
    stack = await harness.stack("orchestrator")
    _assert_i2(stack)
    assert _roles(stack)[2:] == [("tool", "error: turn aborted (backend restarted)"), ("assistant", "[turn failed: backend restarted]")]
    assert await harness.stack("data") == []  # the queued child never wrote to its stack


# --------------------------------------------------------------------------- compaction


async def test_compaction_selects_only_finished_other_task_uncompacted_messages(harness: Harness) -> None:
    gate = asyncio.Event()

    async def orchestrator(s: Session) -> None:
        await s.ask("data", "task A data")
        await gate.wait()
        await s.final("A done")

    harness.on("orchestrator", orchestrator)
    t_c = await harness.post("data", "task C")  # finished before the others
    await harness.wait_task(t_c, "completed")
    t_a = await harness.post("orchestrator", "task A")  # stays running with messages on data's stack

    async def a_data_done() -> bool:
        return any(i["agent"] == "data" and i["status"] == "completed" for i in await harness.invocations(t_a))

    await wait_for(a_data_done)
    t_b = await harness.post("data", "task B")
    await harness.wait_task(t_b, "completed")

    fake = harness.agents["data"]
    assert len(fake.compacts) == 1
    compact = fake.compacts[0]
    assert compact.previous_summary == ""
    assert [m.content for m in compact.messages] == ["[from: user] task C", "done: [from: user] task C"]

    start_b = fake.starts[-1]
    assert start_b.summary == "SUMMARY#1"
    assert [m.content for m in start_b.history] == [
        "[from: orchestrator] task A data",
        "done: [from: orchestrator] task A data",
        "[from: user] task B",
    ]
    by_task = {}
    for m in await harness.stack("data"):
        by_task.setdefault(m["task_id"], set()).add(bool(m["compacted"]))
    assert by_task == {t_c: {True}, t_a: {False}, t_b: {False}}
    assert await repo.get_summary(harness.db, ALICE, "data") == "SUMMARY#1"

    gate.set()
    await harness.wait_task(t_a, "completed")


async def test_compaction_failure_is_not_fatal(harness: Harness) -> None:
    harness.agents["data"].compact_fails = True
    t1 = await harness.post("data", "first")
    await harness.wait_task(t1, "completed")
    t2 = await harness.post("data", "second")
    await harness.wait_task(t2, "completed")

    start = harness.agents["data"].starts[-1]
    assert start.summary == ""
    assert [m.content for m in start.history] == ["[from: user] first", "done: [from: user] first", "[from: user] second"]
    assert not any(m["compacted"] for m in await harness.stack("data"))


async def test_events_are_published_for_the_owning_user(harness: Harness) -> None:
    alice, bob = harness.bus.subscribe(ALICE), harness.bus.subscribe("u_000000000002")
    task_id = await harness.post("data", "hello")
    await harness.wait_task(task_id, "completed")

    names = []
    while not alice.queue.empty():
        ev = await alice.next()
        names.append(ev.event)
    assert {"task.updated", "invocation.updated", "message.appended", "agent.status"} <= set(names)
    assert bob.queue.empty()


async def test_engine_restart_recovers_via_start(harness: Harness) -> None:
    """A second engine on the same DB (a BE restart) fails what the first left running."""
    gate = asyncio.Event()

    async def data(s: Session) -> None:
        await gate.wait()

    harness.on("data", data)
    task_id = await harness.post("data", "hang")
    await wait_for(lambda: _started(harness, "data", 1))
    await harness.engine.stop()

    engine2 = Engine(harness.cfg, harness.db, harness.bus, harness.tokens, harness.clients)
    await engine2.recover()
    row = await repo.get_task(harness.db, task_id)
    assert row["status"] == "failed"
    gate.set()
