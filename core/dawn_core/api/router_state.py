from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..context import DawnContext
from ..state.ui import UIState
from .deps import get_ctx

router = APIRouter(prefix="/api", tags=["state"])


@router.get("/state", response_model=UIState)
def get_state(ctx: DawnContext = Depends(get_ctx)) -> Any:
    return ctx.store.snapshot()


@router.get("/events")
def get_events(limit: int = 100, ctx: DawnContext = Depends(get_ctx)) -> list[dict[str, Any]]:
    return ctx.db.recent_events(limit)


@router.post("/debug/cancel/{name}", include_in_schema=False)
async def debug_cancel(name: str, ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    """On-device check of the liveness repair (DAWN_DEBUG=1 only): cancel one of dawn-core's watched loops by name."""
    import os

    from fastapi import HTTPException

    if os.environ.get("DAWN_DEBUG") != "1":
        raise HTTPException(404, "Not Found")
    loop = ctx.liveness._loops.get(name)
    if loop is None or loop.task is None:
        raise HTTPException(404, f"no watched loop called {name!r}; watched: {', '.join(ctx.liveness.names())}")
    loop.task.cancel()
    return {"ok": True, "cancelled": name}


@router.get("/health")
def health(ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    from ..system.sysinfo import alarms_alive

    return {
        "ok": True,
        "version": ctx.store.state.system.version,
        "sim": ctx.sim,
        "failed_services": ctx.registry.failed,
        "database": ctx.db.mode,
        "alarms_alive": alarms_alive(ctx),  # the face rings by itself when core answers but this is false
        "loops_dead": ctx.liveness.check(),  # loops inside dawn-core that are dead or stale (liveness.py)
        "now": ctx.store.iso(),
    }
