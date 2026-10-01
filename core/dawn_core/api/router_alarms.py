from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..alarms.schemas import AlarmIn, LeaveIn, TestRingIn
from ..alarms.service import AlarmService
from ..context import DawnContext
from .deps import get_ctx

router = APIRouter(prefix="/api/alarms", tags=["alarms"])


def alarms(ctx: DawnContext = Depends(get_ctx)) -> AlarmService:
    return ctx.svc(AlarmService)


@router.get("")
async def list_alarms(a: AlarmService = Depends(alarms)) -> list[dict[str, Any]]:
    return [a.to_out(r).model_dump() for r in a.rows()]


@router.post("", status_code=201)
async def create_alarm(body: AlarmIn, a: AlarmService = Depends(alarms)) -> dict[str, Any]:
    return a.to_out(a.create(body)).model_dump()


@router.post("/snooze")
async def snooze(a: AlarmService = Depends(alarms)) -> dict[str, Any]:
    return {"snoozed": await a.snooze()}


@router.post("/stop")
async def stop(a: AlarmService = Depends(alarms)) -> dict[str, Any]:
    return {"stopped": await a.stop_ringing()}


@router.post("/test")
async def test_ring(body: TestRingIn, a: AlarmService = Depends(alarms)) -> dict[str, Any]:
    await a.test_ring(source=body.source, label=body.label, volume=body.volume, ramp_seconds=body.ramp_seconds)
    return {"ok": True}


@router.put("/leave")
async def set_leave(body: LeaveIn, a: AlarmService = Depends(alarms)) -> dict[str, Any]:
    try:
        await a.set_leave(body.until)
    except ValueError as e:
        raise HTTPException(422, "until must be YYYY-MM-DD") from e
    return {"until": body.until}


@router.get("/holidays")
async def holidays(year: int | None = None, region: str | None = None, scope: str | None = None, a: AlarmService = Depends(alarms)) -> list[dict[str, Any]]:
    y = year or date.today().year
    return [{"date": d.isoformat(), "name": n, "regional": r} for d, n, r in a.calendar(region, scope).holidays_in(y)]


@router.post("/light-wake/dismiss")
async def dismiss_light(a: AlarmService = Depends(alarms)) -> dict[str, Any]:
    await a.dismiss_light_wake()
    return {"ok": True}


@router.get("/{alarm_id}")
async def get_alarm(alarm_id: int, a: AlarmService = Depends(alarms)) -> dict[str, Any]:
    row = a.get(alarm_id)
    if not row:
        raise HTTPException(404)
    return a.to_out(row).model_dump()


@router.put("/{alarm_id}")
async def update_alarm(alarm_id: int, body: AlarmIn, a: AlarmService = Depends(alarms)) -> dict[str, Any]:
    row = a.update(alarm_id, body)
    if not row:
        raise HTTPException(404)
    return a.to_out(row).model_dump()


@router.delete("/{alarm_id}")
async def delete_alarm(alarm_id: int, a: AlarmService = Depends(alarms)) -> dict[str, Any]:
    if not a.delete(alarm_id):
        raise HTTPException(404)
    return {"ok": True}


@router.post("/{alarm_id}/test")
async def test_alarm(alarm_id: int, a: AlarmService = Depends(alarms)) -> dict[str, Any]:
    row = a.get(alarm_id)
    if not row:
        raise HTTPException(404)
    await a.test_ring(row)
    return {"ok": True}


@router.post("/{alarm_id}/skip-next")
async def skip_next(alarm_id: int, body: dict | None = None, a: AlarmService = Depends(alarms)) -> dict[str, Any]:
    row = a.get(alarm_id)
    if not row:
        raise HTTPException(404)
    value = (body or {}).get("skip", not row.skip_next)
    a.patch(alarm_id, skip_next=bool(value))
    return {"skip_next": bool(value)}


@router.post("/{alarm_id}/enabled")
async def set_enabled(alarm_id: int, body: dict, a: AlarmService = Depends(alarms)) -> dict[str, Any]:
    row = a.patch(alarm_id, enabled=bool(body.get("enabled", True)))
    if not row:
        raise HTTPException(404)
    return {"enabled": row.enabled}
