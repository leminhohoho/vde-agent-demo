"""Agent process: grpc.aio server exposing `vdagent.v1.Agent` and `grpc.health.v1` (spec §7.1).

Run with `python -m vdagent_agents.server`.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Any

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from vdagent_proto import agent_pb2, agent_pb2_grpc

from vdagent_agents.llm import LiteLLMClient, LLMClient, LLMTimeoutError
from vdagent_agents.loop import Invocation, compact, load_prompt
from vdagent_agents.mcp_client import McpSessionFactory, open_mcp_session
from vdagent_agents.settings import Settings, SettingsError, load_env_file, load_settings

logger = logging.getLogger("vdagent_agents")

AGENT_SERVICE_NAME = agent_pb2.DESCRIPTOR.services_by_name["Agent"].full_name
SHUTDOWN_GRACE_S = 5.0


class StreamClosedError(Exception):
    """The backend ended the stream while an agent call was still awaiting its result."""


def _find_exception[E: BaseException](exc: BaseException, kind: type[E]) -> E | None:
    """First `kind` in `exc`, looking through exception groups (task groups wrap failures)."""
    if isinstance(exc, kind):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:  # pyright: ignore[reportUnknownVariableType]
            found = _find_exception(inner, kind)  # pyright: ignore[reportUnknownArgumentType]
            if found is not None:
                return found
    return None


def _describe(exc: BaseException) -> str:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:  # pyright: ignore[reportUnknownMemberType]
        exc = exc.exceptions[0]  # pyright: ignore[reportUnknownVariableType]
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


class _CallRouter:
    """Routes `call_result` frames to the `send_to_agent` call awaiting them, by `tool_call_id`."""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[agent_pb2.AgentCallResult]] = {}
        self._closed: BaseException | None = None

    def expect(self, tool_call_id: str) -> asyncio.Future[agent_pb2.AgentCallResult]:
        if self._closed is not None:
            raise self._closed
        future: asyncio.Future[agent_pb2.AgentCallResult] = asyncio.get_running_loop().create_future()
        self._pending[tool_call_id] = future
        return future

    def discard(self, tool_call_id: str) -> None:
        self._pending.pop(tool_call_id, None)

    def resolve(self, result: agent_pb2.AgentCallResult) -> None:
        future = self._pending.pop(result.tool_call_id, None)
        if future is None or future.done():
            logger.warning("ignoring call_result for unknown tool_call_id %r", result.tool_call_id)
            return
        future.set_result(result)

    def close(self, reason: BaseException) -> None:
        self._closed = reason
        for future in self._pending.values():
            if not future.done():
                future.set_exception(reason)
        self._pending.clear()


class AgentService(agent_pb2_grpc.AgentServicer):
    def __init__(
        self,
        *,
        agent_name: str,
        llm: LLMClient,
        mcp_session_factory: McpSessionFactory = open_mcp_session,
        system_prompt: str | None = None,
        compact_prompt: str | None = None,
    ) -> None:
        self._agent_name = agent_name
        self._llm = llm
        self._mcp_session_factory = mcp_session_factory
        self._system_prompt = system_prompt if system_prompt is not None else load_prompt(agent_name)
        self._compact_prompt = compact_prompt if compact_prompt is not None else load_prompt("compact")

    async def Invoke(self, request_iterator: Any, context: grpc.aio.ServicerContext) -> None:  # noqa: N802
        first = await context.read()
        if first is grpc.aio.EOF or first.WhichOneof("kind") != "start":
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "the first frame must be `start`")
        start: agent_pb2.InvokeStart = first.start
        inv_id = start.invocation_id

        write_lock = asyncio.Lock()

        async def emit(frame: agent_pb2.AgentFrame) -> None:
            async with write_lock:
                await context.write(frame)

        router = _CallRouter()

        async def call_agent(tool_call_id: str, target: str, message: str) -> str:
            future = router.expect(tool_call_id)
            try:
                await emit(
                    agent_pb2.AgentFrame(
                        call=agent_pb2.AgentCallRequest(tool_call_id=tool_call_id, target=target, message=message)
                    )
                )
                result = await future
            finally:
                router.discard(tool_call_id)
            content = result.content
            if not result.ok and not content.startswith("error:"):
                content = f"error: {content or f'call to {target} failed'}"
            return content

        async def read_frames() -> None:
            try:
                while True:
                    frame = await context.read()
                    if frame is grpc.aio.EOF:
                        router.close(StreamClosedError("backend closed the stream while a call was pending"))
                        return
                    kind = frame.WhichOneof("kind")
                    if kind == "call_result":
                        router.resolve(frame.call_result)
                    else:
                        logger.warning("[%s] ignoring unexpected %s frame after start", inv_id, kind)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                router.close(StreamClosedError(f"stream read failed: {exc}"))

        reader = asyncio.create_task(read_frames())
        error: BaseException | None = None
        try:
            async with self._mcp_session_factory(start.mcp_url, start.mcp_token) as session:
                await Invocation(
                    start,
                    system_prompt=self._system_prompt,
                    llm=self._llm,
                    mcp=session,
                    emit=emit,
                    call_agent=call_agent,
                ).run()
        except Exception as exc:
            error = exc
        finally:
            reader.cancel()

        if error is None:
            return
        timeout = _find_exception(error, LLMTimeoutError)
        if timeout is not None:
            logger.warning("[%s] invocation aborted: %s", inv_id, timeout)
            await context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, str(timeout))
        logger.error("[%s] invocation failed", inv_id, exc_info=error)
        await context.abort(grpc.StatusCode.INTERNAL, _describe(error))

    async def Compact(  # noqa: N802
        self, request: agent_pb2.CompactRequest, context: grpc.aio.ServicerContext
    ) -> agent_pb2.CompactResponse:
        try:
            summary = await compact(self._llm, self._compact_prompt, request.previous_summary, request.messages)
        except LLMTimeoutError as exc:
            await context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, str(exc))
        except Exception as exc:
            logger.error("compact failed", exc_info=exc)
            await context.abort(grpc.StatusCode.INTERNAL, _describe(exc))
        return agent_pb2.CompactResponse(summary=summary)


async def start_server(
    service: AgentService, address: str
) -> tuple[grpc.aio.Server, health.aio.HealthServicer, int]:
    """Start a server with the Agent service and health; returns `(server, health, bound_port)`."""
    server = grpc.aio.server()
    agent_pb2_grpc.add_AgentServicer_to_server(service, server)
    health_servicer = health.aio.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    port = server.add_insecure_port(address)
    if port == 0:
        raise RuntimeError(f"could not bind {address}")
    await server.start()
    for name in ("", AGENT_SERVICE_NAME):
        await health_servicer.set(name, health_pb2.HealthCheckResponse.SERVING)
    return server, health_servicer, port


async def serve(settings: Settings) -> None:
    llm = LiteLLMClient(
        model=settings.llm_model,
        api_base=settings.openai_base_url,
        api_key=settings.openai_api_key,
        timeout_s=settings.llm_timeout_s,
    )
    service = AgentService(agent_name=settings.agent_name, llm=llm)
    server, health_servicer, port = await start_server(service, f"[::]:{settings.grpc_port}")
    logger.info("agent %s serving on port %d (model %s)", settings.agent_name, port, settings.llm_model)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    logger.info("agent %s shutting down", settings.agent_name)
    await health_servicer.enter_graceful_shutdown()
    await server.stop(SHUTDOWN_GRACE_S)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "LiteLLM"):  # per-request INFO lines drown out agent logs
        logging.getLogger(noisy).setLevel(logging.WARNING)
    load_env_file()
    try:
        settings = load_settings()
    except SettingsError as exc:
        print(f"vdagent agent: {exc}", file=sys.stderr)
        sys.exit(2)
    asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
