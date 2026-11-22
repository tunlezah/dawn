"""GPS is optional: a box with no module never shows GPS as a problem. It is "not fitted" (its own status, a hollow
yellow ring), a module that was there and has gone is "missing" (a warning), and `required: true` makes either a fault.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from test_diagnostics import by_id, gps_facts, time_facts

from dawn_core.app import create_app
from dawn_core.config import ConfigManager
from dawn_core.diagnostics import gps, timepath
from dawn_core.diagnostics.checks import RANK, summarise
from dawn_core.timesync.gps import GpsdClient, GpsFix, SerialNmeaClient
from dawn_core.timesync.presence import PresenceInputs, gps_presence
from dawn_core.timesync.service import LAST_SEEN_KEY, TimeSourceService

NOW = datetime(2026, 11, 22, 3, 0, tzinfo=UTC)


# ---- the pure presence function ------------------------------------------------------------------
@pytest.mark.parametrize(("inputs", "want"), [
    (PresenceInputs(False, usb_ublox=True), "off"),
    (PresenceInputs(True, usb_ublox=True), "present"),
    (PresenceInputs(True, gps0=True), "present"),
    (PresenceInputs(True, gpsd_device=True), "present"),
    (PresenceInputs(True, nmea_age_s=3.0), "present"),
    (PresenceInputs(True, nmea_age_s=31.0), "absent"),  # NMEA that stopped half a minute ago is not a receiver now
    (PresenceInputs(True), "absent"),
    (PresenceInputs(True, last_seen_at=NOW - timedelta(days=13)), "missing"),
    (PresenceInputs(True, last_seen_at=NOW - timedelta(days=15)), "absent"),  # forgotten after forget_after_days
])
def test_presence(inputs: PresenceInputs, want: str) -> None:
    assert gps_presence(inputs, NOW, 14) == want


# ---- checks ------------------------------------------------------------------------------------------
def test_absent_is_one_not_fitted_check_and_never_a_problem() -> None:
    c = gps.checks(gps_facts(presence="absent", usb=[], paths={"/dev/gps0": False}))
    assert [(x.id, x.status) for x in c] == [("gps.receiver", "absent")]
    assert "No GPS module fitted" in c[0].detail and "picked up automatically" in (c[0].hint or "")
    s = summarise(c)
    assert s["problems"] == [] and s["counts"]["absent"] == 1
    assert RANK["warn"] < RANK["absent"] < RANK["info"]


def test_missing_is_a_warning_with_restart_and_forget() -> None:
    c = by_id(gps.checks(gps_facts(presence="missing", last_seen_age_s=3 * 3600.0)))
    assert c["gps.receiver"].status == "warn" and "last seen" in c["gps.receiver"].detail
    assert c["gps.receiver"].actions == ["gps.restart", "gps.forget"]
    assert len(c) == 1


def test_present_runs_the_whole_chain() -> None:
    c = by_id(gps.checks(gps_facts(presence="present")))
    assert c["gps.receiver"].status == "ok" and c["gps.fix"].status == "ok" and "gps.signal" in c


def test_off_is_off() -> None:
    c = gps.checks(gps_facts(enabled=False, presence="off"))
    assert [(x.id, x.status) for x in c] == [("gps.enabled", "off")]


def test_required_makes_absent_and_missing_faults() -> None:
    assert by_id(gps.checks(gps_facts(presence="absent", required=True)))["gps.receiver"].status == "fail"
    assert by_id(gps.checks(gps_facts(presence="missing", required=True)))["gps.receiver"].status == "fail"


def test_time_chain_collapses_without_a_module() -> None:
    f = time_facts(gps={"enabled": True, "has_fix": False, "unit": "active", "presence": "absent"}, shm=[{"unit": 2, "perms": "666", "nattch": 2}])
    c = by_id(timepath.checks(f))
    assert c["time.gps.source"].status == "off" and "no GPS module" in c["time.gps.source"].detail
    assert "time.gps.fix" not in c and "time.gps.shm" not in c  # no red "No GPS fix", no amber "no SHM 0"
    f["gps"] = {"enabled": False, "has_fix": False, "unit": None, "presence": "off"}
    assert by_id(timepath.checks(f))["time.gps.source"].status == "off"
    f["gps"] = {"enabled": True, "has_fix": False, "unit": "active", "presence": "missing"}
    c = by_id(timepath.checks(f))
    assert c["time.gps.source"].status == "warn" and "time.gps.fix" not in c


# ---- the service ---------------------------------------------------------------------------------------
class FakeHost:
    def __init__(self) -> None:
        self.usb = False
        self.paths: set[str] = set()

    async def gps_on_usb(self) -> bool:
        return self.usb

    async def exists(self, path: str) -> bool:
        return path in self.paths


@pytest.fixture()
async def ts(tmp_config):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        ctx = app.state.ctx
        t: TimeSourceService = ctx.svc(TimeSourceService)
        t._task.cancel()
        if t.gps:
            await t.gps.stop()
        t.gps = None
        if t._presence_task:
            t._presence_task.cancel()
        ctx.db.set(LAST_SEEN_KEY, None)
        # the first look at start may have reached a simulator left running (its fake gpsd on 2947): start clean
        t.presence, t._presence_known, t._last_seen_written = "absent", False, None
        host = FakeHost()
        t._host = host
        yield t, ctx, host


def _events(ctx):
    return [e for e in ctx.db.recent_events(100) if e["kind"] == "gps_presence"]


async def test_gpsd_connected_without_a_device_is_absent(ts) -> None:
    t, ctx, host = ts
    c = GpsdClient("127.0.0.1", 9, lambda f: None)
    c.connected = True  # gpsd answers (socket activation) but has no receiver: GpsInfo.available would say True
    t.gps = c
    assert await t.check_presence(now=1.0, wall=NOW) == "absent"
    assert ctx.store.state.time_sources.gps.presence == "absent"
    c.fix = GpsFix(activated="2026-11-22T03:00:00.000Z", driver="u-blox")
    assert await t.check_presence(now=2.0, wall=NOW) == "present"


async def test_usb_only_and_gps0_only_are_present(ts) -> None:
    t, ctx, host = ts
    host.usb = True
    assert await t.check_presence(now=1.0, wall=NOW) == "present"
    host.usb = False
    host.paths = {"/dev/gps0"}
    assert await t.check_presence(now=2.0, wall=NOW) == "present"


async def test_serial_nmea_only_is_present_and_a_bare_ttyacm0_is_not(ts) -> None:
    t, ctx, host = ts
    host.paths = {"/dev/ttyACM0"}  # any CDC device: not evidence
    s = SerialNmeaClient("/dev/ttyACM0", 9600, lambda f: None)
    t.gps = s
    assert await t.check_presence(now=1.0, wall=NOW) == "absent"
    import time as _t

    s.fix.last_msg_at = _t.time() - 2
    assert await t.check_presence(now=2.0, wall=NOW) == "present"


async def test_unplugged_is_missing_until_forgotten_or_expired(ts) -> None:
    t, ctx, host = ts
    host.usb = True
    await t.check_presence(now=1.0, wall=NOW)
    assert ctx.db.get(LAST_SEEN_KEY) == NOW.isoformat(timespec="seconds")
    before = len(_events(ctx))
    host.usb = False
    assert await t.check_presence(now=40.0, wall=NOW + timedelta(seconds=40)) == "missing"
    gi = ctx.store.state.time_sources.gps
    assert gi.presence == "missing" and gi.last_seen_at == NOW.isoformat(timespec="seconds")
    assert [e["presence"] for e in _events(ctx)][: len(_events(ctx)) - before] == ["missing"]  # the change is logged
    # left unplugged past forget_after_days
    assert await t.check_presence(now=80.0, wall=NOW + timedelta(days=15)) == "absent"
    # or forgotten by hand
    host.usb = True
    await t.check_presence(now=100.0, wall=NOW + timedelta(days=16))
    host.usb = False
    assert await t.check_presence(now=200.0, wall=NOW + timedelta(days=16, minutes=1)) == "missing"
    t.forget_gps()
    assert ctx.db.get(LAST_SEEN_KEY) is None and ctx.store.state.time_sources.gps.presence == "absent"
    assert await t.check_presence(now=300.0, wall=NOW + timedelta(days=16, minutes=2)) == "absent"


async def test_last_seen_is_written_at_most_once_a_minute(ts) -> None:
    t, ctx, host = ts
    host.usb = True
    await t.check_presence(now=0.0, wall=NOW)
    await t.check_presence(now=30.0, wall=NOW + timedelta(seconds=30))
    assert ctx.db.get(LAST_SEEN_KEY) == NOW.isoformat(timespec="seconds")
    await t.check_presence(now=61.0, wall=NOW + timedelta(seconds=61))
    assert ctx.db.get(LAST_SEEN_KEY) == (NOW + timedelta(seconds=61)).isoformat(timespec="seconds")


async def test_disabled_is_off(ts) -> None:
    t, ctx, host = ts
    host.usb = True
    await ctx.cfg_mgr.update({"time_sources": {"gps": {"enabled": False}}})
    t._host = host
    assert await t.check_presence(now=1.0, wall=NOW) == "off"
