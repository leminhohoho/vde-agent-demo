"""Agent hub (connect-direction spec §3, §4.2): a real `grpc.aio` hub with test-side agent sessions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import grpc
import pytest

from hub_client import HubClient
from vdagent_backend.config import AgentSpec
from vdagent_backend.engine.hub import AgentError, AgentHub, TurnChannel
from vdagent_proto import agent_pb2 as pb

AGENTS = {n: AgentSpec(n, f"{n} agent") for n in ("data", "compare", "report")}
WAIT_S = 5.0


@dataclass
class Hub:
    hub: AgentHub
    port: int
    changes: list[tuple[str, bool]] = field(default_factory=list)
    clients: list[HubClient] = field(default_factory=list)

    def client(self) -> HubClient:
        c = HubClient(self.port)
        self.clients.append(c)
        return c

    async def connect(self, agent: str = "data") -> HubClient:
        c = self.client()
        welcome = await c.hello(agent)
        assert welcome.WhichOneof("kind") == "welcome"
        return c


@pytest.fixture
async def hub() -> AsyncIterator[Hub]:
    h = AgentHub(AGENTS, compact_timeout_s=0.3)
    state = Hub(h, 0)
    state.port = await h.start("127.0.0.1:0", lambda agent, healthy: state.changes.append((agent, healthy)))
    yield state
    await h.close()
    for c in state.clients:
        await c.close()


async def eventually(probe, timeout: float = WAIT_S) -> None:  # noqa: ANN001
    async with asyncio.timeout(timeout):
        while not probe():
            await asyncio.sleep(0.01)


def invoke(hub: AgentHub, agent: str, invocation_id: str, user_id: str = "u_1") -> tuple[TurnChannel, asyncio.Queue]:
    outbound: asyncio.Queue[pb.BackendFrame | None] = asyncio.Queue()
    outbound.put_nowait(pb.BackendFrame(start=pb.InvokeStart(invocation_id=invocation_id, user_id=user_id)))
    return hub.invoke(agent, outbound), outbound


def assistant(text: str) -> pb.AgentFrame:
    return pb.AgentFrame(message=pb.Message(role=pb.ASSISTANT, content=text))


async def read(turn: TurnChannel) -> pb.AgentFrame:
    return await asyncio.wait_for(turn.read(), WAIT_S)


# --------------------------------------------------------------------------- sessions


@pytest.mark.parametrize(
    ("first", "reason"),
    [
        (pb.AgentUplink(hello=pb.Hello(agent="nobody")), "unknown agent 'nobody'"),
        (pb.AgentUplink(ref="inv_1", frame=assistant("hi")), "the first message must be hello"),
    ],
    ids=["unknown-name", "not-hello"],
)
async def test_session_is_refused_unauthenticated(hub: Hub, first: pb.AgentUplink, reason: str) -> None:
    c = hub.client()
    await c.call.write(first)
    code, details = await c.status()
    assert code == grpc.StatusCode.UNAUTHENTICATED
    assert reason in details
    assert not any(hub.hub.is_healthy(a) for a in AGENTS)
    assert hub.changes == []


async def test_connecting_marks_healthy_and_disconnecting_unhealthy(hub: Hub) -> None:
    assert not hub.hub.is_healthy("data")
    c = await hub.connect("data")
    assert hub.hub.is_healthy("data") and not hub.hub.is_healthy("compare")
    assert hub.changes == [("data", True)]

    await c.close()
    await eventually(lambda: not hub.hub.is_healthy("data"))
    assert hub.changes == [("data", True), ("data", False)]


async def test_new_session_replaces_the_old_one_and_fails_its_turns(hub: Hub) -> None:
    old = await hub.connect("data")
    turn, _ = invoke(hub.hub, "data", "inv_old")
    assert (await old.recv()).ref == "inv_old"

    new = await hub.connect("data")
    with pytest.raises(AgentError) as err:
        await read(turn)
    assert (err.value.code, err.value.detail) == ("UNAVAILABLE", "agent disconnected")
    code, details = await old.status()
    assert code == grpc.StatusCode.ABORTED and "replaced by a new connection" in details
    assert hub.hub.is_healthy("data")
    assert hub.changes == [("data", True)]  # no unhealthy event in between

    turn2, _ = invoke(hub.hub, "data", "inv_new")
    assert (await new.recv()).ref == "inv_new"
    await new.frame("inv_new", assistant("served by the new session"))
    assert (await read(turn2)).message.content == "served by the new session"


# --------------------------------------------------------------------------- turns


async def test_turns_are_routed_by_ref_with_interleaved_frames(hub: Hub) -> None:
    c = await hub.connect("data")
    alice, alice_out = invoke(hub.hub, "data", "inv_a", user_id="u_alice")
    bob, _ = invoke(hub.hub, "data", "inv_b", user_id="u_bob")
    starts = {m.ref: m.frame.start.user_id for m in [await c.recv(), await c.recv()]}
    assert starts == {"inv_a": "u_alice", "inv_b": "u_bob"}

    await c.frame("inv_b", assistant("bob 1"))
    await c.frame("inv_a", pb.AgentFrame(call=pb.AgentCallRequest(tool_call_id="tc", target="compare", message="m")))
    await c.frame("inv_b", pb.AgentFrame(final=pb.Final(content="bob done")))
    assert (await read(alice)).call.tool_call_id == "tc"
    assert (await read(bob)).message.content == "bob 1"
    assert (await read(bob)).final.content == "bob done"
    assert bob.done() and not alice.done()

    alice_out.put_nowait(pb.BackendFrame(call_result=pb.AgentCallResult(tool_call_id="tc", ok=True, content="r")))
    down = await c.recv()
    assert (down.ref, down.frame.call_result.content) == ("inv_a", "r")
    await c.frame("inv_a", pb.AgentFrame(final=pb.Final(content="alice done")))
    assert (await read(alice)).final.content == "alice done"


async def test_turn_for_a_disconnected_agent_fails_unavailable(hub: Hub) -> None:
    turn, _ = invoke(hub.hub, "compare", "inv_1")
    with pytest.raises(AgentError) as err:
        await read(turn)
    assert (err.value.code, err.value.detail) == ("UNAVAILABLE", "compare is not connected")


async def test_session_loss_fails_open_turns(hub: Hub) -> None:
    c = await hub.connect("data")
    turn, _ = invoke(hub.hub, "data", "inv_1")
    await c.recv()
    await c.close()
    with pytest.raises(AgentError) as err:
        await read(turn)
    assert (err.value.code, err.value.detail) == ("UNAVAILABLE", "agent disconnected")


async def test_failure_uplink_fails_the_turn_with_its_code_and_detail(hub: Hub) -> None:
    c = await hub.connect("data")
    turn, _ = invoke(hub.hub, "data", "inv_1")
    await c.recv()
    await c.send("inv_1", failure=pb.Failure(code="DEADLINE_EXCEEDED", detail="LLM timed out"))
    with pytest.raises(AgentError) as err:
        await read(turn)
    assert (err.value.code, err.value.detail) == ("DEADLINE_EXCEEDED", "LLM timed out")
    assert str(err.value) == "DEADLINE_EXCEEDED: LLM timed out"


async def test_cancel_sends_cancel_and_drops_later_uplink(hub: Hub) -> None:
    c = await hub.connect("data")
    turn, _ = invoke(hub.hub, "data", "inv_1")
    await c.recv()
    pending = asyncio.create_task(turn.read())
    await asyncio.sleep(0.05)
    turn.cancel()
    turn.cancel()  # idempotent: one Cancel frame
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert turn.done()

    down = await c.recv()
    assert (down.ref, down.WhichOneof("kind")) == ("inv_1", "cancel")
    await c.frame("inv_1", assistant("too late"))  # dropped

    turn2, _ = invoke(hub.hub, "data", "inv_2")
    assert (await c.recv()).ref == "inv_2"  # no second cancel before it
    await c.frame("inv_2", assistant("fresh"))
    assert (await read(turn2)).message.content == "fresh"
    assert hub.hub.is_healthy("data")


async def test_uplink_for_an_unknown_ref_is_dropped_and_the_session_stays_up(hub: Hub) -> None:
    c = await hub.connect("data")
    await c.frame("inv_nobody", assistant("?"))
    await c.send("cmp_nobody", compacted=pb.CompactResponse(summary="?"))
    turn, _ = invoke(hub.hub, "data", "inv_1")
    await c.recv()
    await c.frame("inv_1", assistant("still here"))
    assert (await read(turn)).message.content == "still here"


async def test_uplink_after_final_is_dropped(hub: Hub) -> None:
    c = await hub.connect("data")
    turn, _ = invoke(hub.hub, "data", "inv_1")
    await c.recv()
    await c.frame("inv_1", pb.AgentFrame(final=pb.Final(content="done")))
    await c.frame("inv_1", assistant("after final"))
    assert (await read(turn)).final.content == "done"
    assert turn.done()
    await asyncio.sleep(0.05)
    assert hub.hub.is_healthy("data")


# --------------------------------------------------------------------------- compaction


async def test_compact_round_trip(hub: Hub) -> None:
    c = await hub.connect("data")
    request = pb.CompactRequest(previous_summary="old", messages=[pb.Message(role=pb.USER, content="hi")])
    pending = asyncio.create_task(hub.hub.compact("data", request))
    down = await c.recv()
    assert down.WhichOneof("kind") == "compact" and down.ref.startswith("cmp_")
    assert down.compact == request
    await c.send(down.ref, compacted=pb.CompactResponse(summary="new"))
    assert (await asyncio.wait_for(pending, WAIT_S)).summary == "new"


async def test_compact_failure_raises_agent_error(hub: Hub) -> None:
    c = await hub.connect("data")
    pending = asyncio.create_task(hub.hub.compact("data", pb.CompactRequest()))
    down = await c.recv()
    await c.send(down.ref, failure=pb.Failure(code="INTERNAL", detail="RuntimeError: boom"))
    with pytest.raises(AgentError) as err:
        await asyncio.wait_for(pending, WAIT_S)
    assert (err.value.code, err.value.detail) == ("INTERNAL", "RuntimeError: boom")


async def test_compact_timeout_raises_agent_error(hub: Hub) -> None:
    c = await hub.connect("data")
    pending = asyncio.create_task(hub.hub.compact("data", pb.CompactRequest()))
    down = await c.recv()
    with pytest.raises(AgentError) as err:
        await asyncio.wait_for(pending, WAIT_S)
    assert err.value.code == "DEADLINE_EXCEEDED"
    await c.send(down.ref, compacted=pb.CompactResponse(summary="late"))  # dropped
    await asyncio.sleep(0.05)
    assert hub.hub.is_healthy("data")


async def test_compact_fails_when_the_agent_is_not_connected_or_disconnects(hub: Hub) -> None:
    with pytest.raises(AgentError) as err:
        await hub.hub.compact("data", pb.CompactRequest())
    assert (err.value.code, err.value.detail) == ("UNAVAILABLE", "data is not connected")

    c = await hub.connect("data")
    pending = asyncio.create_task(hub.hub.compact("data", pb.CompactRequest()))
    await c.recv()
    await c.close()
    with pytest.raises(AgentError) as err:
        await asyncio.wait_for(pending, WAIT_S)
    assert (err.value.code, err.value.detail) == ("UNAVAILABLE", "agent disconnected")


async def test_start_fails_naming_an_address_it_cannot_bind(hub: Hub) -> None:
    other = AgentHub(AGENTS)
    with pytest.raises(RuntimeError, match=f"127.0.0.1:{hub.port}"):
        await other.start(f"127.0.0.1:{hub.port}", lambda a, h: None)
    await other.close()
