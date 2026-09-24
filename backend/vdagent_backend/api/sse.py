"""`GET /api/events?user_id=` — per-user SSE stream (§10).

Keep-alive comment `: ping` every 15 s. The stream ends when the subscriber's bounded queue
overflows (the FE reconnects and refetches). No replay.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Query
from sse_starlette import EventSourceResponse, ServerSentEvent

from vdagent_backend.api.deps import Svc, resolve_user

router = APIRouter(prefix="/api")

PING_INTERVAL_S = 15


def _ping() -> ServerSentEvent:
    return ServerSentEvent(comment="ping")


@router.get("/events")
async def events(svc: Svc, user_id: Annotated[str | None, Query()] = None) -> EventSourceResponse:
    uid = await resolve_user(svc.db, user_id)
    sub = svc.bus.subscribe(uid)

    async def stream() -> AsyncIterator[ServerSentEvent]:
        try:
            while (ev := await sub.next()) is not None:
                yield ServerSentEvent(event=ev.event, data=json.dumps(ev.data, ensure_ascii=False))
        finally:
            svc.bus.unsubscribe(sub)

    return EventSourceResponse(
        stream(),
        ping=PING_INTERVAL_S,
        ping_message_factory=_ping,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
