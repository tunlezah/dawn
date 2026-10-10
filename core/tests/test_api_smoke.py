from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from dawn_core.app import create_app
from dawn_core.config import ConfigManager


@pytest.fixture()
async def client(tmp_config):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def test_state_and_health(client: AsyncClient) -> None:
    r = await client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"]
    r = await client.get("/api/state")
    assert r.status_code == 200
    s = r.json()
    assert s["face"]["mode"] == "standby"
    assert s["tz"] == "Australia/Sydney"
    assert s["system"]["sim"] is True


async def test_config_schema_and_patch(client: AsyncClient) -> None:
    r = await client.get("/api/config/schema")
    assert r.status_code == 200 and "properties" in r.json()
    r = await client.patch("/api/config", json={"general": {"name": "Test Clock"}})
    assert r.status_code == 200 and r.json()["general"]["name"] == "Test Clock"
    r = await client.patch("/api/config", json={"general": {"timezone": "Nope/Nope"}})
    assert r.status_code == 422
    r = await client.get("/api/state")
    assert r.json()["settings"]["name"] == "Test Clock"


async def test_menu_activity_holds_the_sheet_open(client: AsyncClient) -> None:
    """A finger on the sheet (slider drag, tile press) re-arms the auto-close timer; a closed menu is left closed."""
    from dawn_core.face.service import FaceService

    app = client._transport.app  # type: ignore[attr-defined]
    face = app.state.ctx.svc(FaceService)
    r = await client.post("/api/face/menu/activity")
    assert r.status_code == 200 and r.json()["open"] is False
    r = await client.post("/api/face/menu", json={"open": True, "page": None})
    assert r.status_code == 200
    armed = face._menu_opened_at
    assert armed is not None
    r = await client.post("/api/face/menu/activity")
    assert r.status_code == 200 and r.json()["open"] is True
    assert face._menu_opened_at is not None and face._menu_opened_at >= armed
    s = (await client.get("/api/state")).json()
    assert s["face"]["menu_open"] is True and s["face"]["wake_until"] is not None


def test_the_face_is_known_by_what_its_page_sends(tmp_config) -> None:
    """The supervisor tells a live kiosk from a frozen one by the page's own pings, not by the socket being open."""
    from fastapi.testclient import TestClient

    app = create_app(ConfigManager(tmp_config, poll_s=10))
    with TestClient(app) as c:
        hub = app.state.ws_hub
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "state"
            assert not hub.face_seen()  # connected, but it has not said it is the face
            ws.send_json({"type": "hello", "role": "face"})
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"
            assert hub.face_seen()
            info = next(iter(hub.info.values()))
            info["last_seen"] -= 120  # the page went quiet two minutes ago
            assert not hub.face_seen() and hub.face_seen(max_age_s=300)
            ws.send_json({"type": "ping"})
            ws.receive_json()
            assert hub.face_seen()
        assert not hub.face_seen()
