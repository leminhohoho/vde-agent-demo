"""Agent hub (§4.3): agents dial the Backend; each keeps one `AgentHub.Connect` session open.

The hub is a `grpc.aio` server on `agent_listen`. A session starts with `hello` naming the agent;
the hub answers `welcome` if the name is listed in `config.yaml`, else it ends the RPC with
`UNAUTHENTICATED`. There is no credential: anyone who can reach the hub can connect as any listed
agent, so keep `agent_listen` on a trusted network. An agent is healthy iff it has a session.
A new session for a connected name replaces the old one (`ABORTED`), without an unhealthy event.

Turns and compactions are multiplexed on the session by `ref`: the invocation id for a turn, a
`cmp_<12 hex>` id for a compaction. Losing a session fails every open turn and pending compaction
of it with `AgentError("UNAVAILABLE", "agent disconnected")`. Uplink for an unknown or ended `ref`
is dropped with a warning. Construct and use inside the running event loop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import Any

import grpc

from vdagent_backend.config import AgentSpec
from vdagent_backend.ids import new_id
from vdagent_proto import agent_pb2 as pb
from vdagent_proto import agent_pb2_grpc

log = logging.getLogger(__name__)

HealthCallback = Callable[[str, bool], None]

COMPACT_TIMEOUT_S = 150.0  # agent LLM timeout (120 s) plus margin
SHUTDOWN_GRACE_S = 1.0
DISCONNECTED = "agent disconnected"

SERVER_OPTIONS = [
    ("grpc.keepalive_time_ms", 20_000),
    ("grpc.keepalive_timeout_ms", 10_000),
    ("grpc.keepalive_permit_without_calls", 1),
    ("grpc.http2.max_pings_without_data", 0),
    ("grpc.http2.min_ping_interval_without_data_ms", 10_000),  # client pings allowed every 10 s
    ("grpc.so_reuseport", 0),  # a second Backend on the same port must fail, not share it
]


class AgentError(Exception):
    """A turn or compaction failed on the agent side or in transport. `code` is a gRPC status name."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class TurnChannel:
    """One in-flight turn on an agent session."""

    def __init__(self, ref: str) -> None:
        self.ref = ref
        self._inbox: asyncio.Queue[pb.AgentFrame | AgentError | None] = asyncio.Queue()  # None: cancelled
        self._session: _Session | None = None
        self._forwarder: asyncio.Task[None] | None = None
        self._done = False
        self._cancelled = False

    async def read(self) -> pb.AgentFrame:
        """The next agent frame. Raises `AgentError` on failure or disconnect, `CancelledError` after `cancel()`."""
        if self._cancelled:
            raise asyncio.CancelledError
        item = await self._inbox.get()
        if item is None:
            raise asyncio.CancelledError
        if isinstance(item, AgentError):
            raise item
        return item

    def cancel(self) -> None:
        """Send `Cancel(ref)` once (if the turn is still open); later reads raise `CancelledError`."""
        if self._cancelled:
            return
        self._cancelled = True
        if not self._done:
            session = self._session
            self._end()
            if session is not None:
                session.send(pb.HubDownlink(ref=self.ref, cancel=pb.Cancel()))
        self._inbox.put_nowait(None)

    def done(self) -> bool:
        return self._done

    def _deliver(self, frame: pb.AgentFrame) -> None:
        self._inbox.put_nowait(frame)
        if frame.WhichOneof("kind") == "final":
            self._end()

    def _fail(self, error: AgentError) -> None:
        if self._done:
            return
        self._inbox.put_nowait(error)
        self._end()

    def _end(self) -> None:
        self._done = True
        session = self._session
        if session is not None and session.turns.get(self.ref) is self:
            del session.turns[self.ref]
        if self._forwarder is not None:
            self._forwarder.cancel()

    async def _forward(self, session: _Session, outbound: asyncio.Queue[pb.BackendFrame | None]) -> None:
        while (frame := await outbound.get()) is not None:
            session.send(pb.HubDownlink(ref=self.ref, frame=frame))


class _Session:
    """One authenticated agent connection."""

    def __init__(self, agent: str) -> None:
        self.agent = agent
        self.outbox: asyncio.Queue[pb.HubDownlink] = asyncio.Queue()
        self.turns: dict[str, TurnChannel] = {}
        self.compactions: dict[str, asyncio.Future[pb.CompactResponse]] = {}
        self.ended = asyncio.Event()
        self.end_status: tuple[grpc.StatusCode, str] | None = None

    def send(self, msg: pb.HubDownlink) -> None:
        if not self.ended.is_set():
            self.outbox.put_nowait(msg)

    def end(self, status: grpc.StatusCode | None = None, detail: str = "") -> None:
        """Close the session: fail its turns and compactions; the Connect handler ends the RPC with `status`."""
        if self.ended.is_set():
            return
        self.end_status = (status, detail) if status is not None else None
        self.ended.set()
        error = AgentError("UNAVAILABLE", DISCONNECTED)
        for turn in list(self.turns.values()):
            turn._fail(error)  # pyright: ignore[reportPrivateUsage]
        for future in self.compactions.values():
            if not future.done():
                future.set_exception(error)
        self.compactions.clear()


class _Servicer(agent_pb2_grpc.AgentHubServicer):
    def __init__(self, hub: AgentHub) -> None:
        self._hub = hub

    async def Connect(self, request_iterator: Any, context: grpc.aio.ServicerContext) -> None:  # noqa: N802
        await self._hub._serve(context)  # pyright: ignore[reportPrivateUsage]


class AgentHub:
    def __init__(self, agents: Mapping[str, AgentSpec], *, compact_timeout_s: float = COMPACT_TIMEOUT_S) -> None:
        self._agents = dict(agents)
        self._compact_timeout_s = compact_timeout_s
        self._sessions: dict[str, _Session] = {}
        self._server: grpc.aio.Server | None = None
        self.port: int | None = None  # bound hub port once started
        self._on_change: HealthCallback | None = None
        self._closing = False

    # ------------------------------------------------------------------ lifecycle

    async def start(self, listen: str, on_change: HealthCallback) -> int:
        """Serve the hub on `listen` (`host:port`); returns the bound port."""
        self._on_change = on_change
        server = grpc.aio.server(options=SERVER_OPTIONS)
        agent_pb2_grpc.add_AgentHubServicer_to_server(_Servicer(self), server)
        try:
            port = server.add_insecure_port(listen)
        except RuntimeError as e:
            raise RuntimeError(f"cannot listen for agents on {listen}: {e}") from e
        if port == 0:
            raise RuntimeError(f"cannot listen for agents on {listen}")
        await server.start()
        self._server, self.port = server, port
        log.info("agent hub listening on %s", listen)
        return port

    async def close(self) -> None:
        self._closing = True
        for session in list(self._sessions.values()):
            session.end(grpc.StatusCode.UNAVAILABLE, "backend shutting down")
        if self._server is not None:
            await self._server.stop(SHUTDOWN_GRACE_S)

    # ------------------------------------------------------------------ engine surface

    def is_healthy(self, agent: str) -> bool:
        return agent in self._sessions

    def invoke(self, agent: str, outbound: asyncio.Queue[pb.BackendFrame | None]) -> TurnChannel:
        """Start a turn with the `start` frame already queued on `outbound`; forward later frames until `None`."""
        first = outbound.get_nowait()
        if first is None or first.WhichOneof("kind") != "start":
            raise ValueError("the first outbound frame of a turn must be `start`")
        turn = TurnChannel(first.start.invocation_id)
        session = self._sessions.get(agent)
        if session is None:
            turn._fail(AgentError("UNAVAILABLE", f"{agent} is not connected"))  # pyright: ignore[reportPrivateUsage]
            return turn
        turn._session = session  # pyright: ignore[reportPrivateUsage]
        session.turns[turn.ref] = turn
        session.send(pb.HubDownlink(ref=turn.ref, frame=first))
        turn._forwarder = asyncio.create_task(  # pyright: ignore[reportPrivateUsage]
            turn._forward(session, outbound), name=f"turn {turn.ref} downlink"  # pyright: ignore[reportPrivateUsage]
        )
        return turn

    async def compact(self, agent: str, request: pb.CompactRequest) -> pb.CompactResponse:
        session = self._sessions.get(agent)
        if session is None:
            raise AgentError("UNAVAILABLE", f"{agent} is not connected")
        ref = new_id("cmp")
        future: asyncio.Future[pb.CompactResponse] = asyncio.get_running_loop().create_future()
        session.compactions[ref] = future
        session.send(pb.HubDownlink(ref=ref, compact=request))
        try:
            async with asyncio.timeout(self._compact_timeout_s):
                return await future
        except TimeoutError:
            raise AgentError("DEADLINE_EXCEEDED", f"no compaction reply within {self._compact_timeout_s:g}s") from None
        finally:
            session.compactions.pop(ref, None)

    # ------------------------------------------------------------------ sessions

    async def _serve(self, context: grpc.aio.ServicerContext) -> None:
        session = await self._accept(context)
        tasks = [
            asyncio.create_task(self._read_uplinks(session, context)),
            asyncio.create_task(self._write_downlinks(session, context)),
            asyncio.create_task(session.ended.wait()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            self._drop(session)
        if session.end_status is not None:
            await context.abort(*session.end_status)

    async def _accept(self, context: grpc.aio.ServicerContext) -> _Session:
        first = await context.read()
        if first is grpc.aio.EOF or first.WhichOneof("kind") != "hello":  # pyright: ignore[reportAttributeAccessIssue]
            await self._refuse(context, "the first message must be hello")
        hello = first.hello
        name = hello.agent
        if name not in self._agents:
            await self._refuse(context, f"unknown agent '{name}'")
        if self._closing:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "backend shutting down")

        session = _Session(name)
        old = self._sessions.get(name)
        if old is not None:
            log.info("agent %s reconnected; replacing its previous session", name)
            old.end(grpc.StatusCode.ABORTED, "replaced by a new connection")
        self._sessions[name] = session
        session.send(pb.HubDownlink(welcome=pb.Welcome()))
        log.info("agent %s connected from %s (%s)", name, context.peer(), hello.runtime or "unknown runtime")
        if old is None:
            self._notify(name, True)
        return session

    async def _refuse(self, context: grpc.aio.ServicerContext, reason: str) -> None:
        log.warning("refused agent session from %s: %s", context.peer(), reason)
        await context.abort(grpc.StatusCode.UNAUTHENTICATED, reason)

    def _drop(self, session: _Session) -> None:
        session.end()
        if self._sessions.get(session.agent) is session:
            del self._sessions[session.agent]
            log.info("agent %s disconnected", session.agent)
            self._notify(session.agent, False)

    async def _read_uplinks(self, session: _Session, context: grpc.aio.ServicerContext) -> None:
        try:
            while (msg := await context.read()) is not grpc.aio.EOF:  # pyright: ignore[reportAttributeAccessIssue]
                self._dispatch(session, msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.info("agent %s: session read ended: %r", session.agent, e)

    async def _write_downlinks(self, session: _Session, context: grpc.aio.ServicerContext) -> None:
        try:
            while True:
                await context.write(await session.outbox.get())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.info("agent %s: session write ended: %r", session.agent, e)

    def _dispatch(self, session: _Session, msg: pb.AgentUplink) -> None:
        kind, ref = msg.WhichOneof("kind"), msg.ref
        if kind == "frame":
            turn = session.turns.get(ref)
            if turn is not None:
                turn._deliver(msg.frame)  # pyright: ignore[reportPrivateUsage]
                return
        elif kind == "failure":
            error = AgentError(msg.failure.code or "UNKNOWN", msg.failure.detail)
            turn = session.turns.get(ref)
            if turn is not None:
                turn._fail(error)  # pyright: ignore[reportPrivateUsage]
                return
            future = session.compactions.pop(ref, None)
            if future is not None and not future.done():
                future.set_exception(error)
                return
        elif kind == "compacted":
            future = session.compactions.pop(ref, None)
            if future is not None and not future.done():
                future.set_result(msg.compacted)
                return
        log.warning("agent %s: dropping %s uplink for unknown or ended ref %r", session.agent, kind, ref)

    def _notify(self, agent: str, healthy: bool) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(agent, healthy)
        except Exception:
            log.exception("health callback failed for agent %s", agent)
