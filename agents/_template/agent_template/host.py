"""The host: runs an `Agent` as a `vdagent.v1.Agent` gRPC server with `grpc.health.v1`.

COPIED FROM `agents/_template/` — do not edit in an agent folder. Change the template and re-copy.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from vdagent_proto import agent_pb2, agent_pb2_grpc

from .contract import Agent, McpEndpoint, Message, Peer, ToolCall

logger = logging.getLogger(__name__)

AGENT_SERVICE_NAME = agent_pb2.DESCRIPTOR.services_by_name["Agent"].full_name
DEFAULT_MAX_STEPS = 12


class StreamClosedError(Exception):
    """The Backend ended the stream while an agent call was still waiting for its reply."""


def _describe(exc: BaseException) -> str:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:  # pyright: ignore[reportUnknownMemberType]
        exc = exc.exceptions[0]  # pyright: ignore[reportUnknownVariableType]
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


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


class _StreamContext:
    """`InvocationContext` backed by one `Invoke` stream."""

    def __init__(self, start: agent_pb2.InvokeStart, context: grpc.aio.ServicerContext) -> None:
        self.invocation_id = start.invocation_id
        self.task_id = start.task_id
        self.user_id = start.user_id
        self.summary = start.summary
        self.history = _to_messages(start.history)
        self.peers = [Peer(name=p.name, description=p.description) for p in start.peers]
        self.mcp = McpEndpoint(url=start.mcp_url, token=start.mcp_token)
        self.max_steps = start.max_steps if start.max_steps > 0 else DEFAULT_MAX_STEPS
        self._context = context
        self._write_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[agent_pb2.AgentCallResult]] = {}
        self._closed: BaseException | None = None
        self.final_content = ""

    async def _write(self, frame: agent_pb2.AgentFrame) -> None:
        async with self._write_lock:
            await self._context.write(frame)

    async def emit_assistant(self, content: str, tool_calls: Sequence[ToolCall] = ()) -> None:
        await self._write(
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
        self.final_content = content

    async def emit_tool_result(self, tool_call_id: str, content: str) -> None:
        await self._write(
            agent_pb2.AgentFrame(
                message=agent_pb2.Message(role=agent_pb2.TOOL, content=content, tool_call_id=tool_call_id)
            )
        )

    async def call_agent(self, tool_call_id: str, target: str, message: str) -> str:
        if self._closed is not None:
            raise self._closed
        future: asyncio.Future[agent_pb2.AgentCallResult] = asyncio.get_running_loop().create_future()
        self._pending[tool_call_id] = future
        try:
            await self._write(
                agent_pb2.AgentFrame(
                    call=agent_pb2.AgentCallRequest(tool_call_id=tool_call_id, target=target, message=message)
                )
            )
            result = await future
        finally:
            self._pending.pop(tool_call_id, None)
        if result.ok or result.content.startswith("error:"):
            return result.content
        return f"error: {result.content or f'call to {target} failed'}"

    async def read_backend_frames(self) -> None:
        """Route `call_result` frames to waiting `call_agent`s until the Backend ends the stream."""
        try:
            while True:
                frame = await self._context.read()
                if frame is grpc.aio.EOF:
                    self._close(StreamClosedError("the Backend closed the stream while a call was pending"))
                    return
                if frame.WhichOneof("kind") != "call_result":
                    logger.warning("[%s] ignoring unexpected %s frame", self.invocation_id, frame.WhichOneof("kind"))
                    continue
                future = self._pending.pop(frame.call_result.tool_call_id, None)
                if future is None or future.done():
                    logger.warning("[%s] ignoring call_result for %r", self.invocation_id, frame.call_result.tool_call_id)
                    continue
                future.set_result(frame.call_result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._close(StreamClosedError(f"reading the stream failed: {exc}"))

    def _close(self, reason: BaseException) -> None:
        self._closed = reason
        for future in self._pending.values():
            if not future.done():
                future.set_exception(reason)
        self._pending.clear()

    async def finish(self) -> None:
        await self._write(agent_pb2.AgentFrame(final=agent_pb2.Final(content=self.final_content)))


class _AgentServicer(agent_pb2_grpc.AgentServicer):
    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    async def Invoke(self, request_iterator: Any, context: grpc.aio.ServicerContext) -> None:  # noqa: N802
        first = await context.read()
        if first is grpc.aio.EOF or first.WhichOneof("kind") != "start":
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "the first frame must be `start`")
        ctx = _StreamContext(first.start, context)
        reader = asyncio.create_task(ctx.read_backend_frames())
        try:
            await self._agent.invoke(ctx)
            await ctx.finish()
            return
        except Exception as exc:
            error: BaseException = exc
        finally:
            reader.cancel()
        logger.error("[%s] turn failed", ctx.invocation_id, exc_info=error)
        await context.abort(grpc.StatusCode.INTERNAL, _describe(error))


async def start_server(agent: Agent, address: str) -> tuple[grpc.aio.Server, health.aio.HealthServicer, int]:
    """Serve `agent` plus health on `address`; returns `(server, health, bound_port)`."""
    server = grpc.aio.server()
    agent_pb2_grpc.add_AgentServicer_to_server(_AgentServicer(agent), server)
    health_servicer = health.aio.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    port = server.add_insecure_port(address)
    if port == 0:
        raise RuntimeError(f"could not bind {address}")
    await server.start()
    for name in ("", AGENT_SERVICE_NAME):
        await health_servicer.set(name, health_pb2.HealthCheckResponse.SERVING)
    return server, health_servicer, port
