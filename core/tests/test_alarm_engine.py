"""Engine behaviour with injected time: fire within grace, miss outside it,
once-alarms disable, no double fire across a clock step, skip-next consumed,
and an alarm only answers for occurrences after it was created, edited or switched on."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from dawn_core.alarms.schemas import AlarmIn
from dawn_core.alarms.service import AlarmService
from dawn_core.app import create_app
from dawn_core.config import ConfigManager

SYD = ZoneInfo("Australia/Sydney")
BEFORE = datetime(2026, 10, 1, 12, 0, tzinfo=SYD)  # the alarms below are set up the day before their tests


@pytest.fixture()
async def svc(tmp_config):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        ctx = app.state.ctx
        a: AlarmService = ctx.svc(AlarmService)
        a._task.cancel()  # drive ticks manually
        yield a, ctx


def _events(ctx, kind):
    return [e for e in ctx.db.recent_events(200) if e["kind"] == kind]


async def test_fires_once_within_grace(svc) -> None:
    a, ctx = svc
    row = a.create(AlarmIn(label="T", time="06:30", repeat="daily", source="chime:birds", ramp_seconds=0), now=BEFORE)
    t = datetime(2026, 10, 2, 6, 30, 3, tzinfo=SYD)
    await a.tick(now=t)
    assert a.ring is not None and ctx.store.state.alarms.ringing.label == "T"
    await a.tick(now=t + timedelta(seconds=1))
    await a.tick(now=t + timedelta(seconds=2))
    assert len(_events(ctx, "alarm_fire")) == 1
    await a.stop_ringing()
    # clock steps back 30 s (chrony makestep): no second fire
    await a.tick(now=t - timedelta(seconds=30))
    await a.tick(now=t)
    assert a.ring is None and len(_events(ctx, "alarm_fire")) == 1
    assert a.get(row.id).last_fired_occurrence == datetime(2026, 10, 2, 6, 30, tzinfo=SYD).isoformat()


async def test_missed_outside_grace_is_logged_not_fired(svc) -> None:
    a, ctx = svc
    a.create(AlarmIn(label="M", time="06:30", repeat="daily", source="chime:birds"), now=BEFORE)
    await a.tick(now=datetime(2026, 10, 2, 6, 45, tzinfo=SYD))  # 15 min late, grace is 10
    assert a.ring is None
    assert len(_events(ctx, "alarm_missed")) == 1
    assert len(_events(ctx, "alarm_fire")) == 0
    # the next day still fires
    await a.tick(now=datetime(2026, 10, 3, 6, 30, 1, tzinfo=SYD))
    assert a.ring is not None
    await a.stop_ringing()


async def test_once_alarm_disables_after_firing(svc) -> None:
    a, ctx = svc
    row = a.create(AlarmIn(label="O", time="22:00", repeat="once", source="chime:birds", ramp_seconds=0), now=datetime(2026, 3, 1, 12, 0, tzinfo=SYD))
    await a.tick(now=datetime(2026, 3, 1, 22, 0, 2, tzinfo=SYD))
    assert a.ring is not None
    await a.stop_ringing()
    assert a.get(row.id).enabled is False
    await a.tick(now=datetime(2026, 3, 2, 22, 0, 2, tzinfo=SYD))
    assert a.ring is None


async def test_skip_next_consumed_without_firing(svc) -> None:
    a, ctx = svc
    row = a.create(AlarmIn(label="S", time="06:30", repeat="daily", source="chime:birds", skip_next=True), now=BEFORE)
    await a.tick(now=datetime(2026, 10, 2, 6, 30, 2, tzinfo=SYD))
    assert a.ring is None
    r = a.get(row.id)
    assert r.skip_next is False and len(_events(ctx, "alarm_skipped")) == 1
    await a.tick(now=datetime(2026, 10, 3, 6, 30, 2, tzinfo=SYD))
    assert a.ring is not None
    await a.stop_ringing()


async def test_holiday_skipped_in_engine(svc) -> None:
    a, ctx = svc
    a.create(AlarmIn(label="H", time="06:30", repeat="weekdays", source="chime:birds", skip_public_holidays=True, holiday_region="NSW"), now=BEFORE)
    await a.tick(now=datetime(2026, 10, 5, 6, 30, 2, tzinfo=SYD))  # Labour Day NSW (Monday)
    assert a.ring is None and not _events(ctx, "alarm_fire")
    await a.tick(now=datetime(2026, 10, 6, 6, 30, 2, tzinfo=SYD))
    assert a.ring is not None
    await a.stop_ringing()


async def test_snooze_and_stop_restore_volume(svc) -> None:
    a, ctx = svc
    from dawn_core.audio.service import AudioService

    audio = ctx.svc(AudioService)
    await audio.set_volume(33, overlay=False)
    a.create(AlarmIn(label="V", time="07:00", repeat="daily", source="chime:birds", volume=80, ramp_seconds=0, snooze_minutes=1), now=BEFORE)
    await a.tick(now=datetime(2026, 10, 2, 7, 0, 1, tzinfo=SYD))
    assert audio.volume == 80
    assert await a.snooze()
    assert ctx.store.state.alarms.ringing.snoozed_until is not None
    assert ctx.store.state.audio.sources[0]["state"] if False else audio.arbiter.slot("alarm").state == "paused"
    assert not await a.snooze()  # already snoozed
    await a.stop_ringing()
    assert audio.volume == 33 and ctx.store.state.alarms.ringing is None


async def test_light_wake_window(svc) -> None:
    a, ctx = svc
    a.create(AlarmIn(label="L", time="06:30", repeat="daily", source="chime:birds", light_wake=True, light_wake_minutes=10), now=BEFORE)
    await a.tick(now=datetime(2026, 10, 2, 6, 15, tzinfo=SYD))
    assert ctx.store.state.alarms.light_wake_active is False
    await a.tick(now=datetime(2026, 10, 2, 6, 22, tzinfo=SYD))
    assert ctx.store.state.alarms.light_wake_active is True
    await a.dismiss_light_wake()
    await a.tick(now=datetime(2026, 10, 2, 6, 23, tzinfo=SYD))
    assert ctx.store.state.alarms.light_wake_active is False


async def test_alarm_set_after_its_time_waits_for_the_next_day(svc) -> None:
    """Set at 22:00 for 06:30: this morning's 06:30 was before the alarm existed, so it is neither missed nor due."""
    a, ctx = svc
    evening = datetime(2026, 10, 5, 22, 0, tzinfo=SYD)
    once = a.create(AlarmIn(label="Once", time="06:30", repeat="once", source="chime:birds", ramp_seconds=0), now=evening)
    await a.tick(now=evening + timedelta(seconds=1))
    assert a.get(once.id).enabled is True and not _events(ctx, "alarm_missed") and a.ring is None
    await a.tick(now=datetime(2026, 10, 6, 6, 30, 2, tzinfo=SYD))
    assert a.ring is not None
    await a.stop_ringing()
    assert a.get(once.id).enabled is False


async def test_alarm_set_just_after_its_time_does_not_ring_now(svc) -> None:
    a, ctx = svc
    now = datetime(2026, 10, 5, 6, 35, tzinfo=SYD)  # 5 min after, inside the 10-minute grace
    row = a.create(AlarmIn(label="Late", time="06:30", repeat="daily", source="chime:birds", ramp_seconds=0, skip_next=True), now=now)
    await a.tick(now=now + timedelta(seconds=1))
    assert a.ring is None and not _events(ctx, "alarm_missed") and not _events(ctx, "alarm_skipped")
    assert a.get(row.id).skip_next is True  # the skip is kept for tomorrow, not spent on this morning
    await a.tick(now=datetime(2026, 10, 6, 6, 30, 2, tzinfo=SYD))
    assert a.ring is None and a.get(row.id).skip_next is False


async def test_switching_an_alarm_on_counts_from_then(svc) -> None:
    a, ctx = svc
    row = a.create(AlarmIn(label="Toggle", time="06:30", repeat="daily", source="chime:birds", ramp_seconds=0), now=BEFORE)
    a.set_enabled(row.id, False)
    await a.tick(now=datetime(2026, 10, 2, 6, 30, 2, tzinfo=SYD))
    assert a.ring is None
    a.set_enabled(row.id, True, now=datetime(2026, 10, 2, 6, 33, tzinfo=SYD))
    await a.tick(now=datetime(2026, 10, 2, 6, 33, 1, tzinfo=SYD))
    assert a.ring is None and not _events(ctx, "alarm_missed")
    await a.tick(now=datetime(2026, 10, 3, 6, 30, 2, tzinfo=SYD))
    assert a.ring is not None
    await a.stop_ringing()
