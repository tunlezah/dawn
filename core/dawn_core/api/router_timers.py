from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..alarms.schemas import TimerIn
from ..alarms.timers import TimerService
from ..context import DawnContext
from .deps import get_ctx

router = APIRouter(prefix="/api/timers", tags=["timers"])


def timers(ctx: DawnContext = Depends(get_ctx)) -> TimerService:
    return ctx.svc(TimerService)


@router.post("/sleep")
async def start_sleep(body: TimerIn, t: TimerService = Depends(timers)) -> dict[str, Any]:
    await t.start_sleep(body.minutes)
    return {"ok": True}


@router.delete("/sleep")
async def cancel_sleep(t: TimerService = Depends(timers)) -> dict[str, Any]:
    await t.cancel_sleep()
    return {"ok": True}


@router.post("/nap")
async def start_nap(body: TimerIn, t: TimerService = Depends(timers)) -> dict[str, Any]:
    await t.start_nap(body.minutes)
    return {"ok": True}


@router.delete("/nap")
async def cancel_nap(t: TimerService = Depends(timers)) -> dict[str, Any]:
    await t.cancel_nap()
    return {"ok": True}
