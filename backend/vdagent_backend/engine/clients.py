"""gRPC clients for the agent services (§4.3).

One `grpc.aio` channel per configured agent. A health loop calls
`grpc.health.v1.Health/Check(service="")` every `health_interval_s`; an agent is healthy iff the
answer is `SERVING`. Agents start unhealthy until their first successful check. Health changes are
reported through the `on_change(agent, healthy)` callback passed to `start`.

Channel creation is injectable (`channel_factory(address) -> grpc.aio.Channel`) so tests can point
agents at in-process servers. Construct inside the running event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Mapping

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from vdagent_backend.config import AgentSpec
from vdagent_proto import agent_pb2_grpc

log = logging.getLogger(__name__)

ChannelFactory = Callable[[str], grpc.aio.Channel]
HealthCallback = Callable[[str, bool], None]

# gRPC's default reconnect backoff grows to 120 s while an agent is down, so an agent started long
# after the BE would stay "unhealthy" for up to two minutes. Agents are plugged in and out at
# runtime (local addresses), so retry connecting at most every second.
_CHANNEL_OPTIONS = [
    ("grpc.initial_reconnect_backoff_ms", 500),
    ("grpc.min_reconnect_backoff_ms", 500),
    ("grpc.max_reconnect_backoff_ms", 1000),
]


def _default_channel(address: str) -> grpc.aio.Channel:
    return grpc.aio.insecure_channel(address, options=_CHANNEL_OPTIONS)


class AgentClients:
    def __init__(
        self,
        agents: Mapping[str, AgentSpec],
        *,
        health_interval_s: float,
        channel_factory: ChannelFactory | None = None,
        check_timeout_s: float = 5.0,
    ) -> None:
        factory = channel_factory or _default_channel
        self._channels = {name: factory(spec.address) for name, spec in agents.items()}
        self._agents = {name: agent_pb2_grpc.AgentStub(ch) for name, ch in self._channels.items()}
        self._health = {name: health_pb2_grpc.HealthStub(ch) for name, ch in self._channels.items()}
        self._healthy = dict.fromkeys(agents, False)
        self._interval = health_interval_s
        self._check_timeout = check_timeout_s
        self._on_change: HealthCallback | None = None
        self._loop_task: asyncio.Task[None] | None = None

    def stub(self, agent: str) -> agent_pb2_grpc.AgentStub:
        return self._agents[agent]

    def is_healthy(self, agent: str) -> bool:
        return self._healthy.get(agent, False)

    async def _check(self, agent: str) -> bool:
        try:
            resp = await self._health[agent].Check(
                health_pb2.HealthCheckRequest(service=""), timeout=self._check_timeout
            )
        except grpc.aio.AioRpcError:
            return False
        except Exception:
            log.exception("health probe of %s raised", agent)
            return False
        return resp.status == health_pb2.HealthCheckResponse.SERVING

    async def check_all(self) -> None:
        """Probe every agent once; report changes through the callback."""
        names = list(self._health)
        results = await asyncio.gather(*(self._check(n) for n in names))
        for name, healthy in zip(names, results, strict=True):
            if self._healthy[name] != healthy:
                self._healthy[name] = healthy
                log.info("agent %s is now %s", name, "healthy" if healthy else "unhealthy")
                if self._on_change is not None:
                    self._on_change(name, healthy)

    async def start(self, on_change: HealthCallback) -> None:
        """Run a first probe round, then keep probing in the background."""
        self._on_change = on_change
        await self.check_all()
        self._loop_task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.check_all()
            except Exception:  # never let the health loop die
                log.exception("health check round failed")

    async def close(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
        await asyncio.gather(*(ch.close() for ch in self._channels.values()))
