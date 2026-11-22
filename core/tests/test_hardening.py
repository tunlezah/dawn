"""Other hardening (robustness plan WP-H): shutdown with an alarm pending (H1), the real-time clock in Diagnostics (H2),
the evening readiness check (H6), the outside heartbeat (H7) and the WebSocket fan-out (H8). The DAB time guard (H3)
is tested in dawn-timed, the LAN security (H5) in test_lan_security.py."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from test_diagnostics import by_id, time_facts

from dawn_core.alarms import evening
from dawn_core.alarms.service import AlarmService
from dawn_core.api.ws import WsHub
from dawn_core.app import create_app
from dawn_core.config import ConfigManager
from dawn_core.diagnostics import timepath
from dawn_core.state.ui import NextAlarm, RingingInfo
from dawn_core.system import power
from dawn_core.system.sysinfo import SysInfoService


@pytest.fixture()
async def app(tmp_config):
    a = create_app(ConfigManager(tmp_config, poll_s=10))
    async with a.router.lifespan_context(a):
        a.state.ctx.svc(AlarmService)._task.cancel()  # the engine is driven by hand
        yield a


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t", headers={"X-Dawn-Client": "1"})


def _next(ctx, minutes: float, label: str = "Work") -> None:
    at = ctx.store.now() + timedelta(minutes=minutes)
    ctx.store.state.alarms.next = NextAlarm(id=1, label=label, at=at.isoformat(timespec="seconds"), in_seconds=int(minutes * 60))


# ---- H1: shutdown --------------------------------------------------------------------------------------------------
async def test_shutting_down_before_an_alarm_needs_a_confirmation(app) -> None:
    ctx = app.state.ctx
    _next(ctx, 120)
    async with _client(app) as c:
        r = await c.post("/api/system/shutdown")
        assert r.status_code == 409 and "Work alarm at" in r.json()["detail"] and "would not ring" in r.json()["detail"]
        r = await c.post("/api/system/shutdown", json={"confirm": True})
        assert r.status_code == 200  # (the simulator only says it would power off)
        _next(ctx, 30 * 60)  # more than a day away: no question
        assert (await c.post("/api/system/shutdown")).status_code == 200


async def test_never_while_ringing(app) -> None:
    ctx = app.state.ctx
    ctx.store.state.alarms.ringing = RingingInfo(kind="alarm", label="Work", started_at="x", source="chime:birds")
    async with _client(app) as c:
        r = await c.post("/api/system/shutdown", json={"confirm": True})
        assert r.status_code == 409 and "ringing" in r.json()["detail"]
    with pytest.raises(power.ShutdownRefused):
        await power.shutdown(ctx, confirmed=True)
    ctx.store.state.alarms.ringing = None


async def test_a_pi_5_sets_its_rtc_to_wake_before_the_alarm(app, tmp_path, monkeypatch) -> None:
    ctx = app.state.ctx
    wake = tmp_path / "wakealarm"
    wake.write_text("")
    monkeypatch.setattr(power, "WAKEALARM", wake)
    monkeypatch.setattr(power, "can_wake", lambda c: True)
    ran = []

    async def run(c, *args):
        ran.append(args)
        return 0

    monkeypatch.setattr(power, "_run", run)
    monkeypatch.setattr(ctx, "sim", False)
    _next(ctx, 180)
    at = datetime.fromisoformat(ctx.store.state.alarms.next.at)
    await power.shutdown(ctx, confirmed=True, source="test")
    assert int(wake.read_text()) == int((at - power.WAKE_EARLY).timestamp())
    assert ran == [("systemctl", "poweroff")]
    assert "switch itself on at" in ctx.store.state.face.message.body


# ---- H2: the real-time clock in the time chain ---------------------------------------------------------------------
@pytest.mark.parametrize(("name", "status", "text"), [
    ("rtc-ds1307 1-0068", "ok", "DS3231 present"),
    ("rpi-rtc", "info", "battery"),
    (None, "info", "None: after a power cut"),
])
def test_the_rtc_is_reported(name, status, text) -> None:
    c = by_id(timepath.checks(time_facts(rtc=name)))["time.rtc"]
    assert c.status == status and text in c.detail


# ---- H6: the evening check -----------------------------------------------------------------------------------------
async def test_when_the_evening_check_runs(app) -> None:
    ctx = app.state.ctx
    day = ctx.store.now().replace(hour=12, minute=0, second=0, microsecond=0)
    assert evening.check_time(ctx, day).strftime("%H:%M") == "21:30"  # sleep mode is off in the test config
    await ctx.cfg_mgr.update({"display": {"sleep": {"enabled": True, "start": "22:30"}}})
    assert evening.check_time(ctx, day).strftime("%H:%M") == "22:00"  # half an hour before bedtime
    assert not evening.due(ctx, day.replace(hour=21, minute=59), None)
    assert evening.due(ctx, day.replace(hour=22, minute=1), None)
    assert not evening.due(ctx, day.replace(hour=22, minute=1), day.date().isoformat())  # once a day
    assert not evening.due(ctx, day.replace(hour=0, minute=30) + timedelta(days=1), None)  # missed by hours: tomorrow


async def test_a_chime_alarm_is_ready_and_a_dab_alarm_says_what_it_will_start_on(app, monkeypatch) -> None:
    ctx = app.state.ctx
    a: AlarmService = ctx.svc(AlarmService)
    from dawn_core.alarms.schemas import AlarmIn
    from dawn_core.dab.service import DabService

    now = ctx.store.now()
    row = a.create(AlarmIn(label="Chime", time=(now + timedelta(hours=8)).strftime("%H:%M"), repeat="daily", source="chime:birds"), now=now)
    ctx.store.state.alarms.next = NextAlarm(id=row.id, label="Chime", at=(now + timedelta(hours=8)).isoformat(), in_seconds=8 * 3600)
    res = await evening.run(ctx, a, now)
    assert res is not None and res.ok is True
    a.update(row.id, AlarmIn(label="Radio", time=(now + timedelta(hours=8)).strftime("%H:%M"), repeat="daily", source="dab:1002"), now=now)
    dab: DabService = ctx.svc(DabService)
    asked = []

    async def prepare_for(sid, *, may_retune, occurrence=None):
        asked.append(may_retune)
        return "fail", "no DAB signal on 9A"

    monkeypatch.setattr(dab, "prepare_for", prepare_for)
    res = await evening.run(ctx, a, now)
    assert res.ok is False and res.message == "will start on the chime: no DAB signal on 9A" and asked == [False]  # never a retune
    dab.paused = "cool mode"
    res = await evening.run(ctx, a, now)
    assert res.ok is None and "cool mode" in res.message and asked == [False]  # not woken for a check
    dab.paused = None
    ctx.store.state.alarms.next = NextAlarm(id=row.id, label="Radio", at=(now + timedelta(hours=20)).isoformat(), in_seconds=20 * 3600)
    assert await evening.run(ctx, a, now) is None  # more than 14 hours away


async def test_the_engine_runs_it_once_and_drops_it_after_the_alarm(app, monkeypatch) -> None:
    ctx = app.state.ctx
    a: AlarmService = ctx.svc(AlarmService)
    calls = []

    async def fake_run(c, alarms, now):
        calls.append(now)
        from dawn_core.state.ui import EveningCheck

        nxt = c.store.state.alarms.next
        return EveningCheck(alarm_id=nxt.id, label=nxt.label, at=nxt.at, ok=True, checked_at=now.isoformat())

    monkeypatch.setattr(evening, "run", fake_run)
    now = ctx.store.now().replace(hour=21, minute=40, second=0, microsecond=0)
    ctx.store.state.alarms.next = NextAlarm(id=1, label="Work", at=(now + timedelta(hours=9)).isoformat(), in_seconds=9 * 3600)
    a._maybe_evening_check(now)
    await asyncio.wait([a._evening_task], timeout=2)
    assert ctx.store.state.alarms.evening_check.ok is True and len(calls) == 1
    a._maybe_evening_check(now + timedelta(minutes=5))
    assert len(calls) == 1
    ctx.store.state.alarms.next = None  # it rang
    a._maybe_evening_check(now + timedelta(hours=10))
    assert ctx.store.state.alarms.evening_check is None


# ---- H7: the outside heartbeat ---------------------------------------------------------------------------------------
async def test_the_heartbeat_url_is_called_every_5_minutes_only_when_set(app, monkeypatch) -> None:
    ctx = app.state.ctx
    si: SysInfoService = ctx.svc(SysInfoService)
    sent = []

    async def ping(url, status, msg):
        sent.append((url, status, msg))

    monkeypatch.setattr(si, "_ping", ping)
    si._heartbeat_url(True, now=1000.0)
    await asyncio.sleep(0)
    assert sent == []  # nothing is sent anywhere unless the owner sets a URL
    await ctx.cfg_mgr.update({"system": {"heartbeat_url": "https://hc.example/ping/abc"}})
    _next(ctx, 60)
    si._heartbeat_url(True, now=1000.0)
    await asyncio.sleep(0)
    si._heartbeat_url(True, now=1200.0)
    await asyncio.sleep(0)
    assert len(sent) == 1 and sent[0][1] == "ok" and "alarms_alive=yes" in sent[0][2] and "next=" in sent[0][2]
    si._heartbeat_url(False, now=1301.0)
    await asyncio.sleep(0)
    assert len(sent) == 2 and sent[1][1] == "fail" and "alarms_alive=no" in sent[1][2]


# ---- H8: the WebSocket fan-out ---------------------------------------------------------------------------------------
class FakeWs:
    def __init__(self, delay: float) -> None:
        self.delay, self.got = delay, []

    async def send_text(self, msg: str) -> None:
        await asyncio.sleep(self.delay)
        self.got.append(time.monotonic())


async def test_one_slow_phone_does_not_hold_the_face_back(monkeypatch) -> None:
    import dawn_core.api.ws as ws

    monkeypatch.setattr(ws, "SEND_TIMEOUT_S", 0.5)
    hub = WsHub()
    slow, face, hung = FakeWs(0.3), FakeWs(0.0), FakeWs(5.0)
    for w in (slow, face, hung):
        hub.clients.add(w)  # type: ignore[arg-type]
        hub.info[w] = {}  # type: ignore[index]
    t0 = time.monotonic()
    await hub.broadcast({"x": 1})
    assert face.got and face.got[0] - t0 < 0.1  # not behind the slow one
    assert slow.got and time.monotonic() - t0 < 1.0  # all at once: one budget, not one per client
    assert hung not in hub.clients and slow in hub.clients and face in hub.clients
