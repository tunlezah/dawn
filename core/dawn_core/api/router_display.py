from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..context import DawnContext
from ..display.service import DisplayService
from .deps import get_ctx

router = APIRouter(prefix="/api/display", tags=["display"])


def display(ctx: DawnContext = Depends(get_ctx)) -> DisplayService:
    return ctx.svc(DisplayService)


class BrightnessBody(BaseModel):
    value: int


class ModeBody(BaseModel):
    mode: str


class CurveBody(BaseModel):
    curve: list[dict[str, float]]


@router.put("/brightness")
async def set_brightness(body: BrightnessBody, d: DisplayService = Depends(display)) -> dict[str, Any]:
    d.set_manual(body.value)
    return {"mode": d.mode, "manual_percent": d.manual_percent, "manual_until": d.manual_until.isoformat() if d.manual_until else None}


@router.put("/mode")
async def set_mode(body: ModeBody, d: DisplayService = Depends(display)) -> dict[str, Any]:
    if body.mode not in ("auto", "manual"):
        raise HTTPException(400, "mode must be auto or manual")
    d.set_mode(body.mode)
    return {"mode": d.mode}


@router.put("/curve")
async def set_curve(body: CurveBody, ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    pts = sorted(({"lux": float(p["lux"]), "brightness": int(p["brightness"])} for p in body.curve), key=lambda p: p["lux"])
    try:
        await ctx.cfg_mgr.update({"display": {"brightness": {"curve": pts}}})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, str(e)) from e
    return {"curve": pts}


@router.get("/sun")
async def sun(d: DisplayService = Depends(display)) -> dict[str, Any]:
    return d.sun()


@router.get("/lux")
async def lux(d: DisplayService = Depends(display)) -> dict[str, Any]:
    return {"lux": d.lux, "sensor": d.sensor.name if d.sensor else None, "brightness": d.controller.applied, "target": d.controller.target}
