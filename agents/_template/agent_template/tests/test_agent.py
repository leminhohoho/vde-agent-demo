"""This agent's own behaviour. EDIT: replace with tests for your agent.

`test_host.py` exports helpers to drive the agent through the real host:
`agent_stub(agent)`, `start_frame(...)`, `read_all(call)`, `kinds(frames)`.
"""

from __future__ import annotations

import grpc
from vdagent_proto import agent_pb2

from ..agent import build_agent
from .test_host import agent_stub, kinds, read_all, start_frame


async def test_echo_replies_with_the_inbound_message():
    async with agent_stub(build_agent()) as stub:
        call = stub.Invoke()
        await call.write(start_frame())
        frames = await read_all(call)
        assert await call.code() == grpc.StatusCode.OK
    assert kinds(frames) == ["message:assistant", "final"]
    assert frames[-1].final.content == "echo: [from: user] revenue by region?"


async def test_compact_keeps_the_newest_2000_chars_of_summary_and_inbound_messages():
    agent = build_agent()
    summary = await agent.compact(
        "- earlier",
        [
            {"role": "user", "content": "[from: user] hi"},
            {"role": "assistant", "content": "echo: [from: user] hi"},
            {"role": "user", "content": "[from: data] " + "x" * 3000},
        ],
    )
    assert len(summary) == 2000
    assert summary.endswith("x" * 1987)
    short = await agent.compact("", [{"role": "user", "content": "[from: user] hi"}])
    assert short == "[from: user] hi"


async def test_compact_over_grpc_returns_the_summary():
    async with agent_stub(build_agent()) as stub:
        response = await stub.Compact(
            agent_pb2.CompactRequest(
                previous_summary="- earlier",
                messages=[agent_pb2.Message(role=agent_pb2.USER, content="[from: user] hi")],
            )
        )
    assert response.summary == "- earlier\n[from: user] hi"
