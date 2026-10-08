from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import select

from ..audio.service import AudioService
from ..context import DawnContext
from ..dab.service import DabService, TunerBusy
from ..dab.welle import norm_sid
from ..db.models import PresetRow
from .deps import get_ctx

router = APIRouter(prefix="/api", tags=["dab", "presets"])


def dab(ctx: DawnContext = Depends(get_ctx)) -> DabService:
    return ctx.svc(DabService)


class PlayBody(BaseModel):
    sid: str


class ChannelBody(BaseModel):
    channel: str


class PresetBody(BaseModel):
    label: str
    source: str


class PresetOrder(BaseModel):
    ids: list[int]


@router.post("/dab/scan")
async def start_scan(d: DabService = Depends(dab)) -> dict[str, Any]:
    try:
        started = await d.start_scan()
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"started": started}


@router.delete("/dab/scan")
async def cancel_scan(d: DabService = Depends(dab)) -> dict[str, Any]:
    await d.cancel_scan()
    return {"ok": True}


@router.get("/dab/services")
async def services(d: DabService = Depends(dab)) -> list[dict[str, Any]]:
    return [s.model_dump() for s in d.services()]


@router.get("/dab/ensembles")
async def ensembles(d: DabService = Depends(dab)) -> list[dict[str, Any]]:
    return d.ensembles()


@router.post("/dab/play")
async def play(body: PlayBody, ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        slot = await ctx.svc(AudioService).play(f"dab:{norm_sid(body.sid)}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e
    return {"source": slot.source.ref, "state": slot.state, "error": slot.source.error}


@router.post("/dab/channel")
async def tune(body: ChannelBody, d: DabService = Depends(dab)) -> dict[str, Any]:
    try:
        await d.user_tune(body.channel)
    except TunerBusy as e:
        raise HTTPException(409, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    return {"channel": d.channel}


@router.post("/dab/restart")
async def restart(d: DabService = Depends(dab)) -> dict[str, Any]:
    try:
        return {"restarted": await d.restart_welle(reason="manual")}
    except TunerBusy as e:
        raise HTTPException(409, str(e)) from e


@router.get("/dab/logo/{sid}")
async def logo(sid: str, d: DabService = Depends(dab)) -> Response:
    data, ctype = d.logo(sid.split(".")[0])
    return Response(data, media_type=ctype, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/dab/slide/{sid}")
async def slide(sid: str, d: DabService = Depends(dab)) -> Response:
    got = await d.client.slide(norm_sid(sid))
    if not got:
        data, ctype = d.logo(sid)
        return Response(data, media_type=ctype)
    data, ctype = got
    return Response(data, media_type=ctype, headers={"Cache-Control": "no-store"})


# ---- presets ---------------------------------------------------------------
@router.get("/presets")
async def list_presets(ctx: DawnContext = Depends(get_ctx)) -> list[dict[str, Any]]:
    return [p.model_dump() for p in ctx.svc(AudioService).presets()]


@router.post("/presets")
async def add_preset(body: PresetBody, ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.db.session() as s:
        n = len(s.exec(select(PresetRow)).all())
        row = PresetRow(label=body.label, source=body.source, position=n)
        s.add(row)
        s.commit()
        s.refresh(row)
        pid = row.id
    ctx.svc(AudioService).publish()
    return {"id": pid}


@router.put("/presets/order")
async def order_presets(body: PresetOrder, ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.db.session() as s:
        rows = {r.id: r for r in s.exec(select(PresetRow)).all()}
        pos = 0
        for pid in body.ids:
            if pid in rows:
                rows[pid].position = pos
                s.add(rows[pid])
                pos += 1
        for r in rows.values():
            if r.id not in body.ids:
                r.position = pos
                s.add(r)
                pos += 1
        s.commit()
    ctx.svc(AudioService).publish()
    return {"ok": True}


@router.put("/presets/{pid}")
async def update_preset(pid: int, body: PresetBody, ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.db.session() as s:
        row = s.get(PresetRow, pid)
        if not row:
            raise HTTPException(404)
        row.label, row.source = body.label, body.source
        s.add(row)
        s.commit()
    ctx.svc(AudioService).publish()
    return {"ok": True}


@router.delete("/presets/{pid}")
async def delete_preset(pid: int, ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.db.session() as s:
        row = s.get(PresetRow, pid)
        if row:
            s.delete(row)
            s.commit()
    ctx.svc(AudioService).publish()
    return {"ok": True}


@router.post("/presets/next")
async def next_preset(ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    p = await ctx.svc(AudioService).play_next_preset()
    return {"preset": p.model_dump() if p else None}


@router.post("/presets/prev")
async def prev_preset(ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    p = await ctx.svc(AudioService).play_next_preset(step=-1)
    return {"preset": p.model_dump() if p else None}
