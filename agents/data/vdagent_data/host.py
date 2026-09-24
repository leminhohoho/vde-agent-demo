"""The host: connects an `Agent` to the Backend's hub (`vdagent.v1.AgentHub/Connect`) and serves turns.

COPIED FROM `agents/_template/` — do not edit in an agent folder. Change the template and re-copy.

The agent dials out; it needs no listening port. One session (a bidirectional stream) carries all
of this agent's turns and compactions, multiplexed by `ref`. A lost session cancels its in-flight
work and is re-opened with backoff; a refused session (`UNAUTHENTICATED`: the Backend does not list
this agent's name) ends the process.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn

import grpc
from dotenv import find_dotenv, load_dotenv
from vdagent_proto import agent_pb2, agent_pb2_grpc

from .contract import (
    SEND_TO_AGENT,
    Agent,
    AgentConfigError,
    AgentTimeoutError,
    ContractViolation,
    McpEndpoint,
    Message,
    Peer,
    ToolCall,
)

logger = logging.getLogger(__name__)

RUNTIME = "vdagent-template/2"
DEFAULT_BACKEND = "localhost:50050"
DEFAULT_MAX_STEPS = 12
BACKOFF_INITIAL_S = 0.5
BACKOFF_MAX_S = 10.0
BACKOFF_RESET_S = 30.0  # a session that lasted this long resets the backoff
BACKOFF_JITTER = 0.2

CHANNEL_OPTIONS = [
    ("grpc.keepalive_time_ms", 20_000),
    ("grpc.keepalive_timeout_ms", 10_000),
    ("grpc.keepalive_permit_without_calls", 1),
    ("grpc.http2.max_pings_without_data", 0),
]


class SessionRefusedError(Exception):
    """The Backend refused the session (`UNAUTHENTICATED`); retrying cannot help."""


def _find_timeout(exc: BaseException) -> AgentTimeoutError | None:
    """The first `AgentTimeoutError` in `exc`, looking through exception groups."""
    if isinstance(exc, AgentTimeoutError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:  # pyright: ignore[reportUnknownVariableType]
            found = _find_timeout(inner)  # pyright: ignore[reportUnknownArgumentType]
            if found is not None:
                return found
    return None


def _describe(exc: BaseException) -> str:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:  # pyright: ignore[reportUnknownMemberType]
        exc = exc.exceptions[0]  # pyright: ignore[reportUnknownVariableType]
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _failure_for(error: BaseException, what: str) -> agent_pb2.Failure:
    """The `failure` reported for a brain exception: timeouts are DEADLINE_EXCEEDED, the rest INTERNAL."""
    timeout = _find_timeout(error)
    if timeout is not None:
        logger.warning("%s timed out: %s", what, timeout)
        return agent_pb2.Failure(code="DEADLINE_EXCEEDED", detail=str(timeout))
    logger.error("%s failed", what, exc_info=error)
    return agent_pb2.Failure(code="INTERNAL", detail=_describe(error))


def _to_message(msg: agent_pb2.Message) -> Message | None:
    if msg.role == agent_pb2.USER:
        return {"role": "user", "content": msg.content}
    if msg.role == agent_pb2.ASSISTANT:
        if not msg.tool_calls:
            return {"role": "assistant", "content": msg.content}
        return {
            "role": "assistant",
            "content": msg.content or None,
            "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments_json}}
                for tc in msg.tool_calls
            ],
        }
    if msg.role == agent_pb2.TOOL:
        return {"role": "tool", "tool_call_id": msg.tool_call_id, "content": msg.content}
    return None


def _to_messages(messages: Sequence[agent_pb2.Message]) -> list[Message]:
    out: list[Message] = []
    for msg in messages:
        converted = _to_message(msg)
        if converted is None:
            logger.warning("skipping message with unspecified role")
            continue
        out.append(converted)
    return out


class _TurnContext:
    """`InvocationContext` for one turn (`ref`) on a hub session."""

    def __init__(self, start: agent_pb2.InvokeStart, ref: str, session: _Session) -> None:
        self.ref = ref
        self.invocation_id = start.invocation_id
        self.task_id = start.task_id
        self.user_id = start.user_id
        self.summary = start.summary
        self.history = _to_messages(start.history)
        self.peers = [Peer(name=p.name, description=p.description) for p in start.peers]
        self.mcp = McpEndpoint(url=start.mcp_url, token=start.mcp_token)
        self.max_steps = start.max_steps if start.max_steps > 0 else DEFAULT_MAX_STEPS
        self._session = session
        self._pending: dict[str, asyncio.Future[agent_pb2.AgentCallResult]] = {}
        # Ordering guard (R2–R5), about the latest assistant step.
        self._calls: dict[str, str] = {}  # tool_call id → tool name
        self._unresolved: set[str] = set()
        self._calling: set[str] = set()  # send_to_agent calls waiting for their reply
        self._called: set[str] = set()
        self._last_step_had_calls: bool | None = None  # None: no step emitted yet
        self._final_content = ""
        self._ended = False
        self.violation: ContractViolation | None = None

    def _violate(self, message: str) -> ContractViolation:
        violation = ContractViolation(message)
        if self.violation is None:
            self.violation = violation
        return violation

    def _check_open(self, what: str) -> None:
        if self._ended:
            raise self._violate(f"{what}: the turn already ended when invoke returned (R5)")

    def _write(self, frame: agent_pb2.AgentFrame) -> None:
        self._session.uplink(self.ref, frame=frame)

    async def emit_assistant(self, content: str, tool_calls: Sequence[ToolCall] = ()) -> None:
        self._check_open("emit_assistant")
        if self._unresolved:
            raise self._violate(
                f"emit_assistant: tool calls {sorted(self._unresolved)} of the previous step have no result yet (R2)"
            )
        ids = [tc.id for tc in tool_calls]
        if any(not i for i in ids) or len(set(ids)) != len(ids):
            raise self._violate(f"emit_assistant: tool-call ids must be non-empty and unique, got {ids} (R2)")
        self._calls = {tc.id: tc.name for tc in tool_calls}
        self._unresolved = set(ids)
        self._called = set()
        self._last_step_had_calls = bool(ids)
        self._final_content = content
        self._write(
            agent_pb2.AgentFrame(
                message=agent_pb2.Message(
                    role=agent_pb2.ASSISTANT,
                    content=content,
                    tool_calls=[
                        agent_pb2.ToolCall(id=tc.id, name=tc.name, arguments_json=tc.arguments_json)
                        for tc in tool_calls
                    ],
                )
            )
        )

    async def emit_tool_result(self, tool_call_id: str, content: str) -> None:
        what = f"emit_tool_result({tool_call_id!r})"
        self._check_open(what)
        if tool_call_id in self._calling:
            raise self._violate(f"{what}: its call_agent is still waiting for the reply (R4)")
        if tool_call_id not in self._unresolved:
            raise self._violate(f"{what}: not an unresolved tool call of the latest assistant step (R3)")
        self._unresolved.discard(tool_call_id)
        self._write(
            agent_pb2.AgentFrame(
                message=agent_pb2.Message(role=agent_pb2.TOOL, content=content, tool_call_id=tool_call_id)
            )
        )

    async def call_agent(self, tool_call_id: str, target: str, message: str) -> str:
        what = f"call_agent({tool_call_id!r})"
        self._check_open(what)
        name = self._calls.get(tool_call_id)
        if name is None:
            raise self._violate(f"{what}: not a tool call of the latest assistant step (R4)")
        if name != SEND_TO_AGENT:
            raise self._violate(f"{what}: tool call is {name!r}, not {SEND_TO_AGENT} (R4)")
        if tool_call_id not in self._unresolved:
            raise self._violate(f"{what}: tool call already has a result (R4)")
        if tool_call_id in self._called:
            raise self._violate(f"{what}: already called once (R4)")
        self._called.add(tool_call_id)
        self._calling.add(tool_call_id)
        future: asyncio.Future[agent_pb2.AgentCallResult] = asyncio.get_running_loop().create_future()
        self._pending[tool_call_id] = future
        try:
            self._write(
                agent_pb2.AgentFrame(
                    call=agent_pb2.AgentCallRequest(tool_call_id=tool_call_id, target=target, message=message)
                )
            )
            result = await future  # cancelled if the turn is cancelled or the session is lost
        finally:
            self._pending.pop(tool_call_id, None)
            self._calling.discard(tool_call_id)
        if result.ok or result.content.startswith("error:"):
            return result.content
        return f"error: {result.content or f'call to {target} failed'}"

    def deliver(self, frame: agent_pb2.BackendFrame) -> None:
        """Route a downlink frame of this turn to its waiting `call_agent`."""
        if frame.WhichOneof("kind") != "call_result":
            logger.warning("[%s] ignoring unexpected %s frame", self.invocation_id, frame.WhichOneof("kind"))
            return
        future = self._pending.pop(frame.call_result.tool_call_id, None)
        if future is None or future.done():
            logger.warning("[%s] ignoring call_result for %r", self.invocation_id, frame.call_result.tool_call_id)
            return
        future.set_result(frame.call_result)

    async def finish(self) -> None:
        """End the turn after `invoke` returned: enforce R5, then send `final`."""
        self._ended = True
        if self.violation is not None:
            raise self.violation
        if self._unresolved:
            raise self._violate(f"invoke returned with unresolved tool calls {sorted(self._unresolved)} (R5)")
        if self._last_step_had_calls is not False:
            raise self._violate("invoke returned without a final assistant step (one without tool calls) (R5)")
        self._write(agent_pb2.AgentFrame(final=agent_pb2.Final(content=self._final_content)))


class _Session:
    """One open hub session: dispatches downlinks, runs turns and compactions, writes uplinks."""

    def __init__(self, agent: Agent, call: Any) -> None:
        self._agent = agent
        self._call = call
        self._outbox: asyncio.Queue[agent_pb2.AgentUplink] = asyncio.Queue()
        self._turns: dict[str, tuple[_TurnContext, asyncio.Task[None]]] = {}
        self._work: set[asyncio.Task[None]] = set()  # every turn and compaction task

    def uplink(self, ref: str, **kind: Any) -> None:
        """Queue an uplink for turn `ref`; dropped once the turn was cancelled or ended."""
        if ref in self._turns:
            self._outbox.put_nowait(agent_pb2.AgentUplink(ref=ref, **kind))

    async def run(self, stop: asyncio.Event) -> None:
        """Serve until `stop` is set (returns) or the session is lost (raises)."""
        reader = asyncio.create_task(self._read_downlinks())
        writer = asyncio.create_task(self._write_uplinks())
        stopper = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait({reader, writer, stopper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (reader, writer, stopper):
                task.cancel()
            await self._cancel_work()
        for task in done:
            if task is not stopper and not task.cancelled() and task.exception() is not None:
                raise task.exception()  # pyright: ignore[reportGeneralTypeIssues]
        if stopper not in done:
            raise ConnectionError("the Backend ended the session")

    async def _cancel_work(self) -> None:
        for task in self._work:
            task.cancel()
        await asyncio.gather(*self._work, return_exceptions=True)
        self._turns.clear()

    async def _read_downlinks(self) -> None:
        while (msg := await self._call.read()) is not grpc.aio.EOF:  # pyright: ignore[reportAttributeAccessIssue]
            self._dispatch(msg)

    async def _write_uplinks(self) -> None:
        while True:
            await self._call.write(await self._outbox.get())

    def _dispatch(self, msg: agent_pb2.HubDownlink) -> None:
        kind, ref = msg.WhichOneof("kind"), msg.ref
        if kind == "frame" and msg.frame.WhichOneof("kind") == "start" and ref not in self._turns:
            self._start_turn(ref, msg.frame.start)
        elif kind == "frame" and ref in self._turns:
            self._turns[ref][0].deliver(msg.frame)
        elif kind == "cancel" and ref in self._turns:
            _, task = self._turns.pop(ref)  # nothing more is sent for this ref
            logger.info("[%s] turn cancelled by the Backend", ref)
            task.cancel()
        elif kind == "compact":
            self._spawn(self._compact(ref, msg.compact))
        else:
            logger.warning("ignoring %s downlink for unknown ref %r", kind, ref)

    def _spawn(self, coro: Any) -> asyncio.Task[None]:
        task = asyncio.create_task(coro)
        self._work.add(task)
        task.add_done_callback(self._work.discard)
        return task

    def _start_turn(self, ref: str, start: agent_pb2.InvokeStart) -> None:
        if not start.history:
            logger.error("[%s] malformed start: no history", ref)
            self._outbox.put_nowait(
                agent_pb2.AgentUplink(
                    ref=ref, failure=agent_pb2.Failure(code="INVALID_ARGUMENT", detail="start has no history")
                )
            )
            return
        ctx = _TurnContext(start, ref, self)
        # Registered before the task runs, so the turn's first emit already finds it.
        self._turns[ref] = (ctx, self._spawn(self._run_turn(ctx)))

    async def _run_turn(self, ctx: _TurnContext) -> None:
        try:
            await self._agent.invoke(ctx)
            await ctx.finish()
        except asyncio.CancelledError:
            raise  # cancelled by the Backend or the session ended: nothing to report
        except Exception as error:
            if ctx.violation is not None:
                logger.error("[%s] contract violation: %s", ctx.invocation_id, ctx.violation)
                failure = agent_pb2.Failure(code="INTERNAL", detail=f"contract violation: {ctx.violation}")
            else:
                failure = _failure_for(error, f"[{ctx.invocation_id}] turn")
            self.uplink(ctx.ref, failure=failure)
        finally:
            self._turns.pop(ctx.ref, None)

    async def _compact(self, ref: str, request: agent_pb2.CompactRequest) -> None:
        try:
            summary = await self._agent.compact(request.previous_summary, _to_messages(request.messages))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            reply = agent_pb2.AgentUplink(ref=ref, failure=_failure_for(error, "compact"))
        else:
            reply = agent_pb2.AgentUplink(ref=ref, compacted=agent_pb2.CompactResponse(summary=summary))
        self._outbox.put_nowait(reply)


async def _connect_once(
    name: str, agent: Agent, backend: str, stop: asyncio.Event, on_connected: Callable[[], None]
) -> None:
    """Open one session and serve it until `stop` (returns) or loss (raises)."""
    async with grpc.aio.insecure_channel(backend, options=CHANNEL_OPTIONS) as channel:
        call: Any = agent_pb2_grpc.AgentHubStub(channel).Connect()
        try:
            await call.write(agent_pb2.AgentUplink(hello=agent_pb2.Hello(agent=name, runtime=RUNTIME)))
            first = await call.read()
        except grpc.aio.AioRpcError as err:
            if err.code() == grpc.StatusCode.UNAUTHENTICATED:
                raise SessionRefusedError(err.details() or "unauthenticated") from None
            raise
        if first is grpc.aio.EOF or first.WhichOneof("kind") != "welcome":  # pyright: ignore[reportAttributeAccessIssue]
            raise ConnectionError("the Backend did not answer hello with welcome")
        on_connected()
        logger.info("agent %s connected to %s", name, backend)
        try:
            await _Session(agent, call).run(stop)
        finally:
            call.cancel()


async def run_agent(name: str, agent: Agent, backend: str, stop: asyncio.Event) -> None:
    """Keep a hub session open until `stop` is set, reconnecting with backoff.

    Raises `SessionRefusedError` when the Backend refuses the session (it does not list `name`).
    """
    loop = asyncio.get_running_loop()
    delay = BACKOFF_INITIAL_S
    while not stop.is_set():
        connected_at: list[float] = []
        try:
            await _connect_once(name, agent, backend, stop, lambda: connected_at.append(loop.time()))
        except SessionRefusedError:
            raise
        except Exception as e:
            reason = f"{e.code().name}: {e.details()}" if isinstance(e, grpc.aio.AioRpcError) else _describe(e)
            logger.warning("agent %s: session with %s lost or not established: %s", name, backend, reason)
        if stop.is_set():
            return
        if connected_at and loop.time() - connected_at[0] >= BACKOFF_RESET_S:
            delay = BACKOFF_INITIAL_S
        wait = delay * random.uniform(1 - BACKOFF_JITTER, 1 + BACKOFF_JITTER)
        logger.info("agent %s: reconnecting in %.1fs", name, wait)
        try:
            await asyncio.wait_for(stop.wait(), wait)
        except TimeoutError:
            pass
        delay = min(delay * 2, BACKOFF_MAX_S)


def _agent_dir() -> Path:
    """The agent's folder: the nearest directory above this file holding `pyproject.toml`."""
    here = Path(__file__).resolve().parent
    for directory in (here, *here.parents):
        if (directory / "pyproject.toml").is_file():
            return directory
    return here


def load_env_files(agent_dir: Path | None = None) -> None:
    """Agent `.env` beats the process env, which beats the repo-root `.env`.

    `agents/<name>/.env` is loaded with override; then the nearest `.env` above the agent folder
    (else one found from the cwd) fills only unset variables.
    """
    agent_dir = agent_dir or _agent_dir()
    load_dotenv(agent_dir / ".env", override=True)
    for directory in agent_dir.parents:
        candidate = directory / ".env"
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return
    found = find_dotenv(usecwd=True)
    if found:
        load_dotenv(found, override=False)


def _exit_config_error(name: str, message: str) -> NoReturn:
    print(f"{name}: {message}", file=sys.stderr)
    sys.exit(2)


async def _run_until_signalled(name: str, agent: Agent, backend: str) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await run_agent(name, agent, backend, stop)
    logger.info("agent %s shut down", name)


def main(name: str, build_agent: Callable[[], Agent]) -> None:
    """Process entrypoint (`python -m <package>`): configure, build the agent, stay connected until SIGINT/SIGTERM."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_env_files()
    backend = os.environ.get("VDAGENT_BACKEND", "").strip() or DEFAULT_BACKEND
    try:
        agent = build_agent()
    except AgentConfigError as exc:
        _exit_config_error(name, str(exc))
    try:
        asyncio.run(_run_until_signalled(name, agent, backend))
    except SessionRefusedError as exc:
        _exit_config_error(name, f"the Backend at {backend} refused the session: {exc}")
