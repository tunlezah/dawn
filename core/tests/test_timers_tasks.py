"""Timers and background tasks (robustness plan WP-C): a nap that cannot be lost, a sleep fade that always ends,
durations on the monotonic clock with a clock-step watch, tasks that are held and heard from, an input pump that
keeps up, staleness that a clock step cannot fake, and the alarm table read from memory."""

from __future__ import annotations

import asyncio
import gc
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from test_alarm_robustness import AT, BEFORE
from test_supervisor import FakeHost, restarts

from dawn_core import tasks
from dawn_core.alarms.service import ROWS_FRESH_S, AlarmService
from dawn_core.alarms.timers import ClockWatch, TimerService
from dawn_core.app import create_app
from dawn_core.audio.service import AudioService
from dawn_core.config import ConfigManager
from dawn_core.db.models import AlarmRow
from dawn_core.inputs.service import InputService
from dawn_core.system import supervisor as sup
from dawn_core.system.supervisor import SupervisorService


@pytest.fixture()
async def app_ctx(tmp_config):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        ctx = app.state.ctx
        ctx.svc(AlarmService)._task.cancel()  # ticks by hand
        yield ctx


def _events(ctx, kind):
    return [e for e in ctx.db.recent_events(300) if e["kind"] == kind]


async def _until(pred, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


class Waits:
    """Stands in for the timers' monotonic wait: records each wait and returns at once."""

    def __init__(self) -> None:
        self.seen: list[float] = []
        self.on_wait = None

    async def __call__(self, seconds: float) -> None:
        self.seen.append(seconds)
        if self.on_wait:
            self.on_wait(seconds)
        await asyncio.sleep(0)


# ---- C1: the nap ------------------------------------------------------------------------------------------------
async def test_a_nap_whose_ring_fails_twice_still_rings_and_stays_saved_meanwhile(app_ctx, monkeypatch) -> None:
    ctx = app_ctx
    timers: TimerService = ctx.svc(TimerService)
    alarms: AlarmService = ctx.svc(AlarmService)
    real, calls = alarms.ring_nap, []

    async def flaky():
        calls.append(1)
        if len(calls) <= 2:
            raise RuntimeError("the arbiter would not take it")
        return await real()

    monkeypatch.setattr(alarms, "ring_nap", flaky)
    saved_while_retrying, restored = [], []

    def during(seconds: float) -> None:
        if seconds == 5.0:  # a retry wait: the nap must still be saved, so a restart now would ring it
            saved_while_retrying.append(ctx.db.get("timers.nap"))
            fresh = TimerService(ctx)  # what dawn-core restarting at this moment would do
            fresh._restore_nap()
            restored.append(fresh._nap_deadline is not None)
            if fresh._nap_task:
                fresh._nap_task.cancel()

    waits = Waits()
    waits.on_wait = during
    timers._sleep = waits
    await timers.start_nap(20)
    await _until(lambda: timers._nap_task is not None and timers._nap_task.done())
    assert len(calls) == 3 and waits.seen[0] == pytest.approx(1200, abs=1)
    assert len(saved_while_retrying) == 2 and all(isinstance(d, dict) for d in saved_while_retrying)
    assert restored == [True, True]
    assert alarms.ring is not None and alarms.ring.req.kind == "nap"  # it rang
    assert ctx.db.get("timers.nap") is None and ctx.store.state.timers.nap is None
    assert [e["attempt"] for e in _events(ctx, "nap_ring_failed")] == [2, 1]
    await alarms.stop_ringing()


async def test_a_nap_whose_ring_never_starts_sounds_the_backup_tone_directly(app_ctx, monkeypatch) -> None:
    ctx = app_ctx
    timers: TimerService = ctx.svc(TimerService)
    alarms: AlarmService = ctx.svc(AlarmService)

    async def broken():
        raise RuntimeError("no ring for you")

    tone = []

    async def start():
        tone.append("on")

    async def stop():
        tone.append("off")

    monkeypatch.setattr(alarms, "ring_nap", broken)
    monkeypatch.setattr(alarms.buzzer, "start", start)
    monkeypatch.setattr(alarms.buzzer, "stop", stop)
    timers._sleep = Waits()
    await timers.start_nap(5)
    await _until(lambda: timers._nap_task.done())
    r = ctx.store.state.alarms.ringing
    assert tone == ["on"] and r is not None and r.face_beep and r.tier == "buzzer" and r.kind == "nap"
    assert len(_events(ctx, "nap_ring_failed")) == 4
    assert await alarms.stop_ringing()  # the button, a hold or the web ends it like a ring
    assert tone == ["on", "off"] and ctx.store.state.alarms.ringing is None


# ---- C2: the sleep fade -------------------------------------------------------------------------------------------
async def test_a_sleep_fade_whose_volume_steps_fail_still_stops_the_radio(app_ctx, monkeypatch) -> None:
    ctx = app_ctx
    timers: TimerService = ctx.svc(TimerService)
    audio: AudioService = ctx.svc(AudioService)
    await audio.play("chime:birds", level="sleep", remember=False)
    assert audio.arbiter.slot("sleep") is not None
    ctx.config.timers.sleep_fade_s = 1

    async def hung_pipewire(*a, **k):
        raise TimeoutError("wpctl did not answer")

    monkeypatch.setattr(audio, "set_volume", hung_pipewire)
    timers._sleep = Waits()
    timers._sleep_total, timers._sleep_deadline = 2, time.monotonic() + 2
    await timers._run_sleep()  # the old fade raised out of its loop here and never stopped the source
    assert audio.arbiter.slot("sleep") is None
    assert timers._sleep_deadline is None and ctx.store.state.timers.sleep is None


# ---- C3: durations on the monotonic clock ---------------------------------------------------------------------------
class Clocks:
    def __init__(self, ctx, timers: TimerService) -> None:
        self.mono, self.offset = 5000.0, timedelta(0)
        real_now = ctx.store.now
        timers._mono = lambda: self.mono
        ctx.store.now = lambda: real_now() + self.offset  # type: ignore[method-assign]


@pytest.mark.parametrize("step_s", [3600, -600])
async def test_a_clock_step_does_not_move_a_nap(app_ctx, step_s: int) -> None:
    ctx = app_ctx
    timers: TimerService = ctx.svc(TimerService)
    clocks = Clocks(ctx, timers)
    release = asyncio.Event()
    waits: list[float] = []

    async def wait(seconds: float) -> None:
        waits.append(seconds)
        await release.wait()

    timers._sleep = wait
    await timers.start_nap(20)
    await asyncio.sleep(0)
    timers.watch_clock(wall=1_000_000.0, mono=clocks.mono)
    clocks.mono += 60
    clocks.offset += timedelta(seconds=step_s)  # chrony steps the wall clock
    step = timers.watch_clock(wall=1_000_000.0 + 60 + step_s, mono=clocks.mono)
    assert step == pytest.approx(step_s)
    nap = ctx.store.state.timers.nap
    assert nap is not None and nap.remaining_s == 19 * 60  # still 19 minutes, whichever way the clock jumped
    assert datetime.fromisoformat(nap.ends_at) == pytest.approx(ctx.store.now() + timedelta(minutes=19), abs=timedelta(seconds=2))
    assert datetime.fromisoformat(ctx.db.get("timers.nap")["ends_at"]) == pytest.approx(ctx.store.now() + timedelta(minutes=19), abs=timedelta(seconds=2))
    assert waits == [pytest.approx(1200)]  # it rings 20 minutes after it was set, on the monotonic clock
    assert [e["step_s"] for e in _events(ctx, "clock_step")] == [pytest.approx(step_s, abs=0.2)]
    await timers.cancel_nap()


def test_clockwatch_ignores_jitter_and_reports_steps() -> None:
    w = ClockWatch()
    assert w.check(100.0, 10.0) is None
    assert w.check(101.5, 11.0) is None  # half a second of drift or scheduling: not a step
    assert w.check(3702.0, 12.0) == pytest.approx(3599.5)
    assert w.check(3703.0, 13.0) is None


async def test_a_snooze_shown_after_a_clock_step_still_ends_on_time(app_ctx) -> None:
    ctx = app_ctx
    alarms: AlarmService = ctx.svc(AlarmService)
    await alarms.test_ring(source="chime:birds", label="Snoozy", volume=30)
    await alarms.ring.settled()
    await alarms.snooze()
    real_now = ctx.store.now
    ctx.store.now = lambda: real_now() + timedelta(hours=1)  # type: ignore[method-assign]
    alarms.ring.on_clock_step()
    shown = datetime.fromisoformat(ctx.store.state.alarms.ringing.snoozed_until)
    left = (shown - ctx.store.now()).total_seconds()
    assert 8 * 60 < left <= 9 * 60  # still about nine minutes from now, not eight minutes ago
    ctx.store.now = real_now  # type: ignore[method-assign]
    await alarms.stop_ringing()


# ---- C4: tasks that are held and heard from -------------------------------------------------------------------------
async def test_spawn_holds_its_task_and_logs_its_failure(caplog) -> None:
    done = asyncio.Event()
    flag = []

    async def quiet() -> None:
        await done.wait()
        flag.append(1)

    tasks.spawn(quiet(), name="held")  # nothing else holds it
    gc.collect()
    done.set()
    await _until(lambda: flag == [1])

    async def broken() -> None:
        raise ValueError("boom")

    before = tasks.failure_count()
    t = tasks.spawn(broken(), name="broken-one", owner="tests")
    await asyncio.sleep(0.01)
    assert t.done() and tasks.failure_count() == before + 1
    last = tasks.failures()[-1]
    assert last["name"] == "broken-one" and "ValueError: boom" in last["error"]
    assert "broken-one" in caplog.text

    async def cancelled() -> None:
        await asyncio.sleep(10)

    c = tasks.spawn(cancelled(), name="cancelled-one")
    c.cancel()
    await asyncio.sleep(0.01)
    assert tasks.failure_count() == before + 1  # a cancel is not a failure


# ---- C5: the input pump --------------------------------------------------------------------------------------------
async def test_a_push_behind_ten_turns_on_a_hung_volume_snoozes_at_once(app_ctx) -> None:
    ctx = app_ctx
    inp: InputService = ctx.svc(InputService)
    hang = asyncio.Event()
    got: dict[str, list] = {"volume": [], "snooze": []}

    class Actions:
        async def is_ringing(self) -> bool:
            return True

        async def volume_step(self, direction: int) -> None:
            got["volume"].append(direction)
            await hang.wait()  # wpctl never answers

        async def snooze(self) -> None:
            got["snooze"].append(time.monotonic())

    inp.controller.a = Actions()  # type: ignore[assignment]
    for _ in range(10):
        inp.emit("encoder_cw")
    inp.emit("encoder_push")
    t0 = time.monotonic()
    await _until(lambda: got["snooze"], timeout=1.0)
    assert got["snooze"][0] - t0 < 1.0
    assert got["volume"] == [10]  # the ten detents as one change, not ten calls in a row
    hang.set()


# ---- C6: staleness on the monotonic clock --------------------------------------------------------------------------
async def test_a_clock_step_is_not_a_hung_dawn_timed_nor_an_absent_face(app_ctx, tmp_path, monkeypatch) -> None:
    ctx = app_ctx
    s: SupervisorService = ctx.svc(SupervisorService)
    s._task.cancel()
    s.host = FakeHost(ctx)
    monkeypatch.setattr(ctx, "sim", False)
    ctx.store.state.dab.sdr_present = False
    s.host.states["dawn-face"] = "active"
    hub = ctx.ws_hub
    hub.info["face"] = {"role": "face", "last_seen": time.monotonic(), "connected_at": "x", "remote": None, "agent": ""}
    status = tmp_path / "status.json"
    ctx.config.diagnostics.timed_status_file = str(status)
    status.write_text(json.dumps({"updated_at": datetime.now(UTC).isoformat()}))
    t = 1000.0
    await s.check(now=t)
    real = time.time
    monkeypatch.setattr(sup.time, "time", lambda: real() + 3600)  # chrony steps the clock forward an hour
    import dawn_core.api.ws as ws

    monkeypatch.setattr(ws.time, "time", lambda: real() + 3600)
    status.write_text(json.dumps({"updated_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}))
    await s.check(now=t + 15)
    await s.check(now=t + 15 + s.cfg.face_absent_s + 1)
    assert hub.face_seen()
    assert restarts(s.host) == []


# ---- C7: the alarm table from memory -------------------------------------------------------------------------------
async def test_an_edit_is_seen_at_once_and_an_outside_edit_within_30_s(app_ctx) -> None:
    ctx = app_ctx
    a: AlarmService = ctx.svc(AlarmService)
    mono = [100.0]
    a._mono = lambda: mono[0]
    await a.tick(now=BEFORE)
    from dawn_core.alarms.schemas import AlarmIn

    a.create(AlarmIn(label="Via the API", time="06:30", repeat="daily", source="chime:birds"), now=BEFORE)
    assert [i.label for i in ctx.store.state.alarms.items] == ["Via the API"]  # the very next pass
    with ctx.db.session() as s:  # sqlite3 on the box, behind dawn-core's back
        s.add(AlarmRow(label="Outside", time="07:00", repeat="daily", source="chime:birds", enabled=True, last_fired_occurrence=BEFORE.isoformat()))
        s.commit()
    await a.tick(now=BEFORE + timedelta(seconds=1))
    assert len(a.rows_safe()) == 1  # from memory
    mono[0] += ROWS_FRESH_S + 1
    await a.tick(now=BEFORE + timedelta(seconds=31))
    assert sorted(r.label for r in a.rows_safe()) == ["Outside", "Via the API"]
    await a.tick(now=AT)  # and it rings
    assert a.ring is not None
    await a.stop_ringing()
