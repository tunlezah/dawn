"""Engine walks: the real AlarmService ticked through simulated days (10 s steps, 1 s around the alarm minute) with
several alarm kinds, across the April DST change, and through clock steps. These are confidence checks: passing
means the engine fires exactly what it should."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

os.environ["DAWN_SIM"] = "1"

from dawn_core.alarms.schemas import AlarmIn  # noqa: E402
from dawn_core.alarms.service import AlarmService  # noqa: E402
from dawn_core.app import create_app  # noqa: E402
from dawn_core.config import ConfigManager  # noqa: E402

SYD = ZoneInfo("Australia/Sydney")


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
        a._task.cancel()
        yield a, ctx


def _spy(a: AlarmService, monkeypatch) -> list[tuple[str, str, int]]:
    fired: list[tuple[str, str, int]] = []
    orig = a.fire

    async def fire(row, occurrence, late_s=0):
        await orig(row, occurrence, late_s)
        fired.append((row.label, occurrence.strftime("%Y-%m-%dT%H:%M"), late_s))
        await a.stop_ringing()

    monkeypatch.setattr(a, "fire", fire)
    return fired


async def _walk(a: AlarmService, start: datetime, end: datetime, alarm_minutes: set[str]) -> int:
    now, n = start, 0
    while now < end:
        await a.tick(now=now)
        n += 1
        hm = now.strftime("%H:%M")
        step = 1 if hm in alarm_minutes else 10
        now = (now + timedelta(seconds=step)).astimezone(SYD)
    return n


async def test_three_days_across_the_dst_change(svc, monkeypatch) -> None:
    a, ctx = svc
    fired = _spy(a, monkeypatch)
    start = datetime(2026, 4, 3, 20, 0, tzinfo=SYD)  # Friday evening; DST ends Sunday 5 April 03:00 -> 02:00
    for label, t, rep, extra in (
        ("Weekday", "06:30", "weekdays", {}), ("Daily", "06:30", "daily", {}), ("Custom", "07:15", "custom", {"days": [5, 6]}),
        ("Once", "23:30", "once", {}), ("Night", "02:30", "daily", {}), ("Skip", "08:00", "daily", {"skip_next": True}),
        ("Holiday", "09:00", "daily", {"skip_public_holidays": True}),  # Good Friday to Easter Monday 2026 are all NSW public holidays: never
    ):
        a.create(AlarmIn(label=label, time=t, repeat=rep, source="chime:birds", ramp_seconds=0, **extra), now=start)
    end = datetime(2026, 4, 6, 10, 0, tzinfo=SYD)
    n = await _walk(a, start, end, {"06:30", "07:15", "23:30", "02:30", "08:00", "09:00"})
    got = sorted(fired)
    want = sorted([
        ("Weekday", "2026-04-06T06:30", 0),
        ("Daily", "2026-04-04T06:30", 0), ("Daily", "2026-04-05T06:30", 0), ("Daily", "2026-04-06T06:30", 0),
        ("Custom", "2026-04-04T07:15", 0), ("Custom", "2026-04-05T07:15", 0),
        ("Once", "2026-04-03T23:30", 0),
        ("Night", "2026-04-04T02:30", 0), ("Night", "2026-04-05T02:30", 0), ("Night", "2026-04-06T02:30", 0),
        ("Skip", "2026-04-05T08:00", 0), ("Skip", "2026-04-06T08:00", 0),
    ])
    assert got == want, f"after {n} ticks: {got}"
    from datetime import date
    assert all(a.calendar().is_holiday(date(2026, 4, d)) for d in (3, 4, 5, 6))
    kinds = [e["kind"] for e in ctx.db.recent_events(500)]
    assert "alarm_missed" not in kinds
    assert kinds.count("alarm_skipped") == 1
    assert a.tick_failures == 0


async def test_clock_steps(svc, monkeypatch) -> None:
    a, ctx = svc
    fired = _spy(a, monkeypatch)
    base = datetime(2026, 5, 10, 12, 0, tzinfo=SYD)
    a.create(AlarmIn(label="A", time="06:30", repeat="daily", source="chime:birds", ramp_seconds=0), now=base)
    d = lambda days, h, m, s=0: datetime(2026, 5, 11 + days, h, m, s, tzinfo=SYD)  # noqa: E731
    # a) the clock steps forward 8 min across the alarm (chrony after boot): rings, late
    await a.tick(now=d(0, 6, 25))
    await a.tick(now=d(0, 6, 33))
    assert fired[-1] == ("A", "2026-05-11T06:30", 180)
    # b) a forward step of 15 min across it: a miss, not a ring
    await a.tick(now=d(1, 6, 25))
    await a.tick(now=d(1, 6, 45))
    assert len(fired) == 1 and any(e["kind"] == "alarm_missed" for e in ctx.db.recent_events(10))
    # c) a backward step of 5 min after it rang: no second ring when 06:30 comes round again
    await a.tick(now=d(2, 6, 30, 2))
    assert len(fired) == 2
    await a.tick(now=d(2, 6, 25))
    await a.tick(now=d(2, 6, 30, 2))
    await a.tick(now=d(2, 6, 31))
    assert len(fired) == 2
    # d) a backward step of 15 min after it rang: the first ring was on a fast clock, so it rings again at the real 06:30
    await a.tick(now=d(3, 6, 30, 2))
    assert len(fired) == 3
    await a.tick(now=d(3, 6, 15))
    await a.tick(now=d(3, 6, 30, 2))
    assert len(fired) == 4 and fired[-1][1] == "2026-05-14T06:30"
    # e) a boot with the clock an hour behind: 05:30 shown while it is really 06:30; the step lands 2 min after the alarm
    await a.tick(now=d(4, 5, 30))
    await a.tick(now=d(4, 6, 32))
    assert fired[-1] == ("A", "2026-05-15T06:30", 120)
    assert a.tick_failures == 0
