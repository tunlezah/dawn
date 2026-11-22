"""Liveness of the loops inside dawn-core (robustness plan WP-D1): a dead or stale loop is restarted 5 s, 30 s and 120 s
apart; a loop that gates the watchdog and is still dead after that starves it (systemd restarts dawn-core and the
saved ring carries on), and `alarms.engine_alive` follows every gating loop."""

from __future__ import annotations

import asyncio

import pytest

from dawn_core.alarms.service import AlarmService
from dawn_core.alarms.timers import TimerService
from dawn_core.app import create_app
from dawn_core.config import ConfigManager
from dawn_core.display.service import DisplayService
from dawn_core.face.service import FaceService
from dawn_core.liveness import BACKOFF_S, HEAL_S, Liveness
from dawn_core.system import sysinfo
from dawn_core.system.sysinfo import SysInfoService, alarms_alive

pytestmark = pytest.mark.liveness


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


# ---- the registry on its own -------------------------------------------------------------------------------------
async def test_a_stale_loop_is_restarted_5_30_120_s_apart_then_given_up() -> None:
    clock = Clock()
    lv = Liveness(clock)
    restarts = []

    async def restart() -> None:
        restarts.append(clock.t)

    lv.register("display-loop", max_age_s=10, gates_watchdog=True, restart=restart)
    lv.beat("display-loop")
    clock.t += 5
    assert lv.check() == [] and await lv.repair() == []
    clock.t += 11  # no pass for 11 s
    assert lv.check() == ["display-loop"] and lv.gating_dead() == ["display-loop"]
    assert await lv.repair() == ["display-loop"]  # at once
    assert lv.exhausted() == []
    clock.t += 11
    assert await lv.repair() == ["display-loop"]  # 5 s after the first
    clock.t += 11
    assert await lv.repair() == []  # not yet: 30 s after the second
    clock.t += 20
    assert await lv.repair() == ["display-loop"]
    clock.t += BACKOFF_S[2] - 1
    assert lv.exhausted() == []  # still waiting out the third restart
    clock.t += 2
    assert await lv.repair() == [] and lv.exhausted() == ["display-loop"]  # given up: the watchdog is held back
    assert len(restarts) == 3


async def test_a_loop_that_comes_back_heals_and_a_non_gating_one_keeps_being_tried() -> None:
    clock = Clock()
    lv = Liveness(clock)
    calls = []

    async def restart() -> None:
        calls.append(clock.t)
        lv.beat("weather")

    lv.register("weather", max_age_s=60, restart=restart)
    clock.t += 61
    await lv.repair()
    assert len(calls) == 1 and lv.check() == []
    clock.t += 30
    lv.beat("weather")
    await lv.repair()  # alive: starts the heal clock
    clock.t += HEAL_S + 1
    lv.beat("weather")
    await lv.repair()
    assert lv._loops["weather"].attempts == 0  # healed
    # not gating: dead for good, it is still tried every 120 s and never holds the watchdog
    async def broken() -> None:
        raise RuntimeError("nope")

    lv.register("weather", max_age_s=60, restart=broken)
    for _ in range(6):
        clock.t += 200
        await lv.repair()
    assert lv.exhausted() == [] and lv._loops["weather"].attempts == 6


async def test_a_task_that_ended_is_dead_whatever_its_beats() -> None:
    lv = Liveness(Clock())

    async def quick() -> None:
        return None

    t = asyncio.create_task(quick())
    await t
    lv.register("inputs", max_age_s=None)
    lv.task("inputs", t)
    assert lv.check() == ["inputs"] and "ended" in (lv.why("inputs") or "")


# ---- in dawn-core ----------------------------------------------------------------------------------------------------
@pytest.fixture()
async def app_ctx(tmp_config):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        yield app, app.state.ctx


@pytest.mark.parametrize(("name", "cls", "attr"), [
    ("display-loop", DisplayService, "_task"),
    ("face-tick", FaceService, "_task"),
    ("timers-tick", TimerService, "_tick_task"),
    ("alarm-engine", AlarmService, "_task"),
])
async def test_a_killed_gating_loop_is_back_within_one_heartbeat(app_ctx, name, cls, attr) -> None:
    app, ctx = app_ctx
    svc = ctx.svc(cls)
    old = getattr(svc, attr)
    old.cancel()
    await asyncio.sleep(0)
    assert name in ctx.liveness.check()
    await ctx.svc(SysInfoService)._tick()
    new = getattr(svc, attr)
    assert new is not old and not new.done()
    assert name not in ctx.liveness.check()
    rep = [e for e in ctx.db.recent_events(50) if e["kind"] == "supervisor_repair"]
    assert rep and rep[0]["what"] == f"dawn-core: {name}"


async def test_the_state_pump_is_back_within_one_heartbeat(app_ctx) -> None:
    app, ctx = app_ctx
    ctx.store._task.cancel()
    await asyncio.sleep(0)
    assert "state-pump" in ctx.liveness.check() and not alarms_alive(ctx)
    await ctx.svc(SysInfoService)._tick()
    assert not ctx.store._task.done() and "state-pump" not in ctx.liveness.check()


async def test_a_gating_loop_that_keeps_dying_starves_the_watchdog_and_the_face_knows(app_ctx, monkeypatch) -> None:
    app, ctx = app_ctx
    ctx.config.system.watchdog = True
    told: list[str] = []
    monkeypatch.setattr(sysinfo, "sd_notify", lambda msg: told.append(msg))
    clock = Clock()
    ctx.liveness._clock = clock
    disp: DisplayService = ctx.svc(DisplayService)

    async def raising_tick() -> None:
        raise RuntimeError("bus error")

    async def broken_restart() -> None:
        disp._task = asyncio.create_task(asyncio.sleep(0))  # a loop that ends straight away, every time
        ctx.liveness.task("display-loop", disp._task)
        await asyncio.sleep(0.01)

    ctx.liveness._loops["display-loop"].restart = broken_restart
    disp._task.cancel()
    await asyncio.sleep(0)
    si: SysInfoService = ctx.svc(SysInfoService)
    for step in (0, 6, 31, 121):
        clock.t += step
        await si._tick()
    assert ctx.store.state.alarms.engine_alive is False  # the face rings by itself if an alarm comes now
    assert ctx.liveness.exhausted() == ["display-loop"]
    told.clear()
    await si._tick()
    assert "WATCHDOG=1" not in told  # systemd restarts dawn-core
    async with __import__("httpx").AsyncClient(transport=__import__("httpx").ASGITransport(app=app), base_url="http://t") as c:
        h = (await c.get("/api/health")).json()
    assert "display-loop" in h["loops_dead"] and h["alarms_alive"] is False


async def test_a_dead_loop_that_does_not_gate_still_feeds_the_watchdog(app_ctx, monkeypatch) -> None:
    app, ctx = app_ctx
    ctx.config.system.watchdog = True
    told: list[str] = []
    monkeypatch.setattr(sysinfo, "sd_notify", lambda msg: told.append(msg))
    ctx.liveness._loops["weather"].restart = None  # nothing to bring it back
    from dawn_core.weather.service import WeatherService

    t = ctx.svc(WeatherService)._task
    t.cancel()
    await asyncio.wait([t], timeout=2)  # it may be part way through a fetch
    await ctx.svc(SysInfoService)._tick()
    assert "weather" in ctx.liveness.check()
    assert "WATCHDOG=1" in told and ctx.store.state.alarms.engine_alive is True
