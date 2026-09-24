"""Agent clients (§4.3): an agent started after the backend is picked up promptly."""

from __future__ import annotations

import asyncio
import socket
import time

import grpc
from grpc_health.v1 import health_pb2_grpc

from conftest import FakeAgent
from vdagent_backend.config import AgentSpec
from vdagent_backend.engine import AgentClients
from vdagent_proto import agent_pb2_grpc


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_late_agent_becomes_healthy_despite_earlier_failed_probes() -> None:
    """Long agent downtime must not push the channel's reconnect backoff past a few seconds."""
    port = _free_port()
    clients = AgentClients({"data": AgentSpec("data", f"127.0.0.1:{port}", "")}, health_interval_s=3600)
    try:
        # Agent down: probe repeatedly, like the health loop does over a longer outage.
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            await clients.check_all()
            assert not clients.is_healthy("data")
            await asyncio.sleep(0.2)

        fake = FakeAgent("data")
        server = grpc.aio.server()
        agent_pb2_grpc.add_AgentServicer_to_server(fake, server)
        health_pb2_grpc.add_HealthServicer_to_server(fake.health, server)
        server.add_insecure_port(f"127.0.0.1:{port}")
        await server.start()
        await fake.set_healthy(True)
        try:
            started = time.monotonic()
            while not clients.is_healthy("data"):
                assert time.monotonic() - started < 2.5, "backend still sees the agent as down"
                await clients.check_all()
                await asyncio.sleep(0.1)
        finally:
            await server.stop(None)
    finally:
        await clients.close()
