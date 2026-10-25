"""Regression tests for the second reliability assessment (docs/RELIABILITY-ASSESSMENT.md; fixed in 0.2.4): a stamp
without a time zone, a sleep fade overlapping the ring, a preparation that hangs, missed_grace_minutes: 0, and a
read-only open that cannot read the write-ahead log."""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dawn_core.alarms import service as alarm_module
from dawn_core.alarms.schemas import AlarmIn
from dawn_core.alarms.service import AlarmService
from dawn_core.alarms.timers import TimerService
from dawn_core.app import create_app
from dawn_core.audio.service import AudioService
from dawn_core.config import ConfigManager
from dawn_core.dab import service as dab_module
from dawn_core.dab.service import DabService
from dawn_core.db.engine import Database
from dawn_core.db.models import AlarmRow
from dawn_core.diagnostics import system

SYD = ZoneInfo("Australia/Sydney")
BEFORE = datetime(2026, 10, 1, 12, 0, tzinfo=SYD)
AT = datetime(2026, 10, 2, 6, 30, 3, tzinfo=SYD)

# the facts the Diagnostics checks read, as test_alarm_support builds them
FACTS = {
    "units": {}, "audio": {"backend": "pipewire", "sink": None, "sinks": [], "pinned": None, "volume": 40, "muted": False,
                           "active_source": "none", "flowing": False, "eq_present": True, "ceiling": 100, "max_volume": 100},
    "alarms": {"engine_alive": True, "prepare": None, "ringing": None, "backup_tone_24h": [], "missed_24h": [], "restored_24h": [], "backup_tone_problem": None, "gpio_buzzer": None},
    "airplay": {"enabled": False}, "bluetooth": {"enabled": False},
    "display": {"face_clients": [{"connected_at": "2026-10-06T22:00:00"}], "face_mode": "standby", "backlight": "sysfs", "sysfs": "x", "sysfs_writable": True,
                "brightness": 15, "mode": "auto", "sensor_found": True, "sensor": "veml6030", "lux": 0.4, "night": True},
    "inputs": {"encoder": False, "button": False, "backend": None, "last": None},
    "weather": {"enabled": False},
    "system": {"config_error": None, "failed_services": {}, "throttled": None, "throttle_flags": [], "cpu_temp_c": None, "mem_used_percent": None,
               "disk_data": None, "disk_root": None, "heartbeat_age_s": 1, "error_count": 0, "errors": [], "version": "0.2.4", "git_rev": None,
               "model": "Raspberry Pi 4", "uptime_s": 60, "sim": False, "db_write_errors": 0, "db_last_write_error": None},
}


@pytest.fixture()
async def svc(tmp_config):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        ctx = app.state.ctx
        a: AlarmService = ctx.svc(AlarmService)
        a._task.cancel()  # drive ticks by hand
        yield a, ctx


def _events(ctx, kind):
    return [e for e in ctx.db.recent_events(300) if e["kind"] == kind]


# ---- 1. a stamp without a time zone ----------------------------------------------------------------------------
async def test_a_stamp_without_a_zone_still_rings(svc) -> None:
    a, ctx = svc
    with ctx.db.session() as s:  # a hand edit of the database, or a backup edited by hand: no offset on the stamp
        s.add(AlarmRow(label="Naive", time="06:30", repeat="daily", source="chime:birds", ramp_seconds=0, enabled=True, last_fired_occurrence="2026-10-01T06:30:00"))
        s.commit()
    await a.tick(now=AT)
    assert a.ring is not None and a.ring.req.label == "Naive"
    assert ctx.store.state.alarms.items[0].problem is None
    await a.stop_ringing()
    for sec in (5, 30):
        await a.tick(now=AT + timedelta(seconds=sec))
    assert a.ring is None and len(_events(ctx, "alarm_fire")) == 1  # the stamp written on firing holds it


async def test_a_row_the_engine_cannot_evaluate_is_shown_as_broken_never_as_scheduled(svc) -> None:
    a, ctx = svc
    with ctx.db.session() as s:
        s.add(AlarmRow(label="Broken", time="25:99", repeat="daily", source="chime:birds", enabled=True, last_fired_occurrence=BEFORE.isoformat()))
        s.commit()
    good = a.create(AlarmIn(label="Good", time="07:00", repeat="daily", source="chime:birds"), now=BEFORE)
    await a.tick(now=BEFORE + timedelta(hours=1))
    items = {i.label: i for i in ctx.store.state.alarms.items}
    assert items["Broken"].problem and items["Broken"].next_at is None and items["Broken"].enabled
    assert items["Good"].problem is None and items["Good"].next_at is not None
    assert ctx.store.state.alarms.next is not None and ctx.store.state.alarms.next.id == good.id
    out = {o.label: o for o in (a.to_out(r) for r in a.rows())}
    assert out["Broken"].problem and out["Broken"].next_at is None  # /api/alarms says so too
    c = {x.id: x for x in system.checks({**FACTS, "alarms": {**FACTS["alarms"], "broken": [{"id": items["Broken"].id, "label": "Broken", "problem": items["Broken"].problem}]}})}
    assert c["audio.alarm.broken"].status == "fail" and "Broken" in c["audio.alarm.broken"].detail
    # editing the row mends it
    a.update(items["Broken"].id, AlarmIn(label="Mended", time="07:30", repeat="daily", source="chime:birds"), now=BEFORE)
    await a.tick(now=BEFORE + timedelta(hours=1))
    assert all(i.problem is None and i.next_at for i in ctx.store.state.alarms.items)


# ---- 2. a sleep fade overlapping the ring ------------------------------------------------------------------------
async def test_a_sleep_fade_overlapping_the_ring_does_not_stop_the_ramp(svc) -> None:
    a, ctx = svc
    audio: AudioService = ctx.svc(AudioService)
    timers: TimerService = ctx.svc(TimerService)
    await audio.set_volume(20, overlay=False)  # listening at 20 before bed
    ctx.config.timers.sleep_fade_s = 2
    timers._sleep_total, timers._sleep_end = 3, ctx.store.now() + timedelta(seconds=3)
    timers._sleep_task = asyncio.create_task(timers._run_sleep())  # the real fade: 1 s wait, then 2 s fading to 0
    await asyncio.sleep(2.2)  # the fade has halved the volume
    await a.test_ring(source="chime:birds", label="Ramped", volume=70, ramp_seconds=8)
    await a.ring.settled()
    await asyncio.sleep(3.5)  # the fade is over meanwhile
    assert a.ring is not None and a.ring._ramp_task is not None and not a.ring._ramp_task.done()  # the ramp carried on
    assert 25 <= audio.volume < 70  # climbing from 10 towards 70, not stuck at the bedtime volume
    assert a.ring.prev_volume == 20  # and the volume from before the fade is what comes back
    assert timers._sleep_end is None
    await a.stop_ringing()
    assert audio.volume == 20


async def test_a_change_by_hand_still_stops_the_ramp(svc) -> None:
    a, ctx = svc
    audio: AudioService = ctx.svc(AudioService)
    await audio.set_volume(30, overlay=False)
    await a.test_ring(source="chime:birds", label="Knob", volume=80, ramp_seconds=10)
    await a.ring.settled()
    await asyncio.sleep(1.2)
    await audio.step_volume(-1)  # the encoder
    v = audio.volume
    await asyncio.sleep(1.5)
    assert audio.volume == v and a.ring._ramp_task.done()  # the knob wins
    await a.stop_ringing()


# ---- 3. a preparation that hangs -------------------------------------------------------------------------------
async def test_a_hung_preparation_is_given_up_on_and_the_next_alarm_is_prepared(svc, monkeypatch) -> None:
    a, ctx = svc
    dab: DabService = ctx.svc(DabService)

    async def hang(sid: str, *, may_retune: bool):
        await asyncio.sleep(3600)

    monkeypatch.setattr(dab, "prepare_for", hang)
    monkeypatch.setattr(alarm_module, "PREP_TIMEOUT_S", 0.5)
    ctx.store.state.dab.sdr_present = True
    radio = a.create(AlarmIn(label="Radio", time="06:33", repeat="daily", source="dab:1002"), now=BEFORE)
    await a.tick(now=AT)  # 3 min ahead: inside the preparation window
    await asyncio.sleep(0.9)
    assert a._prep_task is not None and a._prep_task.done()
    assert a._prep is not None and a._prep.alarm_id == radio.id and "did not finish" in a._prep.problems[0] and not a._prep.pending
    a.delete(radio.id or 0)
    chime = a.create(AlarmIn(label="Chime", time="06:32", repeat="daily", source="chime:birds"), now=BEFORE)
    await a.tick(now=AT + timedelta(seconds=1))
    await asyncio.sleep(0.3)
    assert a._prep is not None and a._prep.alarm_id == chime.id and a._prep.ready


async def test_a_decoder_restart_that_never_returns_is_given_up_on(svc, monkeypatch) -> None:
    a, ctx = svc
    dab: DabService = ctx.svc(DabService)

    class Stuck:
        returncode = None

        async def wait(self):
            await asyncio.sleep(3600)

        def kill(self):
            self.returncode = -9

    stuck = Stuck()

    async def spawn(*args, **kw):
        return stuck

    monkeypatch.setattr(dab_module.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(dab_module, "RESTART_TIMEOUT_S", 0.2)
    monkeypatch.setattr(ctx, "sim", False)
    t0 = time.monotonic()
    assert await dab.restart_welle(force=True) is True
    assert time.monotonic() - t0 < 2 and stuck.returncode == -9


# ---- 4. missed_grace_minutes: 0 --------------------------------------------------------------------------------
async def test_grace_zero_means_within_the_alarms_own_minute(svc) -> None:
    a, ctx = svc
    ctx.config.alarm_defaults.missed_grace_minutes = 0
    a.create(AlarmIn(label="G0", time="06:30", repeat="daily", source="chime:birds", ramp_seconds=0), now=BEFORE)
    await a.tick(now=AT)  # 3 s past the minute, as every tick is
    assert a.ring is not None and not _events(ctx, "alarm_missed")
    await a.stop_ringing()
    await a.tick(now=datetime(2026, 10, 3, 6, 31, 5, tzinfo=SYD))  # 65 s late: outside the alarm's own minute
    assert a.ring is None and len(_events(ctx, "alarm_missed")) == 1


async def test_grace_counts_whole_minutes(svc) -> None:
    a, ctx = svc
    ctx.config.alarm_defaults.missed_grace_minutes = 2
    a.create(AlarmIn(label="G2", time="06:30", repeat="daily", source="chime:birds", ramp_seconds=0), now=BEFORE)
    await a.tick(now=datetime(2026, 10, 2, 6, 32, 59, tzinfo=SYD))  # 2 min 59 s late: still inside
    assert a.ring is not None and a.ring.req.label == "G2"
    await a.stop_ringing()
    await a.tick(now=datetime(2026, 10, 3, 6, 33, 0, tzinfo=SYD))  # 3 min late: a miss
    assert a.ring is None and len(_events(ctx, "alarm_missed")) == 1


# ---- 5. a read-only open that cannot read the write-ahead log ----------------------------------------------------
def test_a_read_only_open_that_cannot_read_the_wal_says_so(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "dawn.db"
    code = textwrap.dedent(f'''
        import os, signal
        os.environ["DAWN_SIM"] = "1"
        from dawn_core.db.engine import Database
        from dawn_core.db.models import AlarmRow
        db = Database({str(path)!r})
        with db.session() as s:
            s.add(AlarmRow(label="old", time="06:30")); s.commit()
        with db.engine.connect() as c:
            c.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        with db.session() as s:
            s.add(AlarmRow(label="set last night", time="06:45")); s.commit()
        os.kill(os.getpid(), signal.SIGKILL)  # a crash (or the watchdog): nothing is checkpointed
    ''')
    subprocess.run([sys.executable, "-c", code], check=False, capture_output=True)
    assert (tmp_path / "dawn.db-wal").stat().st_size > 0
    real_open = Database._open

    def no_rw(self, url, *, create):
        if not url.startswith("sqlite:///file:"):
            raise sqlite3.OperationalError("unable to open database file")  # the card is read-only and no -shm can be made
        return real_open(self, url, create=create)

    monkeypatch.setattr(Database, "_open", no_rw)
    db = Database(path)
    assert db.mode == "ro" and db.stale and "checkpoint" in (db.open_error or "")
    monkeypatch.undo()
    assert Database(tmp_path / "fresh.db").stale is False


async def test_alarms_are_degraded_while_the_database_may_be_stale(svc) -> None:
    a, ctx = svc
    a.publish(force=True)
    assert ctx.store.state.alarms.degraded is False
    ctx.db.stale, ctx.db.open_error = True, "dawn.db-wal is present: changes since the last checkpoint are missing"
    a.publish(force=True)
    assert ctx.store.state.alarms.degraded is True and "checkpoint" in (ctx.store.state.alarms.degraded_reason or "")
    ctx.db.stale, ctx.db.open_error = False, None
    a.publish(force=True)
    assert ctx.store.state.alarms.degraded is False


def test_diagnostics_say_when_the_read_only_database_misses_recent_changes() -> None:
    f = {**FACTS, "system": {**FACTS["system"], "db_mode": "ro", "db_open_error": "unable to open database file", "db_stale": True, "db_path": "/var/lib/dawn/dawn.db"}}
    c = {x.id: x for x in system.checks(f)}
    assert c["system.database"].status == "fail" and "checkpoint" in c["system.database"].detail
    f["system"]["db_stale"] = False
    c = {x.id: x for x in system.checks(f)}
    assert "checkpoint" not in c["system.database"].detail
