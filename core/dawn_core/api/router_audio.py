from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..audio.service import AudioService
from ..context import DawnContext
from .deps import get_ctx

router = APIRouter(prefix="/api/audio", tags=["audio"])


def audio(ctx: DawnContext = Depends(get_ctx)) -> AudioService:
    return ctx.svc(AudioService)


class VolumeBody(BaseModel):
    volume: int


class MuteBody(BaseModel):
    muted: bool | None = None


class SinkBody(BaseModel):
    name: str | None = None


class EqBody(BaseModel):
    enabled: bool | None = None
    bass_db: float | None = None
    treble_db: float | None = None


class PlayBody(BaseModel):
    source: str
    level: str = "user"


@router.put("/volume")
async def set_volume(body: VolumeBody, a: AudioService = Depends(audio)) -> dict[str, Any]:
    return {"volume": await a.set_volume(body.volume)}


@router.post("/volume/step")
async def step_volume(body: dict[str, int], a: AudioService = Depends(audio)) -> dict[str, Any]:
    return {"volume": await a.step_volume(1 if body.get("direction", 1) >= 0 else -1)}


@router.post("/mute")
async def mute(body: MuteBody | None = None, a: AudioService = Depends(audio)) -> dict[str, Any]:
    return {"muted": await a.set_mute(body.muted if body else None)}


@router.get("/sinks")
async def sinks(a: AudioService = Depends(audio), ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    await a.refresh_sinks()
    return {"sinks": [s.model_dump() for s in ctx.store.state.audio.sinks], "pinned": ctx.config.audio.pinned_sink}


@router.put("/sink")
async def pin_sink(body: SinkBody, a: AudioService = Depends(audio)) -> dict[str, Any]:
    await a.pin_sink(body.name)
    return {"pinned": body.name}


@router.put("/eq")
async def set_eq(body: EqBody, ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    await ctx.cfg_mgr.update({"audio": {"eq": patch}})
    return ctx.config.audio.eq.model_dump()


@router.post("/play")
async def play(body: PlayBody, a: AudioService = Depends(audio)) -> dict[str, Any]:
    if body.level not in ("user", "sleep"):
        raise HTTPException(400, "level must be user or sleep")
    try:
        slot = await a.play(body.source, level=body.level)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from e
    return {"source": slot.source.ref, "state": slot.state}


@router.post("/stop")
async def stop(a: AudioService = Depends(audio)) -> dict[str, Any]:
    await a.stop_level("user")
    await a.stop_level("sleep")
    return {"ok": True}


@router.post("/standby")
async def standby(a: AudioService = Depends(audio)) -> dict[str, Any]:
    await a.standby()
    return {"ok": True}


@router.get("/chimes")
async def chimes(a: AudioService = Depends(audio)) -> list[dict[str, str]]:
    return a.chimes()


@router.get("/playlists")
async def playlists(a: AudioService = Depends(audio)) -> list[dict]:
    return a.playlists()


@router.get("/flowing")
async def flowing(a: AudioService = Depends(audio)) -> dict[str, bool]:
    return {"flowing": await a.audio_flowing()}
