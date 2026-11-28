"""Security on the LAN (robustness plan WP-H5): changes need Dawn's own header (CSRF), only the clock's own names are
served (DNS rebinding), PIN guessing is slowed, and with a PIN the WebSocket wants the session cookie."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from dawn_core.api.guard import LOGIN_LOCKOUT_S, LoginLimiter, host_name
from dawn_core.app import create_app
from dawn_core.config import ConfigManager


@pytest.fixture()
async def app(tmp_config):
    a = create_app(ConfigManager(tmp_config, poll_s=10))
    async with a.router.lifespan_context(a):
        yield a


def client(app, host: str = "dawn.local:8080", **headers) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=("192.168.1.50", 50000)), base_url=f"http://{host}", headers=headers)


async def test_a_change_without_the_header_is_refused(app) -> None:
    async with client(app) as c:
        r = await c.post("/api/system/shutdown")  # what a cross-site form post looks like
        assert r.status_code == 403 and "X-Dawn-Client" in r.json()["detail"]
        assert (await c.post("/api/alarms/stop")).status_code == 403
        assert (await c.get("/api/state")).status_code == 200  # reads need nothing
        r = await c.post("/api/alarms/stop", headers={"X-Dawn-Client": "1"})
        assert r.status_code != 403


async def test_only_the_clocks_own_names_are_served(app, monkeypatch) -> None:
    ctx = app.state.ctx
    from dawn_core.net.service import NetworkService

    ctx.svc(NetworkService)._task.cancel()  # its poll would put this machine's own address in
    monkeypatch.setattr(ctx, "sim", False)
    ctx.store.state.system.network.ip = "192.168.1.42"
    for good in ("dawn.local:8080", "localhost:8080", "127.0.0.1:8080", "[::1]:8080", "192.168.1.42:8080", "10.42.0.1:8080"):
        async with client(app, good) as c:
            assert (await c.get("/api/state")).status_code == 200, good
    async with client(app, "rebind.attacker.example:8080") as c:
        assert (await c.get("/api/state")).status_code == 421
        assert (await c.post("/api/system/reboot", headers={"X-Dawn-Client": "1"})).status_code == 421
    await ctx.cfg_mgr.update({"web": {"allowed_hosts": ["clock.home.arpa"]}})
    async with client(app, "clock.home.arpa") as c:
        assert (await c.get("/api/state")).status_code == 200
    # /api/health from the box itself is always answered (the kiosk's wait, dawn-update's health check)
    async with AsyncClient(transport=ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://whatever:8080") as c:
        assert (await c.get("/api/health")).status_code == 200
        assert (await c.get("/api/state")).status_code == 421


def test_host_names_are_parsed() -> None:
    assert host_name("Dawn.Local:8080") == "dawn.local"
    assert host_name("[fe80::1]:8080") == "fe80::1"
    assert host_name("::1") == "::1"
    assert host_name("10.42.0.1") == "10.42.0.1"


async def test_pin_guessing_is_slowed(app) -> None:
    ctx = app.state.ctx
    await ctx.cfg_mgr.update({"web": {"auth": {"pin": "4321"}}})
    async with client(app, **{"X-Dawn-Client": "1"}) as c:
        for _ in range(5):
            assert (await c.post("/api/auth/login", json={"pin": "0000"})).status_code == 401
        r = await c.post("/api/auth/login", json={"pin": "4321"})  # right, but locked out for a minute
        assert r.status_code == 429 and "Retry-After" in r.headers
    lim: LoginLimiter = app.state.login_limiter
    lim._locked = {k: 0.0 for k in lim._locked}  # the minute has passed
    async with client(app, **{"X-Dawn-Client": "1"}) as c:
        assert (await c.post("/api/auth/login", json={"pin": "4321"})).status_code == 200


def test_the_limiter_counts_a_minute_per_address() -> None:
    lim = LoginLimiter()
    for t in range(4):
        lim.failed("a", now=t * 20.0)  # four in 60 s, but spread so the window drops the first
    assert lim.locked("a", now=61.0) == 0.0
    for t in range(5):
        lim.failed("b", now=100.0 + t)
    assert lim.locked("b", now=105.0) == pytest.approx(LOGIN_LOCKOUT_S - 1, abs=0.01)
    assert lim.locked("a", now=105.0) == 0.0  # per address


def test_with_a_pin_the_websocket_wants_the_session_cookie(tmp_config) -> None:
    from starlette.websockets import WebSocketDisconnect

    tmp_config.write_text(tmp_config.read_text() + "web:\n  auth:\n    pin: '4321'\n")
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    with TestClient(app, headers={"X-Dawn-Client": "1"}) as c:  # the test client is not localhost
        with pytest.raises(WebSocketDisconnect) as e, c.websocket_connect("/ws") as ws:
            ws.receive_json()
        assert e.value.code == 4401
        assert c.post("/api/auth/login", json={"pin": "4321"}).status_code == 200  # sets the cookie on the client
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "state"


def test_a_star_origin_is_not_honoured(tmp_config) -> None:
    p = tmp_config
    p.write_text(p.read_text() + "web:\n  cors_origins: ['*']\n")
    app = create_app(ConfigManager(p, poll_s=10))
    with TestClient(app) as c:
        r = c.options("/api/system/shutdown", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST",
                                                      "Access-Control-Request-Headers": "x-dawn-client"})
        assert r.headers.get("access-control-allow-origin") is None  # the preflight is refused
