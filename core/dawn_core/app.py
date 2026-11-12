"""FastAPI application factory."""

from __future__ import annotations

import hashlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import router_config, router_state, ws
from .config import ConfigManager
from .context import DawnContext
from .wiring import build_services

log = logging.getLogger("dawn.app")


def find_web_dist(override: str | None) -> Path | None:
    candidates = []
    if override:
        candidates.append(Path(override))
    env = os.environ.get("DAWN_WEB_DIST")
    if env:
        candidates.append(Path(env))
    candidates.append(Path(__file__).resolve().parents[2] / "web" / "dist")
    candidates.append(Path("/opt/dawn/web/dist"))
    for c in candidates:
        if (c / "index.html").exists():
            return c
    return None


def web_build_id(dist: Path) -> str | None:
    """What identifies the built web UI: the two pages name their scripts and styles by content hash, so the pages
    themselves change whenever anything in the build does."""
    h = hashlib.sha1(usedforsecurity=False)
    found = False
    for page in (dist / "index.html", dist / "face" / "index.html"):
        try:
            h.update(page.read_bytes())
            found = True
        except OSError:
            pass
    return h.hexdigest()[:12] if found else None


def create_app(cfg_mgr: ConfigManager | None = None) -> FastAPI:
    cfg_mgr = cfg_mgr or ConfigManager()
    ctx = DawnContext(cfg_mgr)
    hub = ws.WsHub()
    ctx.ws_hub = hub

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await ctx.store.start()
        ctx.store.subscribe(lambda _s: hub.broadcast(ctx.store.snapshot()))
        build_services(ctx)
        await ctx.registry.start_all()
        await cfg_mgr.start()
        ctx.refresh_settings_summary()
        log.info("dawn-core %s started (sim=%s)", __version__, ctx.sim)
        from .system import update

        await update.resume(ctx)  # started by an update that is still running: carry on showing it
        try:
            yield
        finally:
            await cfg_mgr.stop()
            await ctx.registry.stop_all()
            await ctx.store.stop()

    app = FastAPI(title="Dawn", version=__version__, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.ctx = ctx
    app.state.ws_hub = hub
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg_mgr.config.web.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from .api.auth import install_auth

    install_auth(app, ctx)

    app.include_router(router_state.router)
    app.include_router(router_config.router)
    app.include_router(ws.router)
    from .api.registry import include_phase_routers

    include_phase_routers(app)

    dist = find_web_dist(cfg_mgr.config.web.static_dir)
    if dist:
        # pages that are open (the kiosk's above all) load the new build when this changes: see web/src/shared/store.ts
        ctx.store.state.system.web_build = web_build_id(dist)
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
        if (dist / "static").exists():
            app.mount("/static", StaticFiles(directory=dist / "static"), name="static")
        face_index = dist / "face" / "index.html"
        index = dist / "index.html"

        @app.get("/face", include_in_schema=False)
        @app.get("/face/{path:path}", include_in_schema=False)
        async def face_spa(path: str = "") -> FileResponse:
            return FileResponse(face_index if face_index.exists() else index)

        for name in ("manifest.webmanifest", "sw.js", "favicon.svg", "icon-192.png", "icon-512.png", "robots.txt"):
            if (dist / name).exists():

                def _mk(p: Path):
                    async def f() -> FileResponse:
                        return FileResponse(p)

                    return f

                app.add_api_route(f"/{name}", _mk(dist / name), include_in_schema=False)

        @app.get("/{path:path}", include_in_schema=False)
        async def control_spa(path: str, request: Request):
            if path.startswith("api/") or path == "ws":
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            candidate = dist / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index)

        log.info("serving web UI from %s", dist)
    else:
        log.warning("web/dist not found; API only (run `make web-build`)")

        @app.get("/", include_in_schema=False)
        async def no_ui() -> JSONResponse:
            return JSONResponse({"detail": "web UI not built; see /api/docs"})

    return app
