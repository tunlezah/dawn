"""Cool mode (robustness plan WP-G). The planner is pure, so it is walked minute by minute like the sleep planner; the
service, the decoder's pause and wake, the supervisor and the alarm's preparation are checked around it. The
interlocks are the point: the radio is back before any alarm on DAB, the CPU cap comes off when any alarm's
preparation starts, and any activity ends cool mode at once."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from test_supervisor import restarts, sv  # noqa: F401  (sv is the fixture)

from dawn_core.app import create_app
from dawn_core.config import ConfigManager
from dawn_core.config.schema import PowerConfig
from dawn_core.dab.service import DabService
from dawn_core.power.cool import CoolInputs, CoolPlanner
from dawn_core.power.service import PowerService

SYD = ZoneInfo("Australia/Sydney")


def walk(planner: CoolPlanner, start: datetime, minutes: int, inputs_at, step_s: int = 60):
    """Run the planner every `step_s` from `start` for `minutes`; `inputs_at(t, mono)` gives the inputs. Yields
    (wall, plan)."""
    for k in range(minutes * 60 // step_s + 1):
        wall = start + timedelta(seconds=k * step_s)
        mono = 10_000.0 + k * step_s
        yield wall, planner.update(mono, wall, inputs_at(wall, mono))


# ---- the planner ---------------------------------------------------------------------------------------------------
def test_a_summer_night_with_a_dab_alarm_at_0630() -> None:
    cfg = PowerConfig(cool_mode="auto", idle_after_s=300)
    p = CoolPlanner(cfg, prepare_minutes=5)
    alarm = datetime(2027, 1, 15, 6, 30, tzinfo=SYD)  # January in Sydney: summer
    last_touch = 10_000.0  # someone at 05:00

    def inputs(wall, mono):
        busy = "an alarm is ringing or snoozed" if alarm <= wall < alarm + timedelta(minutes=10) else None
        return CoolInputs(month=wall.month, latitude=-33.9, last_activity=last_touch, busy=busy, ntp_live=True,
                          next_alarm_at=alarm if wall < alarm else None, next_dab_alarm_at=alarm if wall < alarm else None)

    plans = list(walk(p, datetime(2027, 1, 15, 5, 0, tzinfo=SYD), 100, inputs))
    by_min = {w.strftime("%H:%M"): pl for w, pl in plans}
    assert not by_min["05:04"].cool  # idle_after_s not reached yet
    assert by_min["05:05"].cool and not by_min["05:05"].dab_wanted and by_min["05:05"].reason == "summer"
    assert by_min["05:05"].cpu_cap_mhz == 1000 and by_min["05:05"].freeze and by_min["05:05"].lux_hz == 2.0
    for w, pl in plans:
        if datetime(2027, 1, 15, 6, 22, tzinfo=SYD) <= w < alarm + timedelta(minutes=10):  # prepare 5 + lead 3 minutes, until the ring ends
            assert pl.dab_wanted, w
    assert by_min["06:21"].dab_wanted and by_min["06:21"].dab_reason == "an alarm on the radio is coming"
    assert by_min["06:24"].cpu_cap_mhz is None  # the alarm's preparation window: no cap
    assert not by_min["06:30"].cool and by_min["06:30"].reason == "an alarm is ringing or snoozed"


def test_a_touch_ends_cool_mode_at_once() -> None:
    p = CoolPlanner(PowerConfig(cool_mode="on", idle_after_s=300))
    wall = datetime(2027, 3, 1, 14, 0, tzinfo=SYD)
    assert p.update(1000.0, wall, CoolInputs(last_activity=600.0, ntp_live=True)).cool
    plan = p.update(1005.0, wall, CoolInputs(last_activity=1004.0, ntp_live=True))  # a tap a second ago
    assert not plan.cool and plan.dab_wanted and plan.cpu_cap_mhz is None and not plan.freeze
    assert "in use" in plan.reason


def test_hot_and_cool_again_with_hysteresis() -> None:
    cfg = PowerConfig(cool_mode="auto", enter_temp_c=65, exit_temp_c=55, hold_s=300, exit_hold_s=900, summer=[1])
    p = CoolPlanner(cfg)
    wall = datetime(2026, 11, 20, 15, 0, tzinfo=SYD)  # not "summer" by this config, no forecast
    t = 0.0

    def at(temp: float, secs: float):
        nonlocal t
        t += secs
        return p.update(t, wall, CoolInputs(soc_temp_c=temp, month=wall.month, latitude=-33.9, last_activity=-10_000.0, ntp_live=True))

    assert not at(70, 0).cool
    assert not at(70, 299).cool  # not held long enough
    assert at(70, 1).cool and p.hot  # 300 s at or above 65
    assert at(60, 600).cool  # between 55 and 65: still hot
    assert at(50, 1).cool  # first reading below 55
    assert at(50, 898).cool  # below 55, but not for 900 s yet
    assert not at(50, 2).cool and not p.hot  # 900 s below 55
    assert not at(64, 1000).cool  # below the entry threshold: never hot


def test_no_network_time_runs_the_radio_for_a_time_sync_every_6_h() -> None:
    p = CoolPlanner(PowerConfig(cool_mode="on", dab_time_sync_every_h=6, dab_time_sync_minutes=10))
    wall = datetime(2027, 1, 10, 10, 0, tzinfo=SYD)
    sample = {"at": None}

    def plan(t: float):
        return p.update(t, wall, CoolInputs(last_activity=-10_000.0, dab_last_sample=sample["at"]))

    assert plan(0).dab_wanted  # straight away: there has been no sync yet
    assert plan(300).dab_wanted
    assert not plan(601).dab_wanted  # ten minutes up (no sample came)
    assert not plan(3 * 3600).dab_wanted
    assert plan(601 + 6 * 3600).dab_wanted  # six hours on
    sample["at"] = 601 + 6 * 3600 + 120  # dawn-timed wrote a sample two minutes in
    assert not plan(601 + 6 * 3600 + 125).dab_wanted  # done early


def test_a_chime_alarm_keeps_the_radio_paused_but_lifts_the_cpu_cap() -> None:
    p = CoolPlanner(PowerConfig(cool_mode="on"), prepare_minutes=5)
    alarm = datetime(2027, 1, 10, 6, 30, tzinfo=SYD)

    def plan(wall):
        return p.update(10_000.0, wall, CoolInputs(last_activity=0.0, ntp_live=True, next_alarm_at=alarm, next_dab_alarm_at=None))

    early = plan(alarm - timedelta(minutes=30))
    assert early.cool and not early.dab_wanted and early.cpu_cap_mhz == 1000
    prep = plan(alarm - timedelta(minutes=5))
    assert prep.cool and not prep.dab_wanted and prep.cpu_cap_mhz is None and prep.poll_scale == 1.0


@pytest.mark.parametrize(("why", "field"), [("radio in use", "dab_in_slot"), ("scan", "scan_running"), ("page", "dab_page_open")])
def test_the_radio_runs_while_something_needs_it(why, field) -> None:
    p = CoolPlanner(PowerConfig(cool_mode="on"))
    plan = p.update(10_000.0, datetime(2027, 1, 10, 13, 0, tzinfo=SYD), CoolInputs(last_activity=0.0, ntp_live=True, **{field: True}))
    assert plan.cool and plan.dab_wanted


def test_off_is_never_cool_and_pause_dab_false_keeps_the_radio() -> None:
    wall = datetime(2027, 1, 10, 13, 0, tzinfo=SYD)
    assert not CoolPlanner(PowerConfig(cool_mode="off")).update(10_000.0, wall, CoolInputs(last_activity=0.0)).cool
    plan = CoolPlanner(PowerConfig(cool_mode="on", pause_dab=False)).update(10_000.0, wall, CoolInputs(last_activity=0.0, ntp_live=True))
    assert plan.cool and plan.dab_wanted


# ---- the service, the decoder and the alarms --------------------------------------------------------------------------
@pytest.fixture()
async def app_ctx(tmp_config):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        ctx = app.state.ctx
        pw: PowerService = ctx.svc(PowerService)
        pw._task.cancel()  # ticks by hand
        dab: DabService = ctx.svc(DabService)
        dab._task.cancel()
        units: list[str] = []

        async def unit(verb: str) -> None:
            units.append(verb)

        async def reachable() -> bool:
            return not dab.paused

        dab._unit = unit
        dab.client.reachable = reachable
        if dab._waking:
            await asyncio.wait([dab._waking], timeout=2)
        yield ctx, pw, dab, units


async def test_the_service_pauses_the_radio_when_cool_and_wakes_it_for_a_dab_alarm(app_ctx) -> None:
    ctx, pw, dab, units = app_ctx
    await ctx.cfg_mgr.update({"power": {"cool_mode": "on", "idle_after_s": 60}})
    from dawn_core.state.ui import TimeSource

    ctx.store.state.time_sources.sources = [TimeSource(name="pool", kind="ntp", live=True)]  # time from the network: no DAB sync needed
    ctx.last_activity = pw._started = -1000.0
    pw._first = False
    wall = datetime(2027, 1, 10, 6, 0, tzinfo=SYD)
    plan = await pw.tick(now=10.0, wall=wall)
    assert plan.cool and dab.paused == "cool mode" and units == ["stop"]
    assert ctx.store.state.dab.paused and ctx.store.state.power.dab_paused and ctx.store.state.display.cool
    await pw.tick(now=15.0, wall=wall)
    assert units == ["stop"]  # idempotent
    from dawn_core.alarms.service import AlarmService

    ctx.svc(AlarmService).next_dab_wake = wall + timedelta(minutes=9)  # an alarm on the radio comes into the lead window
    plan = await pw.tick(now=20.0, wall=wall)
    assert plan.dab_wanted
    await asyncio.wait([dab._waking], timeout=2)
    assert units == ["stop", "start"] and dab.paused is None and not ctx.store.state.dab.paused


async def test_after_a_restart_the_radio_is_started_whatever_was_remembered(app_ctx) -> None:
    ctx, pw, dab, units = app_ctx
    pw._first = True  # the first tick after dawn-core started (the decoder may have been left stopped)
    await pw.tick(now=10.0, wall=datetime(2027, 1, 10, 13, 0, tzinfo=SYD))
    assert not pw.plan.cool
    await asyncio.wait([dab._waking], timeout=2)
    assert units == ["start"]


async def test_preparation_treats_a_paused_radio_as_waking_not_failing(app_ctx) -> None:
    ctx, pw, dab, units = app_ctx
    ctx.store.state.dab.sdr_present = True
    await dab.pause("cool mode")
    status, msg = await dab.prepare_for("1002", may_retune=True, occurrence="1@x")
    assert (status, msg) == ("tuning", "waking the radio")
    await asyncio.wait([dab._waking], timeout=2)
    assert units == ["stop", "start"] and dab.paused is None


async def test_the_radio_is_not_paused_while_an_alarm_rings_on_it(app_ctx) -> None:
    ctx, pw, dab, units = app_ctx
    dab._alarm_conflict = lambda within_s=0.0, ringing_only=False: "Radio is ringing on the radio"
    assert not await dab.pause("cool mode") and units == []


async def test_last_played_on_dab_counts_as_a_dab_alarm(app_ctx) -> None:
    ctx, pw, dab, units = app_ctx
    from dawn_core.alarms.schemas import AlarmIn
    from dawn_core.alarms.service import AlarmService
    from dawn_core.audio.service import AudioService

    a: AlarmService = ctx.svc(AlarmService)
    audio: AudioService = ctx.svc(AudioService)
    audio.resolve = lambda ref: "dab:1002" if ref == "last-played" else ref  # the last thing played was a DAB station
    now = ctx.store.now()
    at = (now + timedelta(hours=2)).strftime("%H:%M")
    a.create(AlarmIn(label="Last", time=at, repeat="daily", source="last-played"), now=now)
    await a.tick(now=now)
    assert a.next_dab_wake is not None


async def test_the_supervisor_leaves_a_paused_decoder_alone_until_the_power_loop_dies(sv, monkeypatch) -> None:  # noqa: F811
    s, ctx, host = sv
    dab: DabService = ctx.svc(DabService)
    dab.paused = "cool mode"
    host.states["dawn-dab"] = "inactive"
    t = 1000.0
    for k in range(4):
        await s.check(now=t + k * 20)
    assert restarts(host) == []
    ctx.svc(PowerService)._task.cancel()  # the controller died: normal policy, the decoder is not left off
    await asyncio.sleep(0)
    await s.check(now=t + 100)
    await s.check(now=t + 140)
    assert restarts(host) == ["dawn-dab"]
