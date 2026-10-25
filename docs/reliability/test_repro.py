"""Reproductions for the 0.2.3 reliability assessment (docs/RELIABILITY-ASSESSMENT.md). Each test passing means the
defect it describes is present; invert the assertions once a defect is fixed and move the test into core/tests.

Run from core/:  ../.venv/bin/python -m pytest -q -c pyproject.toml ../docs/reliability/test_repro.py
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

os.environ["DAWN_SIM"] = "1"

from dawn_core.alarms.schemas import AlarmIn  # noqa: E402
from dawn_core.alarms.service import AlarmService  # noqa: E402
from dawn_core.alarms.timers import TimerService  # noqa: E402
from dawn_core.app import create_app  # noqa: E402
from dawn_core.audio.service import AudioService  # noqa: E402
from dawn_core.config import ConfigManager  # noqa: E402
from dawn_core.dab.service import DabService  # noqa: E402
from dawn_core.db.engine import Database  # noqa: E402
from dawn_core.db.models import AlarmRow  # noqa: E402

SYD = ZoneInfo("Australia/Sydney")
BEFORE = datetime(2026, 10, 1, 12, 0, tzinfo=SYD)
AT = datetime(2026, 10, 2, 6, 30, 3, tzinfo=SYD)


@pytest.fixture()
def tmp_config(tmp_path: Path) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(
        "general:\n  timezone: Australia/Sydney\n  data_dir: %s\n  runtime_dir: %s\n"
        "audio:\n  backend: sim\ndisplay:\n  backlight:\n    driver: sim\n  lux_sensor:\n    driver: sim\n"
        "  sleep:\n    enabled: false\n"
        "inputs:\n  pin_factory: mock\ntime_sources:\n  gps:\n    source: sim\nsystem:\n  watchdog: false\n"
        "sim:\n  hub_url: http://127.0.0.1:9\ndab:\n  welle_url: http://127.0.0.1:9\n"
        % (tmp_path / "data", tmp_path / "run")
    )
    os.environ["DAWN_DATA_DIR"] = str(tmp_path / "data")
    os.environ["DAWN_RUNTIME_DIR"] = str(tmp_path / "run")
    return p


@pytest.fixture()
async def svc(tmp_config):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        ctx = app.state.ctx
        a: AlarmService = ctx.svc(AlarmService)
        a._task.cancel()  # drive ticks by hand
        yield a, ctx


# ---- 1. a stamp without a time zone: listed as scheduled, never rings ------------------------------------------
async def test_a_stamp_without_a_zone_is_listed_as_scheduled_but_never_rings(svc) -> None:
    a, ctx = svc
    with ctx.db.session() as s:
        # a hand edit, a foreign backup, or a row written by anything that drops the offset
        s.add(AlarmRow(label="Naive", time="06:30", repeat="daily", source="chime:birds", enabled=True, last_fired_occurrence="2026-10-01T06:30:00"))
        s.commit()
    for sec in range(0, 4):
        await a.tick(now=AT + timedelta(seconds=sec))
    assert a.ring is None, "the engine fired it (defect fixed?)"
    assert a.tick_failures == 0  # the tick as a whole is fine: the watchdog is fed, the face trusts core
    items = ctx.store.state.alarms.items
    assert [i.label for i in items] == ["Naive"] and items[0].enabled and items[0].next_at is not None  # the UI says it is armed
    assert ctx.store.state.alarms.next is not None and ctx.store.state.alarms.next.label == "Naive"
    out = a.to_out(a.rows()[0])
    assert out.next_at is not None  # /api/alarms says so too
    assert ctx.store.state.alarms.degraded is False


# ---- 2. a sleep-timer fade that overlaps the ring stops the alarm's ramp -----------------------------------------
async def test_a_sleep_fade_ending_as_the_alarm_rings_stops_its_ramp(svc) -> None:
    a, ctx = svc
    audio: AudioService = ctx.svc(AudioService)
    timers: TimerService = ctx.svc(TimerService)
    await audio.set_volume(20, overlay=False)  # listening at 20 before bed
    ctx.config.timers.sleep_fade_s = 2
    timers._sleep_total, timers._sleep_end = 3, ctx.store.now() + timedelta(seconds=3)
    timers._sleep_task = asyncio.create_task(timers._run_sleep())  # the real fade code: 1 s wait, 2 s fade to 0, restore to 20
    await asyncio.sleep(1.2)  # the fade has begun
    await a.test_ring(source="chime:birds", label="Ramped", volume=70, ramp_seconds=60)
    await a.ring.settled()
    await asyncio.sleep(3.5)  # the fade is over and restored its volume; the ramp should be climbing from 10 towards 70
    assert a.ring is not None and a.ring.active
    assert a.ring._ramp_task is not None and a.ring._ramp_task.done(), "the ramp carried on (defect fixed?)"
    assert audio.volume == 20, f"volume is {audio.volume}: the ramp carried on (defect fixed?)"
    await a.stop_ringing()


# ---- 3. a preparation that hangs blocks every later preparation ---------------------------------------------
async def test_a_hung_preparation_blocks_every_later_one(svc, monkeypatch) -> None:
    a, ctx = svc
    dab: DabService = ctx.svc(DabService)

    async def hang(sid: str, *, may_retune: bool):
        await asyncio.sleep(3600)

    monkeypatch.setattr(dab, "prepare_for", hang)
    ctx.store.state.dab.sdr_present = True
    radio = a.create(AlarmIn(label="Radio", time="06:33", repeat="daily", source="dab:1002"), now=BEFORE)
    await a.tick(now=AT)  # 3 min ahead: inside prepare_minutes
    await asyncio.sleep(0.3)
    assert a._prep_task is not None and not a._prep_task.done()
    a.delete(radio.id or 0)
    a.create(AlarmIn(label="Chime", time="06:32", repeat="daily", source="chime:birds"), now=BEFORE)
    for sec in range(1, 4):
        await a.tick(now=AT + timedelta(seconds=sec))
        await asyncio.sleep(0.1)
    assert a._prep is None and ctx.store.state.alarms.prepare is None, "the chime alarm was prepared (defect fixed?)"
    assert not a._prep_task.done()  # the hung task is still the one and only
    a._prep_task.cancel()


# ---- 4. missed_grace_minutes: 0 is accepted and makes every alarm a miss ---------------------------------------
async def test_grace_zero_makes_every_alarm_a_miss(svc) -> None:
    a, ctx = svc
    ctx.config.alarm_defaults.missed_grace_minutes = 0  # the schema allows 0
    a.create(AlarmIn(label="G0", time="06:30", repeat="daily", source="chime:birds"), now=BEFORE)
    await a.tick(now=AT)  # 3 s past the minute, as every tick is
    assert a.ring is None
    assert any(e["kind"] == "alarm_missed" for e in ctx.db.recent_events(20))


# ---- 5. the immutable read-only fallback does not see rows still in the WAL ------------------------------------
def test_immutable_fallback_misses_alarms_still_in_the_wal(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "dawn.db"
    code = textwrap.dedent(f'''
        import os, signal, sys
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
            raise sqlite3.OperationalError("unable to open database file")  # the card is read-only and the -shm cannot be made
        return real_open(self, url, create=create)

    monkeypatch.setattr(Database, "_open", no_rw)
    db = Database(path)
    assert db.mode == "ro"
    with db.session() as s:
        labels = sorted(r.label for r in s.query(AlarmRow).all())
    assert labels == ["old"], f"the fallback saw {labels} (defect fixed?)"


# ---- 6. scheduler walk across both DST changes (confidence check, expected to pass = correct) ---------------------
def test_dst_walks_ring_exactly_once_per_day() -> None:
    from dawn_core.alarms.scheduler import AlarmSpec, due_occurrence

    for day, t in ((datetime(2026, 4, 5, tzinfo=SYD), "02:30"), (datetime(2026, 10, 4, tzinfo=SYD), "02:30"), (datetime(2026, 4, 5, tzinfo=SYD), "06:30"), (datetime(2026, 10, 4, tzinfo=SYD), "06:30")):
        spec = AlarmSpec(time=t, repeat="daily", last_fired_occurrence=(day - timedelta(hours=2)).isoformat())  # everything before the walk is handled
        fired = []
        now = (day - timedelta(hours=2)).astimezone(SYD)
        end = (day + timedelta(hours=50)).astimezone(SYD)
        while now < end:
            due = due_occurrence(spec, now, SYD)
            if due is not None:
                fired.append(due)
                spec.last_fired_occurrence = due.isoformat()
            now = (now + timedelta(seconds=30)).astimezone(SYD)
        assert [f.date() for f in fired] == [day.date(), (day + timedelta(days=1)).date()], (t, day.date(), fired)  # once on the change day, once the next
