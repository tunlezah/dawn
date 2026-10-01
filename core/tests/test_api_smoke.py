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
