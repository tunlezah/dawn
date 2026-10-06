from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient

from dawn_core.app import create_app
from dawn_core.config import ConfigManager
from dawn_core.display.sleep import SleepPlanner, SleepSettings, in_window, last_at, local_at, next_at

TZ = ZoneInfo("Australia/Sydney")
DARK, LAMP, DAY = 0.5, 150.0, 800.0


def at(day: int, hh: int, mm: int = 0, ss: int = 0) -> datetime:
    return datetime(2026, 10, day, hh, mm, ss, tzinfo=TZ)


class Night:
    """Drives a planner minute by minute (lux per minute from a function) and records what it wanted."""

    def __init__(self, s: SleepSettings | None = None, alarm: datetime | None = None):
        self.s = s or SleepSettings()
        self.p = SleepPlanner()
        self.alarm = alarm

    def run(self, start: datetime, end: datetime, lux, ringing=lambda t: False, step_s: int = 30) -> dict[datetime, bool]:
        out = {}
        t = start
        while t <= end:
            out[t] = self.p.update(self.s, t, lux(t), self.alarm, ringing(t))
            t += timedelta(seconds=step_s)
        return out


def test_window_helpers_across_midnight_and_dst() -> None:
    assert in_window(at(6, 2), time(22, 30), time(6, 30)) and not in_window(at(6, 12), time(22, 30), time(6, 30))
    assert in_window(at(6, 14), time(13, 0), time(15, 0)) and not in_window(at(6, 16), time(13, 0), time(15, 0))
    assert not in_window(at(6, 2), time(6, 0), time(6, 0))
    assert last_at(at(6, 2), time(22, 30)) == at(5, 22, 30) and next_at(at(6, 2), time(6, 30)) == at(6, 6, 30)
    # Sydney springs forward on 4 Oct 2026 at 02:00: a 02:30 bedtime that night is an hour later on the wall
    gap = local_at(at(4, 12), time(2, 30))
    assert gap.hour == 3 and gap.minute == 30


def test_bedtime_or_dark_whichever_first() -> None:
    # lights off at 21:40, before the 22:30 bedtime: the dark trigger takes it
    n = Night()
    r = n.run(at(5, 21), at(5, 23), lambda t: LAMP if t < at(5, 21, 40) else DARK)
    assert not r[at(5, 21, 40)] and r[at(5, 21, 41)] and n.p.reason == "dark"
    # lights still on at bedtime: OR means it sleeps anyway
    n = Night()
    r = n.run(at(5, 21), at(5, 23), lambda t: LAMP)
    assert not r[at(5, 22, 29, 30)] and r[at(5, 22, 30)] and n.p.reason == "schedule"


def test_each_start_trigger_on_its_own() -> None:
    only_time = SleepSettings(start_when_dark=False)
    r = Night(only_time).run(at(5, 21), at(5, 23), lambda t: DARK)
    assert not r[at(5, 22)] and r[at(5, 22, 30)]
    only_dark = SleepSettings(start_at_time=False)
    r = Night(only_dark).run(at(5, 21), at(5, 23), lambda t: LAMP)
    assert not any(r.values())  # lit room, no bedtime: never
    r = Night(only_dark).run(at(5, 13), at(5, 14), lambda t: DARK)  # a dark room in the afternoon counts too
    assert r[at(5, 13, 1)]


def test_morning_time_wakes_and_a_dark_morning_stays_awake() -> None:
    n = Night()
    r = n.run(at(5, 22), at(6, 7, 30), lambda t: DARK if t < at(6, 7, 15) else DAY)
    assert r[at(6, 6, 29, 30)] and not r[at(6, 6, 30)]
    # still dark after 06:30 but it must not go back to sleep until the room has been light
    assert not any(v for t, v in r.items() if at(6, 6, 30) <= t < at(6, 7, 15))
    assert not n.p.dark_armed or n.p.room == "bright"
    # that evening the lights go off: the dark trigger works again
    r = n.run(at(6, 18), at(6, 21), lambda t: DAY if t < at(6, 20) else DARK)
    assert r[at(6, 20, 1)] and n.p.reason == "dark"


def test_wakes_before_the_alarm_or_its_light_wake() -> None:
    alarm = at(6, 6, 0)
    n = Night(SleepSettings(end_at_time=False, alarm_lead_minutes=10), alarm=alarm)
    r = n.run(at(5, 22), at(6, 6, 30), lambda t: DARK)
    assert r[at(6, 5, 49, 30)] and not r[at(6, 5, 50)]
    assert not any(v for t, v in r.items() if t >= at(6, 5, 50))  # stays awake for the alarm


def test_lamp_in_the_night_wakes_then_sleeps_again() -> None:
    n = Night(SleepSettings(start_when_dark=False))  # bedtime only; the lamp still counts inside the window
    lamp = (at(6, 2, 0), at(6, 2, 10))
    r = n.run(at(5, 22), at(6, 3), lambda t: LAMP if lamp[0] <= t < lamp[1] else DARK)
    assert r[at(6, 1, 59)]
    assert not r[at(6, 2, 1)]  # bright after 20 s
    assert r[at(6, 2, 12)]  # dark again for a minute, still inside the window: back to sleep


def test_bright_morning_wakes_without_a_morning_time() -> None:
    n = Night(SleepSettings(end_at_time=False, end_before_alarm=False))
    r = n.run(at(5, 22), at(6, 8), lambda t: DARK if t < at(6, 6, 45) else DAY)
    assert r[at(6, 6, 44)] and not r[at(6, 6, 46)]


def test_ringing_wakes_and_is_not_overridden() -> None:
    n = Night(SleepSettings(end_at_time=False, end_before_alarm=False, end_when_bright=False))
    ring = (at(6, 3, 0), at(6, 3, 5))
    r = n.run(at(5, 22, 40), at(6, 4), lambda t: DARK, ringing=lambda t: ring[0] <= t < ring[1])
    assert r[at(6, 2, 59)] and not r[at(6, 3, 0)] and not r[at(6, 3, 30)]


def test_boot_inside_the_window_sleeps_and_clock_steps_resync() -> None:
    p, s = SleepPlanner(), SleepSettings()
    assert p.update(s, at(6, 1), None, None, False)  # rebooted at 01:00 with no sensor
    p2 = SleepPlanner()
    p2.update(s, datetime(2026, 1, 1, 0, 0, tzinfo=TZ), None, None, False)  # fake-hwclock era, inside the window
    assert p2.update(s, at(6, 12), None, None, False) is False  # chrony steps to midday: resynced, awake


def test_boot_in_a_lit_room_is_not_a_bright_edge() -> None:
    # rebooted at 23:00 with the lights on: asleep by the schedule, and the room settling to bright must not wake it
    r = Night().run(at(5, 23), at(5, 23, 5), lambda t: LAMP)
    assert all(r.values())
    # a dim room at boot is a real "between": lights on later still wake it
    r = Night().run(at(5, 23), at(5, 23, 5), lambda t: 10.0 if t < at(5, 23, 2) else LAMP)
    assert r[at(5, 23, 1)] and not r[at(5, 23, 3)]
    # boot in the dark with only the dark trigger: settling to dark counts
    r = Night(SleepSettings(start_at_time=False)).run(at(5, 13), at(5, 13, 3), lambda t: DARK)
    assert r[at(5, 13, 1)]


def instants(start: datetime, end: datetime, step_s: int = 30):
    """Real time steps (in UTC), shown on the local clock: across a DST change the wall clock jumps, time does not."""
    t = start.astimezone(UTC)
    while t <= end.astimezone(UTC):
        yield t.astimezone(TZ)
        t += timedelta(seconds=step_s)


def test_dst_changes_are_not_clock_steps() -> None:
    s = SleepSettings()
    # spring forward, Sun 4 Oct 2026 02:00 -> 03:00: woken by hand at 23:00, it stays awake through the jump
    p, woke = SleepPlanner(), datetime(2026, 10, 3, 23, 0, tzinfo=TZ)
    for now in instants(datetime(2026, 10, 3, 22, 0, tzinfo=TZ), datetime(2026, 10, 4, 4, 0, tzinfo=TZ)):
        if now.timestamp() == woke.timestamp():
            p.manual(False)
        asleep = p.update(s, now, None, None, False)
        assert not (asleep and now.timestamp() >= woke.timestamp()), now
    # fall back, Sun 5 Apr 2026 03:00 -> 02:00: a lamp on at 02:30 (daylight time) keeps it awake through the repeat
    p, lamp = SleepPlanner(), datetime(2026, 4, 4, 15, 30, tzinfo=UTC)  # 02:30+11:00
    for now in instants(datetime(2026, 4, 4, 22, 0, tzinfo=TZ), datetime(2026, 4, 5, 4, 0, tzinfo=TZ)):
        asleep = p.update(s, now, LAMP if now >= lamp else DARK, None, False)
        assert not (asleep and now.timestamp() >= lamp.timestamp() + 30), now


def test_restart_inside_the_alarm_lead_stays_awake() -> None:
    p, s, alarm = SleepPlanner(), SleepSettings(), at(6, 6, 0)
    assert not p.update(s, at(6, 5, 55), None, alarm, False)  # core restarted at 05:55, inside the window
    assert not p.update(s, at(6, 5, 59, 59), None, alarm, False)
    assert p.next_end(s, at(6, 5, 55), alarm) == at(6, 6, 30)  # not 05:50, which has passed


def test_a_ring_at_bedtime_does_not_cancel_it() -> None:
    ring = (at(5, 22, 29, 50), at(5, 22, 31))
    for light in (None, LAMP):
        n = Night()
        r = n.run(at(5, 22), at(5, 22, 40), lambda t, light=light: light, ringing=lambda t: ring[0] <= t < ring[1], step_s=10)
        assert not r[at(5, 22, 30, 50)] and r[at(5, 22, 31)], light
    # but a morning alarm that rings past a quarter of an hour does not send it back to sleep
    n = Night(alarm=at(6, 6, 0))
    r = n.run(at(6, 5, 0), at(6, 6, 40), lambda t: None, ringing=lambda t: at(6, 6, 0) <= t < at(6, 6, 20), step_s=10)
    assert not any(v for t, v in r.items() if t >= at(6, 5, 50))


def test_a_passing_shadow_is_not_a_bright_morning() -> None:
    """Asleep with the lights on: a hand over the sensor for 2 s must not count as the room getting bright again."""
    shadow = (at(5, 23, 0), at(5, 23, 0, 2))
    r = Night().run(at(5, 22), at(5, 23, 5), lambda t: 2.0 if shadow[0] <= t < shadow[1] else LAMP, step_s=1)
    assert r[at(5, 22, 30)] and all(v for t, v in r.items() if t >= at(5, 22, 30))


def test_dark_only_sleep_says_dark() -> None:
    n = Night(SleepSettings(start_at_time=False))
    n.run(at(5, 23), at(5, 23, 3), lambda t: DARK)
    assert n.p.asleep and n.p.reason == "dark"


def test_no_sensor_means_schedule_only() -> None:
    r = Night().run(at(5, 22), at(6, 7), lambda t: None)
    assert r[at(5, 22, 30)] and r[at(6, 6, 29, 30)] and not r[at(6, 6, 30)]


def test_manual_sleep_and_wake() -> None:
    p, s = SleepPlanner(), SleepSettings()
    p.update(s, at(5, 15), DAY, None, False)
    p.update(s, at(5, 15, 0, 25), DAY, None, False)  # the room is known to be bright
    p.manual(True)
    assert p.update(s, at(5, 15, 0, 30), DAY, None, False) and p.reason == "manual"
    p.manual(False)
    assert not p.update(s, at(5, 15, 1), DARK, None, False)  # a manual wake holds off the dark trigger
    assert [e[0] for e in p.events] == ["sleep", "wake"]


def test_next_start_and_end() -> None:
    p, s = SleepPlanner(), SleepSettings()
    now = at(5, 12)
    assert p.next_start(s, now) == at(5, 22, 30)
    assert p.next_end(s, now, at(6, 6, 0)) == at(6, 5, 50)
    assert p.next_end(SleepSettings(end_before_alarm=False), now, at(6, 6, 0)) == at(6, 6, 30)


# ---- on the device: face mode, taps, backlight ----------------------------------------------------------------

# only the manual switch: the test must not depend on the hour or on the simulated light sensor
MANUAL_ONLY = {"enabled": True, "start_at_time": False, "start_when_dark": False, "end_at_time": False,
               "end_before_alarm": False, "end_when_bright": False}


@pytest.fixture()
async def client(tmp_config):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def until(client: AsyncClient, pred, timeout: float = 4.0) -> dict:
    s: dict = {}
    for _ in range(int(timeout / 0.05)):
        s = (await client.get("/api/state")).json()
        if pred(s):
            return s
        await asyncio.sleep(0.05)
    raise AssertionError(f"condition not met; face={s.get('face')} display.sleep={s.get('display', {}).get('sleep')}")


async def test_sleep_face_tap_and_ringing(client: AsyncClient) -> None:
    assert (await client.post("/api/display/sleep", json={"on": True})).status_code == 409  # off in the test config
    r = await client.patch("/api/config", json={"display": {"sleep": MANUAL_ONLY}})
    assert r.status_code == 200
    r = await client.post("/api/display/sleep", json={"on": True})
    assert r.status_code == 200 and r.json() == {"sleep": True, "reason": "manual"}
    s = await until(client, lambda s: s["face"]["mode"] == "sleep")
    assert s["display"]["sleep_enabled"] and s["display"]["sleep_reason"] == "manual"
    # the backlight goes to the floor (backlight.min_percent, 1 %)
    await until(client, lambda s: s["display"]["brightness"] <= 1)
    # first tap: the full face, dimly, for standby_wake_s, but not the menu
    await client.post("/api/face/touch")
    s = await until(client, lambda s: s["face"]["mode"] == "standby")
    assert not s["face"]["menu_open"] and s["face"]["wake_until"] is not None and s["display"]["sleep"]
    await until(client, lambda s: s["display"]["target"] == 15)  # sleep.wake_percent, not standby_wake_percent (100)
    # the tap-wake runs out: back to the sleep clock
    app = client._transport.app  # type: ignore[attr-defined]
    app.state.ctx.store.state.face.wake_until = None
    app.state.ctx.store.touch()
    await until(client, lambda s: s["face"]["mode"] == "sleep")
    # an alarm ringing ends sleep mode (and the planner does not put it back afterwards)
    assert (await client.post("/api/alarms/test", json={"source": "chime"})).status_code == 200
    await until(client, lambda s: s["face"]["mode"] == "ringing")  # at once: ringing wins over sleep in the face
    await until(client, lambda s: s["display"]["sleep"] is False)  # and the planner (1 Hz) lets go of it
    await client.post("/api/alarms/stop")
    s = await until(client, lambda s: s["face"]["mode"] == "standby")
    assert s["display"]["sleep"] is False


async def test_screen_off_tap_peeks_then_wakes(client: AsyncClient) -> None:
    await client.patch("/api/config", json={"display": {"sleep": {**MANUAL_ONLY, "screen_off": True}}})
    await client.post("/api/display/sleep", json={"on": True})
    s = await until(client, lambda s: s["face"]["mode"] == "sleep" and s["display"]["sleep_screen_off"])
    await until(client, lambda s: s["display"]["brightness"] == 0)  # backlight off, not just the floor
    await client.post("/api/face/touch")  # first tap: the sleep clock for a few seconds, still asleep
    s = await until(client, lambda s: s["face"]["peek_until"] is not None)
    assert s["face"]["mode"] == "sleep" and s["face"]["wake_until"] is None
    await until(client, lambda s: s["display"]["target"] == 1)  # the sleep floor while peeking
    await client.post("/api/face/touch")  # second tap: the full face
    s = await until(client, lambda s: s["face"]["mode"] == "standby")
    assert s["face"]["peek_until"] is None and not s["face"]["menu_open"]


async def test_backlight_comes_back_after_screen_off_sleep_in_a_dim_room(client: AsyncClient) -> None:
    """Screen-off sleep holds the target at 0; afterwards the curve's 1-5 % in a dim room is within the 4 %
    hysteresis of 0, so the target must be taken from the curve again, or the ringing screen stays black."""
    from dawn_core.display.service import DisplayService

    class Dim:
        name = "dim"

        async def read(self) -> float:
            return 0.5

        async def stop(self) -> None:
            pass

    d = client._transport.app.state.ctx.svc(DisplayService)  # type: ignore[attr-defined]
    d.sensor, d.lux = Dim(), 0.5
    await client.patch("/api/config", json={"display": {"sleep": {**MANUAL_ONLY, "screen_off": True}}})
    await client.post("/api/display/sleep", json={"on": True})
    await until(client, lambda s: s["display"]["brightness"] == 0)
    assert (await client.post("/api/alarms/test", json={"source": "chime"})).status_code == 200
    await until(client, lambda s: s["face"]["mode"] == "ringing")
    s = await until(client, lambda s: s["display"]["brightness"] >= 1)
    assert s["display"]["target"] >= 1
    await client.post("/api/alarms/stop")


async def test_turning_sleep_off_wakes_and_logs(client: AsyncClient) -> None:
    await client.patch("/api/config", json={"display": {"sleep": MANUAL_ONLY}})
    await client.post("/api/display/sleep", json={"on": True})
    await until(client, lambda s: s["face"]["mode"] == "sleep")
    await client.patch("/api/config", json={"display": {"sleep": {"enabled": False}}})
    s = await until(client, lambda s: s["face"]["mode"] == "standby")
    assert not s["display"]["sleep"] and not s["display"]["sleep_enabled"] and s["display"]["sleep_next_start"] is None
    app = client._transport.app  # type: ignore[attr-defined]
    rows = app.state.ctx.db.events_since(datetime.now(ZoneInfo("UTC")) - timedelta(minutes=5), kinds=["sleep_mode"])
    assert [(r["on"], r["reason"]) for r in reversed(rows)][-2:] == [(True, "manual"), (False, "turned off")]  # newest first
