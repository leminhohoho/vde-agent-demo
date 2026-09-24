"""Test-side agent session: dials the hub like an agent host would."""

from __future__ import annotations

import asyncio
from typing import Any

import grpc

from vdagent_proto import agent_pb2 as pb
from vdagent_proto import agent_pb2_grpc

WAIT_S = 5.0


class HubClient:
    """One `AgentHub.Connect` stream seen from the agent side."""

    def __init__(self, port: int) -> None:
        self.channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        self.call: Any = agent_pb2_grpc.AgentHubStub(self.channel).Connect()
        self._write_lock = asyncio.Lock()  # grpc.aio allows one pending write per call

    async def hello(self, agent: str) -> pb.HubDownlink:
        await self.send("", hello=pb.Hello(agent=agent, runtime="test"))
        return await self.recv()

    async def send(self, ref: str, **kind: Any) -> None:
        async with self._write_lock:
            await self.call.write(pb.AgentUplink(ref=ref, **kind))

    async def frame(self, ref: str, frame: pb.AgentFrame) -> None:
        await self.send(ref, frame=frame)

    async def recv(self, timeout: float = WAIT_S) -> pb.HubDownlink:
        msg = await asyncio.wait_for(self.call.read(), timeout)
        assert msg is not grpc.aio.EOF, "hub ended the session"
        return msg

    async def status(self) -> tuple[grpc.StatusCode, str]:
        """Drain the session until the hub ends it; returns its status code and details."""
        try:
            while await asyncio.wait_for(self.call.read(), WAIT_S) is not grpc.aio.EOF:
                pass
        except grpc.aio.AioRpcError as err:
            return err.code(), err.details() or ""
        return await self.call.code(), await self.call.details()

    async def close(self) -> None:
        await self.channel.close()
