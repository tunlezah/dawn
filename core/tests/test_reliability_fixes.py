"""What the reliability assessment (docs/RELIABILITY-ASSESSMENT.md) found, pinned: a dead mpv is not "flowing",
a stopped source lets go of its player, a hung backup-tone player is given up on, helpers that time out are
killed, a failing engine tick is not a tick, the event log is pruned, and a wedged PipeWire is restarted."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import func, select

from dawn_core.alarms import buzzer as bz
from dawn_core.alarms.service import AlarmService
from dawn_core.app import create_app
from dawn_core.audio import backend as audio_backend
from dawn_core.audio.arbiter import Slot
from dawn_core.audio.backend import AudioBackend
from dawn_core.audio.player import MpvPlayer
from dawn_core.audio.service import AudioService
from dawn_core.audio.sources import PlayerSource
from dawn_core.config import ConfigManager
from dawn_core.db.models import EventRow
from dawn_core.net import nmcli
from dawn_core.system import supervisor as sup
from dawn_core.system.sysinfo import SysInfoService, alarms_alive


@pytest.fixture()
async def app(tmp_config):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        yield app


class _DeadProc:
    returncode = -9


def _playing_mpv(path: Path) -> MpvPlayer:
    p = MpvPlayer("dawn-alarm-chime", path)
    p._loaded_url = "/x.ogg"
    p.props.update({"pause": False, "idle-active": False, "core-idle": False, "paused-for-cache": False})
    return p


# ---- 1. a dead mpv ------------------------------------------------------------------------------------------------
def test_a_dead_mpv_is_neither_playing_nor_flowing(tmp_path: Path) -> None:
    p = _playing_mpv(tmp_path)

    class _Live:
        returncode = None

    p.proc = _Live()
    assert p.alive and p.playing and p.flowing
    p.proc = _DeadProc()
    assert not p.alive and not p.playing and not p.flowing


async def test_the_alarm_level_is_not_flowing_on_a_dead_mpv_whatever_the_backend_says(app) -> None:
    audio: AudioService = app.state.ctx.svc(AudioService)
    audio.backend = AudioBackend()  # ALSA / none: stream_running() answers None ("cannot tell")
    p = _playing_mpv(app.state.ctx.runtime_dir)
    p.proc = _DeadProc()
    src = PlayerSource(p, "/x.ogg")
    src._started = True
    slot = Slot(level="alarm", priority=100, source=src, state="playing")
    assert await audio._slot_flowing(slot) is False


async def test_a_closed_ipc_resets_what_mpv_reported_and_tells_the_owner(tmp_path: Path) -> None:
    p = _playing_mpv(tmp_path)

    class _Live:
        returncode = None

    p.proc = _Live()
    told = []
    p.on_change(lambda: told.append(True))
    reader = asyncio.StreamReader()
    reader.feed_data(b'{"event":"property-change","name":"core-idle","data":false}\n')
    reader.feed_eof()
    p._reader = reader
    fut = asyncio.get_running_loop().create_future()
    p._pending[7] = fut
    await p._rx()
    assert p._ipc_lost and not p.alive and not p.playing and not p.flowing
    assert p.props == MpvPlayer.IDLE_PROPS and p.props is not MpvPlayer.IDLE_PROPS
    assert fut.done() and isinstance(fut.exception(), RuntimeError)
    assert told  # the arbiter republishes, the ring's watchdog sees silence
    with pytest.raises(RuntimeError):
        await p._cmd("stop")


async def test_an_ipc_reader_that_raises_marks_the_player_dead(tmp_path: Path) -> None:
    p = _playing_mpv(tmp_path)

    class _Live:
        returncode = None

    p.proc = _Live()

    class _Broken:
        async def readline(self):
            raise ValueError("line too long")

    p._reader = _Broken()  # type: ignore[assignment]
    await p._rx()
    assert p._ipc_lost and not p.alive


# ---- 2. listeners do not accumulate -----------------------------------------------------------------------------
async def test_a_stopped_source_lets_go_of_its_player(app) -> None:
    audio: AudioService = app.state.ctx.svc(AudioService)
    for _ in range(40):
        await audio.play("chime:gentle_bell", level="alarm", remember=False)
        await audio.stop_level("alarm")
    player = audio.players["dawn-alarm-chime"]
    assert player._listeners == []
    slot = await audio.play("chime:gentle_bell", level="alarm", remember=False)
    assert len(player._listeners) == 1
    # pause/resume (a snooze) keep the listener; a second start after a stop adds it back once
    await audio.arbiter.pause_level("alarm")
    await audio.arbiter.resume_level("alarm")
    assert len(player._listeners) == 1
    await slot.source.stop()
    await slot.source.start()
    await slot.source.start()
    assert len(player._listeners) == 1
    await audio.stop_level("alarm")
    assert player._listeners == []


def test_on_change_is_idempotent_and_off_change_is_forgiving(tmp_path: Path) -> None:
    p = MpvPlayer("x", tmp_path)

    def fn() -> None:
        pass

    p.on_change(fn)
    p.on_change(fn)
    assert p._listeners == [fn]
    p.off_change(fn)
    p.off_change(fn)
    assert p._listeners == []


# ---- 3. a hung backup-tone player ------------------------------------------------------------------------------
async def test_a_hung_tone_player_is_given_up_on_and_the_next_one_plays(app, monkeypatch) -> None:
    ctx = app.state.ctx
    ctx.sim = False
    monkeypatch.setattr(bz, "PLAY_MAX_S", 1.0)
    hang = ["sh", "-c", "exec sleep 1000", "x"]
    plays = ["sh", "-c", "sleep 1.2", "x"]
    monkeypatch.setattr(bz, "player_commands", lambda wav, which=None, cards=None: [[*hang, wav], [*plays, wav]])
    b = bz.Buzzer(ctx)
    await b.start()
    try:
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and not (b.sounding and b.player and b.player.startswith("sh -c sleep 1.2")):
            await asyncio.sleep(0.05)
        assert b.sounding and b.player.startswith("sh -c sleep 1.2"), b.problem
        assert " ".join([*hang, str(b.wav_path())]) in b._hung  # it goes last for a while
        await asyncio.sleep(0.5)
        for d in Path("/proc").iterdir():
            if d.name.isdigit():
                try:
                    assert b"exec sleep 1000" not in (d / "cmdline").read_bytes(), "the hung player was not killed"
                except OSError:
                    pass
    finally:
        await b.stop()


# ---- 4. helpers that time out are killed ------------------------------------------------------------------------
def _running(cmd: str) -> list[int]:
    out = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                if (d / "cmdline").read_bytes().replace(b"\0", b" ").strip() == cmd.encode():
                    out.append(int(d.name))
            except OSError:
                pass
    return out


@pytest.mark.parametrize("run", [audio_backend.run, nmcli.run])
async def test_a_helper_that_times_out_is_killed(run) -> None:
    rc, _ = await run("sleep", "41", timeout=0.2)
    assert rc == 124
    await asyncio.sleep(0.3)
    left = _running("sleep 41")
    for pid in left:
        os.kill(pid, 9)
    assert not left


async def test_backend_run_reports_a_missing_command() -> None:
    rc, out = await audio_backend.run("/nonexistent/wpctl", "status")
    assert rc == 127 and "not found" in out


# ---- 6. a failing tick is not a tick ----------------------------------------------------------------------------
async def test_a_tick_that_keeps_failing_starves_the_watchdog_and_shows_on_the_face(app, monkeypatch) -> None:
    ctx = app.state.ctx
    a: AlarmService = ctx.svc(AlarmService)
    assert a.alive() and ctx.store.state.alarms.engine_alive
    await asyncio.sleep(1.2)
    assert a.last_tick is not None

    async def broken(now=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(a, "tick", broken)
    before = a.last_tick
    await asyncio.sleep(2.5)
    assert a.last_tick == before and a.tick_failures >= 2
    assert a.alive(max_age_s=2.0) is False
    monkeypatch.setattr(a, "alive", lambda max_age_s=30.0: False)
    await ctx.svc(SysInfoService)._tick()
    assert ctx.store.state.alarms.engine_alive is False
    assert alarms_alive(ctx) is False
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/health")
        assert r.status_code == 200 and r.json()["ok"] and r.json()["alarms_alive"] is False
        assert (await c.get("/api/state")).json()["alarms"]["engine_alive"] is False


async def test_a_tick_that_works_again_counts_as_alive(app, monkeypatch) -> None:
    a: AlarmService = app.state.ctx.svc(AlarmService)
    calls = []
    orig = a.tick

    async def flaky(now=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("once")
        await orig(now)

    a._task.cancel()
    monkeypatch.setattr(a, "tick", flaky)
    a.last_tick = None
    a._task = asyncio.create_task(a._loop())
    await asyncio.sleep(1.5)
    assert len(calls) >= 2 and a.last_tick is not None and a.tick_failures == 0 and a.alive()


# ---- 7. events are pruned ---------------------------------------------------------------------------------------
async def test_old_events_are_pruned_and_recent_ones_kept(app) -> None:
    db = app.state.ctx.db
    db.log_event("probe_recent")
    with db.session() as s:
        s.add(EventRow(kind="probe_old", at=datetime.now(UTC) - timedelta(days=200)))
        s.commit()
    assert db.prune_events(90) == 1
    with db.session() as s:
        kinds = {r.kind for r in s.exec(select(EventRow)).all()}
    assert "probe_recent" in kinds and "probe_old" not in kinds
    with db.session() as s:
        n = s.exec(select(func.count()).select_from(EventRow)).one()
    assert db.prune_events(90) == 0
    with db.session() as s:
        assert s.exec(select(func.count()).select_from(EventRow)).one() == n


async def test_prune_events_never_raises(app, monkeypatch) -> None:
    db = app.state.ctx.db

    def boom():
        raise RuntimeError("disk gone")

    monkeypatch.setattr(db, "session", boom)
    assert db.prune_events(90) == 0 and db.write_errors == 1


def test_events_days_is_configurable_and_in_the_example_config() -> None:
    from dawn_core.config.schema import DawnConfig

    assert DawnConfig().diagnostics.events_days == 90
    text = (Path(__file__).resolve().parents[2] / "config" / "config.example.yaml").read_text()
    assert "events_days: 90" in text


# ---- supervisor: a PipeWire that answers nothing ----------------------------------------------------------------
async def test_a_pipewire_that_answers_nothing_is_restarted(tmp_config, monkeypatch) -> None:
    from tests.test_supervisor import FakeHost

    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        ctx = app.state.ctx
        s: sup.SupervisorService = ctx.svc(sup.SupervisorService)
        s._task.cancel()
        host = FakeHost(ctx)
        s.host = host
        monkeypatch.setattr(ctx, "sim", False)
        ctx.store.state.dab.sdr_present = False
        host.states["dawn-face"] = "unknown"
        wpctl_rc = {"rc": 124}

        async def run(*args, timeout=5.0):
            if args[0] == "wpctl":
                return wpctl_rc["rc"], "timeout" if wpctl_rc["rc"] == 124 else ""
            return 127, "not here"

        host.run = run  # type: ignore[method-assign]
        t = 1000.0
        await s.check(t)
        await s.check(t + 15)
        await s.check(t + 30)
        assert host.user_restarts == []  # not yet: three checks must agree
        await s.check(t + 45)
        assert host.user_restarts == ["pipewire"]
        assert any(r.what.startswith("pipewire") and "wpctl" in r.reason for r in s.repairs)
        # answering again: no further restart; a later hang starts the count afresh
        wpctl_rc["rc"] = 0
        await s.check(t + 60)
        wpctl_rc["rc"] = 124
        await s.check(t + 75)
        await s.check(t + 90)
        assert host.user_restarts == ["pipewire"]


async def test_a_missing_wpctl_is_not_a_hung_pipewire(tmp_config, monkeypatch) -> None:
    from tests.test_supervisor import FakeHost

    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        ctx = app.state.ctx
        s: sup.SupervisorService = ctx.svc(sup.SupervisorService)
        s._task.cancel()
        host = FakeHost(ctx)
        s.host = host
        monkeypatch.setattr(ctx, "sim", False)
        ctx.store.state.dab.sdr_present = False
        host.states["dawn-face"] = "unknown"
        for i in range(5):
            await s.check(1000.0 + 15 * i)
        assert host.user_restarts == []
