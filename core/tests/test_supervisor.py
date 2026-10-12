"""The supervisor: what dawn-core restarts by itself, when, and how it holds back. A fake host stands in for
systemd, sudo and the user session; the clock is driven by hand."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime

import pytest

from dawn_core.app import create_app
from dawn_core.audio.service import AudioService
from dawn_core.config import ConfigManager
from dawn_core.dab.service import DabService
from dawn_core.diagnostics.host import Host
from dawn_core.services import Service, ServiceRegistry
from dawn_core.system import supervisor as sup
from dawn_core.system.supervisor import SupervisorService


class FakeHost(Host):
    """systemd as the tests want it to look."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.states: dict[str, str] = {u: "active" for u in sup.OWN_UNITS + sup.SYSTEM_UNITS}
        self.enabled: dict[str, bool | None] = {}
        self.user: dict[str, str] | None = {u: "active" for u in sup.USER_UNITS}
        self.active_for: float | None = 3600.0
        self.sudo_calls: list[tuple[str, ...]] = []
        self.user_restarts: list[str] = []
        self.sudo_rc = 0

    async def units(self, names):
        return {n: self.states.get(n, "unknown") for n in names}

    async def unit_enabled(self, name):
        return self.enabled.get(name, True)

    async def unit_active_for(self, name):
        return self.active_for

    async def user_units(self, names):
        return None if self.user is None else {n: self.user.get(n, "inactive") for n in names}

    async def user_restart(self, name):
        self.user_restarts.append(name)
        self.user[name] = "active"
        return 0, ""

    async def sudo(self, *args, timeout=15.0):
        self.sudo_calls.append(args)
        if self.sudo_rc == 0 and args[:2] == ("systemctl", "restart"):
            self.states[args[2]] = "active"
        return self.sudo_rc, "" if self.sudo_rc == 0 else "sudo: a password is required"


@pytest.fixture()
async def sv(tmp_config, monkeypatch):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        ctx = app.state.ctx
        s: SupervisorService = ctx.svc(SupervisorService)
        s._task.cancel()  # checks run by hand
        s.host = FakeHost(ctx)
        monkeypatch.setattr(ctx, "sim", False)  # the fake host plays the Pi
        ctx.store.state.dab.sdr_present = False  # welle is not looked at unless a test says so
        s.host.states["dawn-face"] = "unknown"  # no kiosk in these tests unless one says so (with_face)
        yield s, ctx, s.host


def with_face(host: FakeHost) -> None:
    host.states["dawn-face"] = "active"


def _repairs(ctx):
    return [e for e in ctx.db.recent_events(200) if e["kind"] == "supervisor_repair"]


def restarts(host: FakeHost) -> list[str]:
    return [c[2] for c in host.sudo_calls if c[:2] == ("systemctl", "restart")]


# ---- system units --------------------------------------------------------------
async def test_a_failed_unit_is_restarted_once_and_then_only_after_the_backoff(sv) -> None:
    s, ctx, host = sv
    host.states["gpsd"] = "failed"
    t = 1000.0
    await s.check(now=t)
    assert restarts(host) == ["gpsd"] and host.states["gpsd"] == "active"
    ev = _repairs(ctx)
    assert len(ev) == 1 and ev[0]["what"] == "gpsd" and ev[0]["ok"] and ev[0]["reason"] == "failed"
    assert ctx.store.state.system.services["gpsd"] == "failed"  # as read at the start of the pass
    await s.check(now=t + 5)
    assert ctx.store.state.system.services["gpsd"] == "active"
    host.states["gpsd"] = "failed"  # it fails again right away: not before min_backoff_s has passed
    await s.check(now=t + 10)
    assert restarts(host) == ["gpsd"]
    await s.check(now=t + s.cfg.min_backoff_s + 1)
    assert restarts(host) == ["gpsd", "gpsd"]
    assert s._backoff["unit:gpsd"] == 2 * s.cfg.min_backoff_s  # longer each time it comes back
    host.states["gpsd"] = "failed"
    await s.check(now=t + 2 * s.cfg.min_backoff_s + 2)
    assert restarts(host) == ["gpsd", "gpsd"]  # the second backoff is twice as long


async def test_a_unit_that_is_not_enabled_here_is_left_alone(sv) -> None:
    s, ctx, host = sv
    host.states["shairport-sync"] = "failed"
    host.enabled["shairport-sync"] = False  # AirPlay was never built on this box
    await s.check(now=1000.0)
    assert restarts(host) == []
    host.states["nqptp"] = "failed"
    host.enabled["nqptp"] = None  # systemd could not say
    await s.check(now=2000.0)
    assert restarts(host) == []


async def test_dawns_own_unit_seen_stopped_for_a_while_is_started_again(sv) -> None:
    s, ctx, host = sv
    host.states["dawn-dab"] = "inactive"
    await s.check(now=1000.0)
    assert restarts(host) == []  # an update restarts it for a moment: not yet
    await s.check(now=1000.0 + sup.INACTIVE_FOR_S)
    assert restarts(host) == ["dawn-dab"]
    host.states["chrony"] = "inactive"  # a third-party unit that is merely inactive is not Dawn's to start
    await s.check(now=1000.0 + 2 * sup.INACTIVE_FOR_S)
    assert restarts(host) == ["dawn-dab"]


async def test_a_restart_that_sudo_refuses_is_recorded_as_failed(sv) -> None:
    s, ctx, host = sv
    host.states["chrony"] = "failed"
    host.sudo_rc = 1
    await s.check(now=1000.0)
    ev = _repairs(ctx)
    assert len(ev) == 1 and not ev[0]["ok"] and "password" in ev[0]["message"]
    assert s.recent(24)[0]["ok"] is False


async def test_nothing_is_repaired_with_the_supervisor_off_but_states_still_show(sv) -> None:
    s, ctx, host = sv
    ctx.config.system.supervisor.enabled = False
    host.states["gpsd"] = "failed"
    await s.check(now=1000.0)
    assert restarts(host) == [] and ctx.store.state.system.services["gpsd"] == "failed"


# ---- the face ------------------------------------------------------------------
async def test_a_face_that_never_connects_gets_its_kiosk_restarted(sv) -> None:
    s, ctx, host = sv
    with_face(host)
    t = 1000.0
    await s.check(now=t)
    await s.check(now=t + s.cfg.face_absent_s - 1)
    assert restarts(host) == []
    await s.check(now=t + s.cfg.face_absent_s)
    assert restarts(host) == ["dawn-face"]
    assert _repairs(ctx)[-1]["what"] == "dawn-face" and "no face" in _repairs(ctx)[-1]["reason"]


async def test_a_kiosk_still_starting_or_not_running_is_not_restarted(sv) -> None:
    s, ctx, host = sv
    with_face(host)
    t = 1000.0
    host.active_for = 20.0  # Chromium has had 20 s so far on a slow board
    await s.check(now=t)
    await s.check(now=t + s.cfg.face_absent_s + 10)
    assert restarts(host) == []
    host.active_for = 3600.0
    host.states["dawn-face"] = "activating"  # systemd is on it
    await s.check(now=t + s.cfg.face_absent_s + 20)
    assert restarts(host) == []


async def test_a_connected_face_that_keeps_talking_is_fine_and_a_frozen_one_is_not(sv) -> None:
    s, ctx, host = sv
    with_face(host)
    hub = ctx.ws_hub
    hub.info["ws"] = {"role": "face", "last_seen": time.time(), "connected_at": "x", "remote": None, "agent": ""}
    t = 1000.0
    await s.check(now=t)
    await s.check(now=t + s.cfg.face_absent_s + 5)
    assert restarts(host) == []
    hub.info["ws"]["last_seen"] = time.time() - sup.FACE_STALE_S - 1  # the page froze: no ping for a minute
    await s.check(now=t + 2 * s.cfg.face_absent_s)
    assert restarts(host) == []  # first seen frozen now: it gets face_absent_s
    await s.check(now=t + 3 * s.cfg.face_absent_s)
    assert restarts(host) == ["dawn-face"]


# ---- welle-cli -----------------------------------------------------------------
async def test_a_dab_decoder_that_stops_answering_is_restarted_unless_an_alarm_rings(sv, monkeypatch) -> None:
    s, ctx, host = sv
    dab: DabService = ctx.svc(DabService)
    ctx.store.state.dab.sdr_present = True
    calls: list[str] = []

    async def unreachable() -> bool:
        return False

    async def restart(force=False, reason="unreachable") -> bool:
        calls.append(reason)
        return True

    monkeypatch.setattr(dab.client, "reachable", unreachable)
    monkeypatch.setattr(dab, "restart_welle", restart)
    t = 1000.0
    await s.check(now=t)
    await s.check(now=t + s.cfg.welle_dead_s - 1)
    assert calls == []
    await s.check(now=t + s.cfg.welle_dead_s)
    assert calls == ["supervisor"]
    # ringing: the ring's own watch restarts welle, with its own timing
    from dawn_core.state.ui import RingingInfo

    ctx.store.state.alarms.ringing = RingingInfo(kind="alarm", label="A", started_at="x", source="dab:1002")
    await s.check(now=t + s.cfg.welle_dead_s + 2 * s.cfg.min_backoff_s + 100)
    assert calls == ["supervisor"]
    ctx.store.state.alarms.ringing = None
    ctx.store.state.dab.sdr_present = False  # no stick: welle cannot run, systemd loops it, nothing to do here
    await s.check(now=t + 10_000)
    await s.check(now=t + 10_000 + s.cfg.welle_dead_s)
    assert calls == ["supervisor"]


# ---- PipeWire ------------------------------------------------------------------
async def test_a_failed_pipewire_session_service_is_restarted_and_audio_set_up_again(sv, monkeypatch) -> None:
    s, ctx, host = sv
    audio: AudioService = ctx.svc(AudioService)
    again: list[str] = []

    async def refresh(select_now=False):
        again.append("sinks")

    async def eq():
        again.append("eq")

    monkeypatch.setattr(audio, "refresh_sinks", refresh)
    monkeypatch.setattr(audio, "apply_eq", eq)
    host.user["wireplumber"] = "inactive"
    host.user["pipewire"] = "inactive"  # socket-activated: inactive is fine
    t = 1000.0
    await s.check(now=t)
    assert host.user_restarts == []
    await s.check(now=t + sup.USER_FAILED_FOR_S)
    assert host.user_restarts == ["wireplumber"] and again == ["sinks", "eq"]
    assert ctx.store.state.system.services["wireplumber"] == "activating"
    await s.check(now=t + sup.USER_FAILED_FOR_S + 5)
    assert ctx.store.state.system.services["wireplumber"] == "active"
    host.user = None  # no user manager (a laptop): nothing to do and nothing logged twice
    await s.check(now=t + 1000)
    await s.check(now=t + 2000)
    assert host.user_restarts == ["wireplumber"]


# ---- dawn-timed ----------------------------------------------------------------
async def test_a_dawn_timed_that_stopped_writing_its_status_is_restarted(sv, tmp_path) -> None:
    s, ctx, host = sv
    status = tmp_path / "status.json"
    ctx.config.diagnostics.timed_status_file = str(status)
    t = 1000.0
    await s.check(now=t)
    assert restarts(host) == []  # no file (an older dawn-timed): nothing to judge by
    status.write_text(json.dumps({"updated_at": datetime.now(UTC).isoformat()}))
    await s.check(now=t + 20)
    assert restarts(host) == []
    old = datetime.fromtimestamp(time.time() - s.cfg.timed_stale_s - 5, UTC).isoformat()
    status.write_text(json.dumps({"updated_at": old}))
    await s.check(now=t + 40)
    assert restarts(host) == ["dawn-timed"]
    host.states["dawn-timed"] = "activating"
    await s.check(now=t + 40 + s.cfg.min_backoff_s + 1)
    assert restarts(host) == ["dawn-timed"]  # systemd is starting it: not again


# ---- the backoff heals, and the pass survives a broken check ------------------------
async def test_fine_for_ten_minutes_starts_the_backoff_over(sv) -> None:
    s, ctx, host = sv
    t = 1000.0
    host.states["gpsd"] = "failed"
    await s.check(now=t)
    host.states["gpsd"] = "failed"
    await s.check(now=t + s.cfg.min_backoff_s + 1)
    assert s._backoff["unit:gpsd"] == 2 * s.cfg.min_backoff_s
    await s.check(now=t + 1000)
    await s.check(now=t + 1000 + sup.HEAL_S)
    assert "unit:gpsd" not in s._backoff
    host.states["gpsd"] = "failed"
    await s.check(now=t + 1000 + sup.HEAL_S + 1)
    assert s._backoff["unit:gpsd"] == s.cfg.min_backoff_s


async def test_one_broken_check_does_not_stop_the_others(sv, monkeypatch) -> None:
    s, ctx, host = sv

    async def boom(now):
        raise RuntimeError("no")

    monkeypatch.setattr(s, "_face", boom)
    host.states["gpsd"] = "failed"
    await s.check(now=1000.0)
    assert restarts(host) == ["gpsd"]


# ---- services inside dawn-core ---------------------------------------------------------
class Flaky(Service):
    name = "flaky"

    def __init__(self, fail_times: int):
        self.left = fail_times
        self.starts = 0

    async def start(self) -> None:
        self.starts += 1
        if self.left > 0:
            self.left -= 1
            raise RuntimeError("not ready yet")


async def test_a_service_that_could_not_start_at_boot_is_tried_again_with_a_backoff(monkeypatch) -> None:
    reg = ServiceRegistry()
    f = reg.add(Flaky(2))
    await reg.start_all()
    assert f.starts == 1 and "flaky" in reg.failed and reg.retries["flaky"] == 1
    assert await reg.retry_failed() == [] and f.starts == 1  # not before its minute is up
    t = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: t + 61)
    assert await reg.retry_failed() == [] and f.starts == 2 and reg.retries["flaky"] == 2
    monkeypatch.setattr(time, "monotonic", lambda: t + 61 + 100)
    assert await reg.retry_failed() == [] and f.starts == 2  # the second wait is two minutes
    monkeypatch.setattr(time, "monotonic", lambda: t + 61 + 121)
    assert await reg.retry_failed() == ["flaky"] and f.starts == 3
    assert "flaky" not in reg.failed and "flaky" in reg.recovered


async def test_the_supervisor_reports_a_service_that_came_good(sv, monkeypatch) -> None:
    s, ctx, host = sv
    f = Flaky(1)
    ctx.registry.add(f)
    await ctx.registry._start(f)
    assert "flaky" in ctx.registry.failed
    ctx.registry._next_try["flaky"] = 0.0
    await s.check(now=1000.0)
    assert "flaky" not in ctx.registry.failed
    assert any(e["what"] == "dawn-core: flaky" for e in _repairs(ctx))


async def test_a_start_tried_again_does_not_run_a_second_engine(sv) -> None:
    from dawn_core.alarms.service import AlarmService
    from dawn_core.alarms.timers import TimerService
    from dawn_core.face.service import FaceService

    s, ctx, host = sv
    a: AlarmService = ctx.svc(AlarmService)
    engine, ticks, face = a._task, ctx.svc(TimerService)._tick_task, ctx.svc(FaceService)._task
    subs = len(ctx.store._subs)
    await a.start()
    await ctx.svc(TimerService).start()
    await ctx.svc(FaceService).start()
    assert a._task is engine and ctx.svc(TimerService)._tick_task is ticks and ctx.svc(FaceService)._task is face
    assert len(ctx.store._subs) == subs
    await asyncio.sleep(0)
