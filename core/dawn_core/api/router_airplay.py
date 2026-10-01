from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response

from ..airplay.metadata import artwork_mime
from ..airplay.service import AirPlayService
from ..context import DawnContext
from .deps import get_ctx

router = APIRouter(prefix="/api/airplay", tags=["airplay"])


@router.get("")
async def status(ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    return ctx.store.state.airplay.model_dump()


@router.get("/artwork")
async def artwork(ctx: DawnContext = Depends(get_ctx)) -> Response:
    data = ctx.svc(AirPlayService).artwork()
    if not data:
        return Response(status_code=404)
    return Response(data, media_type=artwork_mime(data), headers={"Cache-Control": "no-store"})


@router.post("/remote/{command}")
async def remote(command: str, ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    if command not in ("Play", "Pause", "PlayPause", "Next", "Previous", "Stop"):
        return {"ok": False}
    await ctx.svc(AirPlayService).remote(command)
    return {"ok": True}
