"""Optional PIN authentication for the control UI.

No auth by default on the LAN. When `web.auth.pin` is set, /api/* requires a
session cookie obtained from POST /api/auth/login. The face (localhost) and the
WebSocket from localhost are exempt so the kiosk keeps working.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..context import DawnContext

COOKIE = "dawn_session"
EXEMPT_PREFIXES = ("/api/auth/", "/api/health", "/assets", "/face", "/static", "/manifest.webmanifest", "/sw.js")


def _secret(ctx: DawnContext) -> str:
    s = ctx.db.get("auth.secret")
    if not s:
        s = secrets.token_hex(32)
        ctx.db.set("auth.secret", s)
    return s


def make_token(ctx: DawnContext) -> str:
    exp = int(time.time()) + ctx.config.web.auth.session_hours * 3600
    sig = hmac.new(_secret(ctx).encode(), str(exp).encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def check_token(ctx: DawnContext, token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp_s, sig = token.split(".", 1)
    if not exp_s.isdigit() or int(exp_s) < time.time():
        return False
    good = hmac.new(_secret(ctx).encode(), exp_s.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, sig)


def install_auth(app: FastAPI, ctx: DawnContext) -> None:
    @app.middleware("http")
    async def auth_mw(request: Request, call_next):
        pin = ctx.config.web.auth.pin
        path = request.url.path
        if pin and path.startswith("/api") and not path.startswith(EXEMPT_PREFIXES):
            host = request.client.host if request.client else ""
            if host not in ("127.0.0.1", "::1") and not check_token(ctx, request.cookies.get(COOKIE)):
                return JSONResponse({"detail": "PIN required"}, status_code=401)
        return await call_next(request)

    @app.post("/api/auth/login", tags=["auth"])
    async def login(body: dict) -> JSONResponse:
        pin = ctx.config.web.auth.pin
        if pin and not hmac.compare_digest(str(body.get("pin", "")), pin):
            return JSONResponse({"ok": False}, status_code=401)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, make_token(ctx), max_age=ctx.config.web.auth.session_hours * 3600, httponly=True, samesite="lax")
        return resp

    @app.post("/api/auth/logout", tags=["auth"])
    async def logout() -> JSONResponse:
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE)
        return resp

    @app.get("/api/auth/status", tags=["auth"])
    async def status(request: Request) -> dict:
        pin = ctx.config.web.auth.pin
        return {"required": bool(pin), "authenticated": (not pin) or check_token(ctx, request.cookies.get(COOKIE))}
