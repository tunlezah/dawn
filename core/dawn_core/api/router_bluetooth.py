from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..bluetooth.service import BluetoothService
from ..context import DawnContext
from .deps import get_ctx

router = APIRouter(prefix="/api/bluetooth", tags=["bluetooth"])


def bt(ctx: DawnContext = Depends(get_ctx)) -> BluetoothService:
    return ctx.svc(BluetoothService)


class AddrBody(BaseModel):
    address: str


class DiscBody(BaseModel):
    seconds: int | None = None


async def _do(coro) -> dict[str, Any]:
    try:
        await coro
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e
    return {"ok": True}


@router.get("")
async def status(ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    return ctx.store.state.bluetooth.model_dump()


@router.post("/discoverable")
async def discoverable(body: DiscBody | None = None, b: BluetoothService = Depends(bt)) -> dict[str, Any]:
    return await _do(b.discoverable(body.seconds if body else None))


@router.post("/scan")
async def scan(body: dict | None = None, b: BluetoothService = Depends(bt)) -> dict[str, Any]:
    return await _do(b.backend.scan(bool((body or {}).get("on", True))))


@router.post("/pair")
async def pair(body: AddrBody, b: BluetoothService = Depends(bt)) -> dict[str, Any]:
    return await _do(b.backend.pair(body.address))


@router.post("/connect")
async def connect(body: AddrBody, b: BluetoothService = Depends(bt)) -> dict[str, Any]:
    return await _do(b.backend.connect(body.address))


@router.post("/disconnect")
async def disconnect(body: AddrBody, b: BluetoothService = Depends(bt)) -> dict[str, Any]:
    return await _do(b.backend.disconnect(body.address))


@router.delete("/{address}")
async def remove(address: str, b: BluetoothService = Depends(bt)) -> dict[str, Any]:
    return await _do(b.backend.remove(address))


@router.post("/player/{command}")
async def player(command: str, b: BluetoothService = Depends(bt)) -> dict[str, Any]:
    if command not in ("Play", "Pause", "Stop", "Next", "Previous"):
        raise HTTPException(400, "bad command")
    return await _do(b.backend.player(command))
