"""Watchdog coverage outside the loops (robustness plan WP-D2..D5): the units' limits, the stop budget, a frozen kiosk,
the kiosk's daily refresh, a hung chronyd or gpsd, and the optional Wi-Fi driver recovery."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from test_supervisor import restarts, sv, with_face  # noqa: F401  (sv is the fixture)

from dawn_core.services import Service, ServiceRegistry
from dawn_core.system import supervisor as sup
from dawn_core.timesync.gps import GpsdClient
from dawn_core.timesync.service import TimeSourceService

SYD = ZoneInfo("Australia/Sydney")
DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def _reasons(ctx, what: str) -> list[str]:
    return [e["reason"] for e in ctx.db.recent_events(100) if e["kind"] == "supervisor_repair" and e["what"] == what]


# ---- D2: units and the stop budget ---------------------------------------------------------------------------------
def test_the_units_set_memory_priorities_and_stop_limits() -> None:
    core = {ln.strip() for ln in (DEPLOY / "systemd" / "dawn-core.service").read_text().splitlines()}
    assert {"OOMScoreAdjust=-800", "LimitCORE=0", "TimeoutStopSec=45", "WatchdogSec=60"} <= core
    face = {ln.strip() for ln in (DEPLOY / "systemd" / "dawn-face.service").read_text().splitlines()}
    assert "OOMScoreAdjust=500" in face
    for unit in ("chrony", "gpsd"):
        drop = {ln.strip() for ln in (DEPLOY / "systemd" / f"{unit}.service.d" / "dawn.conf").read_text().splitlines()}
        assert {"StartLimitIntervalSec=0", "Restart=always", "RestartSec=5"} <= drop
    assert 'chrony.service.d/dawn.conf' in (DEPLOY / "install.sh").read_text().replace('$u', 'chrony')


async def test_stopping_keeps_to_its_budget_and_a_hung_service_does_not_block_the_rest() -> None:
    stopped = []

    class Hangs(Service):
        name = "hangs"

        async def stop(self) -> None:
            await asyncio.sleep(3600)

    class Quick(Service):
        def __init__(self, n: str) -> None:
            self.name = n

        async def stop(self) -> None:
            stopped.append(self.name)

    reg = ServiceRegistry()
    reg.add(Quick("first"))
    reg.add(Hangs())
    reg.add(Quick("last"))
    t0 = time.monotonic()
    await reg.stop_all(budget_s=1.0)
    assert time.monotonic() - t0 < 2.0
    assert stopped == ["last", "first"]  # reverse order, and the one after the hang still stopped


# ---- D3: the kiosk ---------------------------------------------------------------------------------------------------
async def test_a_face_that_pings_but_draws_no_frames_is_restarted(sv) -> None:  # noqa: F811
    s, ctx, host = sv
    with_face(host)
    hub = ctx.ws_hub
    hub.info["ws"] = {"role": "face", "last_seen": time.monotonic(), "raf_age_ms": 2000.0, "connected_at": "x", "remote": None, "agent": ""}
    t = 1000.0
    await s.check(now=t)
    await s.check(now=t + s.cfg.face_absent_s + 5)
    assert restarts(host) == []  # pinging and drawing: fine
    hub.info["ws"]["raf_age_ms"] = 90_000.0  # JavaScript runs, the compositor is stuck
    hub.info["ws"]["last_seen"] = time.monotonic()
    await s.check(now=t + 2 * s.cfg.face_absent_s)
    assert restarts(host) == []  # first seen frozen: it gets face_absent_s
    hub.info["ws"]["last_seen"] = time.monotonic()
    await s.check(now=t + 3 * s.cfg.face_absent_s + 1)
    assert restarts(host) == ["dawn-face"]
    assert "frozen" in _reasons(ctx, "dawn-face")[0]


def test_the_kiosk_script_uses_its_own_profile_and_clears_a_stale_lock() -> None:
    script = (DEPLOY / "bin" / "dawn-face").read_text()
    assert '--user-data-dir="$PROFILE"' in script and 'Singleton{Lock,Cookie,Socket}' in script
    assert "tmpfs" not in script.split("PROFILE=")[1].split("\n")[0]  # on the card: the backup alarm's memory lives there


def test_when_the_daily_refresh_is_due() -> None:
    d = datetime(2026, 11, 22, 3, 0, tzinfo=SYD)
    assert sup.recycle_due(d, "03:30", None) is None  # not yet
    assert sup.recycle_due(d.replace(minute=31), "03:30", None) == "due"
    assert sup.recycle_due(d.replace(hour=5, minute=29), "03:30", None) == "due"
    assert sup.recycle_due(d.replace(hour=5, minute=31), "03:30", None) == "missed"  # never quiet: skipped today
    assert sup.recycle_due(d.replace(minute=40), "03:30", d.date()) is None  # done today


def _state(ctx, **over):
    st = ctx.store.state
    st.face.mode = over.get("mode", "sleep")
    st.audio.active_source = over.get("source", "none")
    st.alarms.ringing = None
    st.timers.nap = None
    st.alarms.light_wake_active = False
    st.alarms.next = None
    return st


async def test_the_daily_refresh_waits_for_a_quiet_face(sv, monkeypatch) -> None:  # noqa: F811
    s, ctx, host = sv
    with_face(host)
    hub = ctx.ws_hub
    hub.info["ws"] = {"role": "face", "last_seen": time.monotonic(), "raf_age_ms": 100.0, "connected_at": "x", "remote": None, "agent": ""}
    from dawn_core.state.ui import NextAlarm

    clock = {"now": datetime(2026, 11, 22, 3, 35, tzinfo=SYD)}
    monkeypatch.setattr(ctx.store, "now", lambda: clock["now"])
    host.active_for = 7 * 3600.0
    st = _state(ctx, source="dab")  # the radio is on
    t = 1000.0
    await s.check(now=t)
    assert restarts(host) == []
    st.audio.active_source = "none"
    st.alarms.next = NextAlarm(id=1, label="Early", at=(clock["now"] + timedelta(minutes=50)).isoformat(), in_seconds=3000)
    await s.check(now=t + 20)
    assert restarts(host) == []  # an alarm within the hour
    st.alarms.next = NextAlarm(id=1, label="Later", at=(clock["now"] + timedelta(hours=3)).isoformat(), in_seconds=3 * 3600,
                               light_wake_at=(clock["now"] + timedelta(minutes=40)).isoformat())
    await s.check(now=t + 40)
    assert restarts(host) == []  # its light wake counts
    st.alarms.next = None
    host.active_for = 3600.0
    await s.check(now=t + 60)
    assert restarts(host) == []  # the kiosk is only an hour old
    host.active_for = 7 * 3600.0
    clock["now"] = clock["now"] + timedelta(minutes=10)
    await s.check(now=t + 80)
    assert restarts(host) == ["dawn-face"] and _reasons(ctx, "dawn-face") == ["daily refresh"]
    clock["now"] = clock["now"] + timedelta(minutes=20)
    await s.check(now=t + 3000)
    assert restarts(host) == ["dawn-face"]  # once a day


async def test_a_day_with_no_quiet_moment_is_skipped(sv, monkeypatch) -> None:  # noqa: F811
    s, ctx, host = sv
    with_face(host)
    ctx.ws_hub.info["ws"] = {"role": "face", "last_seen": time.monotonic(), "connected_at": "x", "remote": None, "agent": ""}
    clock = {"now": datetime(2026, 11, 22, 4, 0, tzinfo=SYD)}
    monkeypatch.setattr(ctx.store, "now", lambda: clock["now"])
    host.active_for = 7 * 3600.0
    _state(ctx, mode="playing", source="airplay")
    await s.check(now=1000.0)
    clock["now"] = datetime(2026, 11, 22, 5, 31, tzinfo=SYD)
    _state(ctx)
    await s.check(now=1100.0)
    assert restarts(host) == [] and s._recycled_on == clock["now"].date()


# ---- D4: hung chronyd and gpsd --------------------------------------------------------------------------------------
async def test_a_chronyd_that_stops_answering_is_restarted(sv) -> None:  # noqa: F811
    s, ctx, host = sv
    ts: TimeSourceService = ctx.svc(TimeSourceService)
    ts.chronyc_timeouts = 3
    await s.check(now=1000.0)
    assert restarts(host) == []
    ts.chronyc_timeouts = 4  # two minutes of chronyc timing out
    await s.check(now=1015.0)
    assert restarts(host) == ["chrony"] and _reasons(ctx, "chrony") == ["chronyc timed out 4 times in a row"]


async def test_chronyc_timeouts_are_counted_per_poll(sv, monkeypatch) -> None:  # noqa: F811
    s, ctx, host = sv
    ts: TimeSourceService = ctx.svc(TimeSourceService)
    monkeypatch.setattr(ctx, "sim", False)

    class Hung:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(3600)

        def kill(self) -> None:
            pass

    async def spawn(*a, **k):
        return Hung()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(asyncio, "wait_for", _instant_timeout)
    for _ in range(4):
        await ts.poll()
    assert ts.chronyc_timeouts == 4


async def _instant_timeout(aw, timeout):
    aw.close()
    raise TimeoutError


async def test_gpsd_that_lost_a_fitted_receiver_is_restarted(sv) -> None:  # noqa: F811
    s, ctx, host = sv
    ts: TimeSourceService = ctx.svc(TimeSourceService)
    c = GpsdClient("127.0.0.1", 9, lambda f: None)
    c.connected = True
    c.fix.last_tpv_at = time.time() - 30
    old_gps, ts.gps, ts.presence = ts.gps, c, "present"
    try:
        await s.check(now=1000.0)
        assert restarts(host) == []
        c.fix.last_tpv_at = time.time() - 130
        await s.check(now=1015.0)
        assert restarts(host) == ["gpsd"] and "no position report" in _reasons(ctx, "gpsd")[0]
        ts.presence = "absent"  # no receiver: a quiet gpsd is expected
        await s.check(now=1015.0 + 4000)
        assert restarts(host) == ["gpsd"]
    finally:
        ts.gps = old_gps


# ---- D5: the optional Wi-Fi driver recovery ------------------------------------------------------------------------
async def test_a_missing_wifi_adapter_restarts_networkmanager_twice_at_most(sv, monkeypatch) -> None:  # noqa: F811
    s, ctx, host = sv
    net = ctx.store.state.system.network
    net.wifi_configured, net.online = True, False

    async def missing(path: str) -> bool:
        return False

    monkeypatch.setattr(host, "exists", missing)
    t = 1000.0
    await s.check(now=t)
    await s.check(now=t + 700)
    assert restarts(host) == []  # off by default
    s.cfg.wifi_driver_recovery = True
    await s.check(now=t + 800)
    await s.check(now=t + 800 + sup.WIFI_DRIVER_S + 1)
    assert restarts(host) == ["NetworkManager"]
    host.states["NetworkManager"] = "active"
    for k in range(1, 8):
        await s.check(now=t + 800 + k * (sup.WIFI_DRIVER_S + s.cfg.max_backoff_s))
    assert restarts(host) == ["NetworkManager", "NetworkManager"]  # never a third time, and never a reboot
    from dawn_core.diagnostics import system

    facts = await system.collect(ctx, host)
    checks = {c.id: c for c in system.checks(facts)}
    assert checks["network.wifi_driver"].status == "fail" and "Reboot" in (checks["network.wifi_driver"].hint or "")
