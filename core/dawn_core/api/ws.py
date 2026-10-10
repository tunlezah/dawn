"""WebSocket: pushes the full UI state on connect and on every change."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger("dawn.ws")
router = APIRouter()


class WsHub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        # remote address, connected_at, role ("face" from the kiosk), user agent, and last_seen: time.time() of the
        # last message the page itself sent (the face pings every 15 s, so a kiosk whose page has frozen can be told
        # from one that is fine: the browser keeps answering protocol pings on a frozen page's behalf)
        self.info: dict[WebSocket, dict] = {}

    def connections(self) -> list[dict]:
        return [dict(v) for v in self.info.values()]

    def face_seen(self, max_age_s: float = 60.0) -> bool:
        """A face is connected and its page has spoken within `max_age_s`."""
        now = time.time()
        return any(v.get("role") == "face" and now - v.get("last_seen", 0.0) < max_age_s for v in self.info.values())

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
            self.info.pop(ws, None)


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    ctx = ws.app.state.ctx
    hub: WsHub = ws.app.state.ws_hub
    await ws.accept()
    hub.clients.add(ws)
    hub.info[ws] = {"remote": ws.client.host if ws.client else None, "connected_at": ctx.store.iso(), "role": None,
                    "agent": ws.headers.get("user-agent", "")[:160], "last_seen": time.time()}
    try:
        await ws.send_text(json.dumps({"type": "state", "data": ctx.store.snapshot()}, separators=(",", ":")))
        while True:
            raw = await ws.receive_text()
            info = hub.info.get(ws)
            if info is not None:
                info["last_seen"] = time.time()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong", "now": ctx.store.iso()}))
            elif msg.get("type") == "hello":
                hub.info.setdefault(ws, {})["role"] = str(msg.get("role") or "")[:16] or None
            elif msg.get("type") == "refresh":
                await ws.send_text(json.dumps({"type": "state", "data": ctx.store.snapshot()}, separators=(",", ":")))
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.debug("ws closed with error", exc_info=True)
    finally:
        hub.clients.discard(ws)
        hub.info.pop(ws, None)
