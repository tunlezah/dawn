"""WebSocket: pushes the full UI state on connect and on every change."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger("dawn.ws")
router = APIRouter()


class WsHub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def broadcast(self, payload: dict) -> None:
        if not self.clients:
            return
        msg = json.dumps({"type": "state", "data": payload}, separators=(",", ":"))
        dead = []
        for ws in list(self.clients):
            try:
                await asyncio.wait_for(ws.send_text(msg), timeout=2)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    ctx = ws.app.state.ctx
    hub: WsHub = ws.app.state.ws_hub
    await ws.accept()
    hub.clients.add(ws)
    try:
        await ws.send_text(json.dumps({"type": "state", "data": ctx.store.snapshot()}, separators=(",", ":")))
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong", "now": ctx.store.iso()}))
            elif msg.get("type") == "refresh":
                await ws.send_text(json.dumps({"type": "state", "data": ctx.store.snapshot()}, separators=(",", ":")))
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.debug("ws closed with error", exc_info=True)
    finally:
        hub.clients.discard(ws)
