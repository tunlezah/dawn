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


@router.get("/health")
def health(ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    return {
        "ok": True,
        "version": ctx.store.state.system.version,
        "sim": ctx.sim,
        "failed_services": ctx.registry.failed,
        "now": ctx.store.iso(),
    }
