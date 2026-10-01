"""Input injection (sim, tests, the face's touch surface) and face controls."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..context import DawnContext
from ..face.service import FaceService
from ..inputs.service import InputService
from .deps import get_ctx

router = APIRouter(prefix="/api", tags=["input", "face"])

EVENTS = {"encoder_cw", "encoder_ccw", "encoder_push", "encoder_long", "button_down", "button_up", "button_short", "button_long", "touch"}


class InputBody(BaseModel):
    event: str
    value: Any = None


class MenuBody(BaseModel):
    open: bool
    page: str | None = None


class DemoBody(BaseModel):
    mode: str | None = None


@router.post("/input")
async def inject(body: InputBody, ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    if body.event not in EVENTS:
        raise HTTPException(400, f"unknown event; expected one of {sorted(EVENTS)}")
    await ctx.svc(InputService).inject(body.event)
    return {"ok": True, "face": ctx.store.state.face.mode}


@router.post("/face/touch")
async def face_touch(ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    await ctx.svc(InputService).inject("touch")
    return {"ok": True, "face": ctx.store.state.face.mode}


@router.post("/face/menu")
async def face_menu(body: MenuBody, ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    ctx.svc(FaceService).set_menu(body.open, body.page)
    return {"ok": True}


@router.post("/face/dismiss")
async def face_dismiss(ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    face = ctx.svc(FaceService)
    face.dismiss_message()
    try:
        from ..alarms.service import AlarmService

        await ctx.svc(AlarmService).dismiss_light_wake()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


@router.post("/face/wake")
async def face_wake(ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    ctx.svc(FaceService).wake()
    return {"ok": True}


@router.post("/face/demo")
async def face_demo(body: DemoBody, ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    """Force a face mode (screenshots, kiosk checks). Only setup/message/standby etc; null clears."""
    if body.mode == "setup" and ctx.store.state.face.setup is None:
        from ..state.ui import SetupInfo

        ctx.store.state.face.setup = SetupInfo(ssid="Dawn-Setup", password=ctx.config.network.hotspot.password, url="http://10.42.0.1/", qr_payload="http://10.42.0.1/")
    ctx.svc(FaceService).set_demo(body.mode)
    return {"ok": True, "face": ctx.store.state.face.mode}
