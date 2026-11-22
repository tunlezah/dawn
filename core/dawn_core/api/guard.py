"""Requests from the LAN that the clock should not act on.

Dawn has no login by default, so any web page opened by anyone on the Wi-Fi could reach it:
  * CSRF. A plain form post from another site (POST /api/system/shutdown, /reboot, /api/alarms/stop take no body)
    worked. Every non-GET /api/* request must now carry `X-Dawn-Client: 1`: a cross-site form cannot set a custom
    header, and a cross-site fetch() that sets one is preflighted, which CORS refuses (no `*` origin is honoured).
  * DNS rebinding. A page on an attacker's name that later resolves to the clock's address is same-origin with it
    in the browser's eyes, but its requests still say `Host: attacker.example`. Only Host values that name this box are
    served (localhost, its addresses, <hostname>.local, the setup hotspot's 10.42.0.1, and `web.allowed_hosts`);
    anything else gets 421. `/api/health` from the box itself is always answered.
  * PIN guessing: at most 5 failed logins a minute from one address, then 60 s of 429.
The kiosk (127.0.0.1) only needs the header, which the web client sends on every change.
"""

from __future__ import annotations

import socket
import time
from collections import deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ..context import DawnContext

CLIENT_HEADER = "x-dawn-client"
LOCAL = ("127.0.0.1", "::1", "localhost")
HOTSPOT_IP = "10.42.0.1"
ADDR_TTL_S = 60.0
LOGIN_WINDOW_S, LOGIN_MAX_FAILS, LOGIN_LOCKOUT_S = 60.0, 5, 60.0


def host_name(header: str) -> str:
    """`dawn.local:8080` -> `dawn.local`, `[fe80::1]:8080` -> `fe80::1`, lower case."""
    h = header.strip().lower()
    if h.startswith("["):
        return h[1:].split("]", 1)[0]
    if h.count(":") == 1:
        return h.rsplit(":", 1)[0]
    return h


class HostCheck:
    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self._addrs: set[str] = set()
        self._addrs_at = -1e9

    def _box_addresses(self) -> set[str]:
        now = time.monotonic()
        if now - self._addrs_at > ADDR_TTL_S:
            addrs: set[str] = set()
            try:
                for info in socket.getaddrinfo(socket.gethostname(), None):
                    addrs.add(str(info[4][0]).lower())
            except OSError:
                pass
            self._addrs, self._addrs_at = addrs, now
        return self._addrs

    def allowed(self) -> set[str]:
        cfg = self.ctx.config
        hn = cfg.general.hostname.lower()
        out = {*LOCAL, HOTSPOT_IP, hn, f"{hn}.local", socket.gethostname().lower(), f"{socket.gethostname().lower()}.local"}
        ip = self.ctx.store.state.system.network.ip
        if ip:
            out.add(ip.lower())
        out |= self._box_addresses()
        out |= {h.lower() for h in cfg.web.allowed_hosts}
        return out

    def ok(self, header: str | None) -> bool:
        if not header:
            return True  # HTTP/1.0 without Host: not a browser on someone else's name
        return host_name(header) in self.allowed()


class LoginLimiter:
    """At most LOGIN_MAX_FAILS failed logins in LOGIN_WINDOW_S per address, then LOGIN_LOCKOUT_S of refusals."""

    def __init__(self) -> None:
        self._fails: dict[str, deque[float]] = {}
        self._locked: dict[str, float] = {}

    def locked(self, addr: str, now: float | None = None) -> float:
        """Seconds left of a lockout (0 when none)."""
        now = time.monotonic() if now is None else now
        until = self._locked.get(addr, 0.0)
        return max(0.0, until - now)

    def failed(self, addr: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        q = self._fails.setdefault(addr, deque())
        q.append(now)
        while q and now - q[0] > LOGIN_WINDOW_S:
            q.popleft()
        if len(q) >= LOGIN_MAX_FAILS:
            self._locked[addr] = now + LOGIN_LOCKOUT_S
            q.clear()

    def succeeded(self, addr: str) -> None:
        self._fails.pop(addr, None)


def install_guard(app: FastAPI, ctx: DawnContext) -> HostCheck:
    hosts = HostCheck(ctx)

    @app.middleware("http")
    async def guard_mw(request: Request, call_next):
        path = request.url.path
        client = request.client.host if request.client else ""
        # the simulator runs on a laptop under whatever name the developer uses; the Pi checks
        if not ctx.sim and not hosts.ok(request.headers.get("host")) and not (path == "/api/health" and client in LOCAL):
            return PlainTextResponse("Misdirected request: this is not a name of this clock", status_code=421)
        if path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CLIENT_HEADER) != "1":
            return JSONResponse({"detail": "This request needs the X-Dawn-Client header (sent by Dawn's own web pages)."}, status_code=403)
        return await call_next(request)

    return hosts
