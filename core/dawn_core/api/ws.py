"""WebSocket: pushes the full UI state on connect and on every change."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger("dawn.ws")
router = APIRouter()
SEND_TIMEOUT_S = 2.0


class WsHub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        # remote address, connected_at, role ("face" from the kiosk), user agent, and last_seen: time.monotonic() of
        # the last message the page itself sent (monotonic, so a clock step cannot make a live face look absent) (the face pings every 15 s, so a kiosk whose page has frozen can be told
        # from one that is fine: the browser keeps answering protocol pings on a frozen page's behalf)
        self.info: dict[WebSocket, dict] = {}

    def connections(self) -> list[dict]:
        return [dict(v) for v in self.info.values()]

    def face_frozen(self, max_raf_age_ms: float = 60_000) -> bool:
        """A face whose page still pings but whose last animation frame is older than `max_raf_age_ms`: JavaScript runs
        but the compositor or the GPU is stuck, so the screen is frozen."""
        return any(v.get("role") == "face" and (v.get("raf_age_ms") or 0) > max_raf_age_ms for v in self.info.values())

    def face_seen(self, max_age_s: float = 60.0) -> bool:
        """A face is connected and its page has spoken within `max_age_s`."""
        now = time.monotonic()
        return any(v.get("role") == "face" and now - v.get("last_seen", 0.0) < max_age_s for v in self.info.values())

    async def broadcast(self, payload: dict) -> None:
        """Send to every client at once, each with its own 2 s budget: one slow phone used to hold the face's copy of a
        ring back by 2 s per broadcast, as the sends went one after another."""
        if not self.clients:
            return
        msg = json.dumps({"type": "state", "data": payload}, separators=(",", ":"))
        clients = list(self.clients)

        async def send(ws: WebSocket) -> bool:
            try:
                await asyncio.wait_for(ws.send_text(msg), timeout=SEND_TIMEOUT_S)
                return True
            except Exception:  # noqa: BLE001
                return False

        results = await asyncio.gather(*(send(ws) for ws in clients))
        for ws, ok in zip(clients, results, strict=True):
            if not ok:
                self.clients.discard(ws)
                self.info.pop(ws, None)


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    ctx = ws.app.state.ctx
    hub: WsHub = ws.app.state.ws_hub
    client = ws.client.host if ws.client else ""
    hosts = getattr(ws.app.state, "host_check", None)
    if hosts is not None and not ctx.sim and not hosts.ok(ws.headers.get("host")):
        await ws.close(code=4421)  # DNS rebinding: not a name of this clock
        return
    pin = ctx.config.web.auth.pin
    if pin and client not in ("127.0.0.1", "::1"):
        from .auth import COOKIE, check_token

        if not check_token(ctx, ws.cookies.get(COOKIE)):
            await ws.close(code=4401)  # the state carries everything the PIN protects
            return
    await ws.accept()
    hub.clients.add(ws)
    hub.info[ws] = {"remote": ws.client.host if ws.client else None, "connected_at": ctx.store.iso(), "role": None,
                    "agent": ws.headers.get("user-agent", "")[:160], "last_seen": time.monotonic()}
    try:
        await ws.send_text(json.dumps({"type": "state", "data": ctx.store.snapshot()}, separators=(",", ":")))
        while True:
            raw = await ws.receive_text()
            info = hub.info.get(ws)
            if info is not None:
                info["last_seen"] = time.monotonic()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "ping":
                if info is not None and isinstance(msg.get("raf_age_ms"), int | float):
                    info["raf_age_ms"] = float(msg["raf_age_ms"])
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
