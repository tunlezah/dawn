from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from dawn_core.app import create_app
from dawn_core.config import ConfigManager
from dawn_core.net.nmcli import parse_active, parse_wifi_list


def test_parse_wifi_list_dedup_and_escapes() -> None:
    txt = "yes:HomeWiFi:82:WPA2\nno:HomeWiFi:60:WPA2\nno:Cafe\\:Guest:25:\nno::10:WPA1\n"
    nets = parse_wifi_list(txt)
    assert [n.ssid for n in nets] == ["HomeWiFi", "Cafe:Guest"]
    assert nets[0].active and nets[0].signal == 82
    assert nets[1].security == ""


def test_parse_active() -> None:
    assert parse_active("lo:loopback:lo\nHomeWiFi:802-11-wireless:wlan0\n") == ("HomeWiFi", "wlan0")
    assert parse_active("") == (None, None)


@pytest.fixture()
async def client(tmp_config):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def test_backup_restore_roundtrip(client: AsyncClient) -> None:
    r = await client.post("/api/alarms", json={"label": "Backup me", "time": "05:45", "repeat": "daily", "source": "chime:birds"})
    assert r.status_code == 201
    await client.post("/api/presets", json={"label": "P1", "source": "chime:birds"})
    await client.patch("/api/config", json={"general": {"name": "Saved"}})
    b = (await client.get("/api/system/backup")).json()
    assert b["dawn_backup"] == 1 and len(b["db"]["alarms"]) == 1 and b["config"]["general"]["name"] == "Saved"
    # wipe and restore
    aid = b["db"]["alarms"][0]["id"]
    await client.delete(f"/api/alarms/{aid}")
    await client.patch("/api/config", json={"general": {"name": "Other"}})
    assert (await client.get("/api/alarms")).json() == []
    r = await client.post("/api/system/restore", json=b)
    assert r.status_code == 200 and r.json()["restored"]["alarms"] == 1
    alarms = (await client.get("/api/alarms")).json()
    assert len(alarms) == 1 and alarms[0]["label"] == "Backup me"
    assert (await client.get("/api/config")).json()["general"]["name"] == "Saved"
    presets = (await client.get("/api/presets")).json()
    assert [p["label"] for p in presets] == ["P1"]
    r = await client.post("/api/system/restore", json={"nope": 1})
    assert r.status_code == 422
