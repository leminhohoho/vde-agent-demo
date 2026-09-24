"""FastAPI app factory and lifespan (§2.1).

`uvicorn vdagent_backend.app:app` resolves `app` lazily (PEP 562), so importing this module (e.g.
from tests) has no side effects. The app MUST run as a single process.

Lifespan: open backend.db (schema applied), startup recovery (§4.6), agent channels + health loop,
MCP session manager. `/mcp` is routed ahead of everything else; the built FE (`frontend_dist`) is
served at `/` with an SPA fallback when the directory exists.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.requests import Request
from starlette.responses import Response

from vdagent_backend.api import rest, sse
from vdagent_backend.api.deps import Services
from vdagent_backend.api.errors import error_response, install_error_handlers
from vdagent_backend.config import Config, load_config
from vdagent_backend.db.database import create_db
from vdagent_backend.engine import AgentClients, ChannelFactory, Engine
from vdagent_backend.events import EventBus
from vdagent_backend.mcp.server import create_mcp
from vdagent_backend.tokens import TokenRegistry


def create_app(cfg: Config | None = None, *, channel_factory: ChannelFactory | None = None) -> FastAPI:
    cfg = cfg or load_config()
    tokens = TokenRegistry()
    bus = EventBus()
    db = create_db(cfg.backend_db)
    mcp = create_mcp(cfg, db, tokens)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        clients = AgentClients(cfg.agents, health_interval_s=cfg.health_interval_s, channel_factory=channel_factory)
        engine = Engine(cfg, db, bus, tokens, clients)
        app.state.services = Services(cfg=cfg, db=db, bus=bus, engine=engine)
        await engine.start()
        try:
            async with mcp.lifespan():
                yield
        finally:
            await engine.stop()
            await db.dispose()

    app = FastAPI(title="vdagent backend", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(rest.router)
    app.include_router(sse.router)
    mcp.install(app)
    _serve_frontend(app, Path(cfg.frontend_dist))
    return app


def _serve_frontend(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.is_file():
        return
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str, request: Request) -> Response:
        if path == "api" or path.startswith("api/"):
            return error_response(404, "not_found", f"no route {request.url.path}")
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index)


_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    if name == "app":
        global _app
        if _app is None:
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
            _app = create_app()
        return _app
    raise AttributeError(name)
