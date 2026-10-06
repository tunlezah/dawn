"""The alarm must ring whatever else is broken: the database, one bad row, the source, the chime, mpv, a restart
of dawn-core, a snooze or stop that lands while the sound is still starting. Each test pins one of those."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from dawn_core.alarms import ringing
from dawn_core.alarms.ringing import RingRequest
from dawn_core.alarms.schemas import AlarmIn
from dawn_core.alarms.service import AlarmService, RingBusy, parse_days
from dawn_core.app import create_app
from dawn_core.audio.service import AudioService
from dawn_core.audio.sources import AudioSource
from dawn_core.config import ConfigManager
from dawn_core.dab.service import DabService, TunerBusy
from dawn_core.db.models import AlarmRow, DabServiceRow

SYD = ZoneInfo("Australia/Sydney")
BEFORE = datetime(2026, 10, 1, 12, 0, tzinfo=SYD)
AT = datetime(2026, 10, 2, 6, 30, 3, tzinfo=SYD)


@pytest.fixture()
async def svc(tmp_config):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        ctx = app.state.ctx
        a: AlarmService = ctx.svc(AlarmService)
        a._task.cancel()  # drive ticks by hand
        yield a, ctx


@pytest.fixture()
def fast(monkeypatch):
    """Shrink the ladder's floors so climbs happen within a second or two."""
    monkeypatch.setattr(ringing, "MIN_HEARD_S", {"source": 0.5, "chime": 0.5})
    monkeypatch.setattr(ringing, "DROPOUT_S", {"source": 1.0, "chime": 1.0})
    monkeypatch.setattr(ringing, "BUZZER_RETRY_S", 0.2)


def _events(ctx, kind):
    return [e for e in ctx.db.recent_events(300) if e["kind"] == kind]


async def _until(pred, timeout: float = 6.0) -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


class FakeSource(AudioSource):
    """A source whose start can be slow and whose audio can be made to flow or not."""

    kind = "url"  # type: ignore[assignment]

    def __init__(self, delay: float = 0.0, flows: bool = True, fail: bool = False):
        super().__init__()
        self.ref, self.label = "fake:x", "fake"
        self.delay, self.flows, self.fail = delay, flows, fail
        self._playing = False

    async def start(self) -> None:
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("source broke")
        self._playing = True

    async def stop(self) -> None:
        self._playing = False

    async def pause(self) -> None:
        self._playing = False

    async def resume(self) -> None:
        self._playing = True

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def flowing(self) -> bool:
        return self._playing and self.flows


def _factory(audio: AudioService, scheme: str, **kw):
    made: list[FakeSource] = []

    async def make(arg: str, level: str = "user") -> AudioSource:
        src = FakeSource(**kw)
        made.append(src)
        return src

    audio.register_factory(scheme, make)
    return made


def _broken(audio: AudioService, scheme: str) -> None:
    async def make(arg: str, level: str = "user") -> AudioSource:
        raise RuntimeError("mpv not found")

    audio.register_factory(scheme, make)


# ---- the ladder: source -> chime -> backup tone ------------------------------
async def test_alarm_level_has_its_own_players(svc) -> None:
    """An alarm on the same kind of source as what was playing must not share its player: the preempted source's
    duck would pause the alarm two seconds in."""
    a, ctx = svc
    audio: AudioService = ctx.svc(AudioService)
    audio.arbiter.duck_seconds = 0.05
    await audio.play("url:http://radio.invalid/user")
    a.create(AlarmIn(label="U", time="06:30", repeat="daily", source="url:http://radio.invalid/alarm", ramp_seconds=0), now=BEFORE)
    await a.tick(now=AT)
    await a.ring.settled()
    await asyncio.sleep(0.3)
    alarm_player, user_player = audio.players["dawn-alarm-media"], audio.players["dawn-media"]
    assert alarm_player is not user_player
    assert alarm_player.loaded_url == "http://radio.invalid/alarm" and alarm_player.playing
    assert audio.arbiter.slot("user").state == "paused" and not user_player.playing
    await a.stop_ringing()
    await _until(lambda: user_player.playing)  # the radio resumes after the alarm


async def test_source_that_cannot_start_falls_to_the_chime_at_once(svc, fast) -> None:
    a, ctx = svc
    _broken(ctx.svc(AudioService), "url")
    await a.start_ring(RingRequest(kind="alarm", label="B", source="url:http://x.invalid/", volume=50, ramp_seconds=0))
    await _until(lambda: a.ring.tier == "chime")
    await a.ring.settled()
    r = ctx.store.state.alarms.ringing
    assert r.tier == "chime" and r.fallback and r.source.startswith("chime:") and "mpv not found" in r.fallback_reason
    assert ctx.svc(AudioService).arbiter.slot("alarm").source.kind == "chime"
    await a.stop_ringing()


async def test_silent_source_then_silent_chime_reach_the_backup_tone(svc, fast) -> None:
    a, ctx = svc
    audio: AudioService = ctx.svc(AudioService)
    _factory(audio, "fake", flows=False)
    chimes = _factory(audio, "chime", flows=False)
    await a.start_ring(RingRequest(kind="alarm", label="S", source="fake:x", volume=55, ramp_seconds=0, fallback_after_s=1, buzzer_after_s=1))
    await _until(lambda: a.ring.tier == "chime")
    assert chimes  # the chime was tried
    await _until(lambda: a.ring.tier == "buzzer")
    await a.ring.settled()
    r = ctx.store.state.alarms.ringing
    assert r.tier == "buzzer" and r.face_beep is True and r.source == "buzzer:"
    slot = audio.arbiter.slot("alarm")
    assert slot.source.kind == "buzzer" and a.buzzer.active
    assert audio.volume == 55  # the backup tone goes straight to the alarm's volume
    tiers = [e.get("tier") for e in _events(ctx, "ring_fallback")]
    assert "chime" in tiers and "buzzer" in tiers
    await a.stop_ringing()
    assert not a.buzzer.active and ctx.store.state.alarms.ringing is None


async def test_audio_that_stops_mid_ring_climbs(svc, fast) -> None:
    a, ctx = svc
    audio: AudioService = ctx.svc(AudioService)
    made = _factory(audio, "fake", flows=True)
    await a.start_ring(RingRequest(kind="alarm", label="D", source="fake:x", volume=50, ramp_seconds=0, fallback_after_s=1))
    await a.ring.settled()
    await _until(lambda: ctx.store.state.alarms.ringing.audible is True)
    made[0].flows = False  # e.g. the DAB signal goes, or mpv dies
    await _until(lambda: a.ring.tier == "chime")
    assert "stopped" in a.ring.fallback_reason
    await a.stop_ringing()


async def test_a_chime_alarm_skips_straight_to_the_backup_tone(svc, fast) -> None:
    a, ctx = svc
    _factory(ctx.svc(AudioService), "chime", flows=False)
    await a.start_ring(RingRequest(kind="alarm", label="C", source="chime:birds", volume=50, ramp_seconds=0, buzzer_after_s=1))
    await _until(lambda: a.ring.tier == "buzzer")
    assert [e.get("from_tier") for e in _events(ctx, "ring_fallback")] == ["source"]
    await a.stop_ringing()


async def test_everything_broken_still_reaches_the_backup_tone(svc, fast) -> None:
    """mpv missing (no source, no chime) and the arbiter refusing the tone: the buzzer starts directly."""
    a, ctx = svc
    audio: AudioService = ctx.svc(AudioService)
    _broken(audio, "url")
    _broken(audio, "chime")
    _broken(audio, "buzzer")
    await a.start_ring(RingRequest(kind="alarm", label="X", source="url:http://x.invalid/", volume=50, ramp_seconds=0))
    await _until(lambda: a.buzzer.active)
    assert a.ring.tier == "buzzer" and ctx.store.state.alarms.ringing.face_beep
    await a.stop_ringing()
    assert not a.buzzer.active


async def test_ramp_carries_across_rungs_and_yields_to_the_knob(svc, fast) -> None:
    a, ctx = svc
    audio: AudioService = ctx.svc(AudioService)
    _factory(audio, "fake", flows=True)
    await audio.set_volume(30, overlay=False)
    await a.start_ring(RingRequest(kind="alarm", label="R", source="fake:x", volume=80, ramp_seconds=10, ramp_start_percent=10))
    await a.ring.settled()
    await asyncio.sleep(1.2)
    v = audio.volume
    assert 10 < v < 80  # ramping (10 s: room for a slow machine)
    await audio.set_volume(25, overlay=False)  # turned down by hand
    await asyncio.sleep(1.5)
    assert audio.volume == 25  # the ramp let go
    await a.stop_ringing()
    assert audio.volume == 30  # and the volume from before the alarm is back


async def test_ring_unmutes_a_sink_muted_behind_cores_back(svc) -> None:
    a, ctx = svc
    audio: AudioService = ctx.svc(AudioService)
    audio.backend.muted = True  # wpctl set-mute by hand: core's flag still says unmuted
    assert audio.muted is False
    await a.start_ring(RingRequest(kind="alarm", label="M", source="chime:birds", volume=50, ramp_seconds=0))
    await a.ring.settled()
    assert audio.backend.muted is False
    await a.stop_ringing()


# ---- snooze and stop while the sound is still starting -----------------------
async def test_stop_while_the_source_is_starting_leaves_nothing_playing(svc, fast) -> None:
    a, ctx = svc
    audio: AudioService = ctx.svc(AudioService)
    _factory(audio, "slow", delay=0.6, flows=False)
    await a.start_ring(RingRequest(kind="alarm", label="S", source="slow:x", volume=50, ramp_seconds=0, fallback_after_s=1, max_ring_minutes=1))
    await asyncio.sleep(0.1)
    await a.stop_ringing()
    assert ctx.store.state.alarms.ringing is None and a.ring is None
    await asyncio.sleep(2.5)  # past the source's start and its fallback time
    assert audio.arbiter.slot("alarm") is None
    assert ctx.store.state.alarms.ringing is None  # never comes back on the face
    assert not _events(ctx, "ring_fallback")


async def test_snooze_while_the_source_is_starting_stays_quiet(svc, fast) -> None:
    a, ctx = svc
    audio: AudioService = ctx.svc(AudioService)
    _factory(audio, "slow", delay=0.6, flows=False)
    await a.start_ring(RingRequest(kind="alarm", label="S", source="slow:x", volume=50, ramp_seconds=0, fallback_after_s=1, snooze_minutes=9))
    await asyncio.sleep(0.1)
    assert await a.snooze()
    assert not await a.snooze()  # a second tap during the pause is a no-op
    await asyncio.sleep(2.5)
    slot = audio.arbiter.slot("alarm")
    assert slot is None or slot.state == "paused"
    assert a.ring.tier == "source" and not _events(ctx, "ring_fallback")
    assert ctx.store.state.alarms.ringing.snoozed_until is not None and a.ring.snooze_count == 1
    await a.stop_ringing()


async def test_snooze_rings_again_on_the_same_rung(svc, fast) -> None:
    a, ctx = svc
    _broken(ctx.svc(AudioService), "url")
    req = RingRequest(kind="alarm", label="Z", source="url:http://x.invalid/", volume=50, ramp_seconds=0, snooze_minutes=1)
    await a.start_ring(req)
    await _until(lambda: a.ring.tier == "chime")
    await a.ring.settled()
    assert await a.snooze()
    a.ring._snooze_task.cancel()
    a.ring._ring_again("snooze over")  # as if the minute had passed
    await a.ring.settled()
    assert a.ring.tier == "chime" and ctx.svc(AudioService).arbiter.slot("alarm").state == "playing"
    await a.stop_ringing()


# ---- the engine survives the database and bad rows ---------------------------
async def test_alarm_rings_once_with_a_read_only_database(svc, monkeypatch) -> None:
    a, ctx = svc
    row = a.create(AlarmIn(label="RO", time="06:30", repeat="daily", source="chime:birds", ramp_seconds=0), now=BEFORE)

    def readonly(*args, **kw):
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(a, "patch", readonly)  # every write to the alarm table fails
    monkeypatch.setattr(ctx.db, "set", readonly)
    await a.tick(now=AT)
    assert a.ring is not None and ctx.store.state.alarms.ringing.label == "RO"
    first = a.ring
    for s in range(1, 5):  # the next seconds: no second fire although the stamp could not be saved
        await a.tick(now=AT + timedelta(seconds=s))
    assert a.ring is first
    assert a.get(row.id).last_fired_occurrence != AT.replace(second=0).isoformat()  # really not saved
    assert ctx.db.write_errors > 0
    await a.stop_ringing()


async def test_event_log_failure_does_not_stop_an_alarm(svc, monkeypatch) -> None:
    a, ctx = svc
    a.create(AlarmIn(label="EV", time="06:30", repeat="daily", source="chime:birds", ramp_seconds=0), now=BEFORE)

    def full(*args, **kw):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(ctx.db, "session", full)  # nothing can be read or written any more
    await a.tick(now=AT)  # the alarms come from the copy read when the alarm was created
    assert a.ring is not None and a.ring.req.label == "EV"
    await a.stop_ringing()


async def test_a_once_alarm_switches_off_in_memory_when_it_cannot_be_saved(svc, monkeypatch) -> None:
    a, ctx = svc
    a.create(AlarmIn(label="Once", time="06:30", repeat="once", source="chime:birds", ramp_seconds=0), now=BEFORE)
    monkeypatch.setattr(a, "patch", lambda *x, **k: (_ for _ in ()).throw(sqlite3.OperationalError("readonly")))
    await a.tick(now=AT)
    await a.stop_ringing()
    await a.tick(now=AT + timedelta(days=1))  # tomorrow: it is a once-alarm, it does not ring again
    assert a.ring is None


async def test_one_malformed_alarm_does_not_stop_the_others(svc) -> None:
    a, ctx = svc
    with ctx.db.session() as s:
        s.add(AlarmRow(label="Broken", time="25:99", repeat="custom", days="x,1", enabled=True, last_fired_occurrence=BEFORE.isoformat()))
        s.commit()
    good = a.create(AlarmIn(label="Good", time="06:30", repeat="daily", source="chime:birds", ramp_seconds=0), now=BEFORE)  # creating still works
    await a.tick(now=AT)
    assert a.ring is not None and a.ring.req.alarm_id == good.id
    assert [i.label for i in ctx.store.state.alarms.items] == ["Good"]
    await a.stop_ringing()
    assert parse_days("x,1, 3 ,9,") == [1, 3]


async def test_a_failing_publish_does_not_lose_the_alarm(svc, monkeypatch) -> None:
    a, ctx = svc
    a.create(AlarmIn(label="P", time="06:30", repeat="daily", source="chime:birds", ramp_seconds=0), now=BEFORE)
    monkeypatch.setattr(a, "publish", lambda force=False: (_ for _ in ()).throw(RuntimeError("holiday calendar blew up")))
    await a.tick(now=AT)
    assert a.ring is not None and a.ring.req.label == "P"
    await a.stop_ringing()


async def test_a_corrupt_row_still_rings_with_the_defaults(svc) -> None:
    a, ctx = svc
    with ctx.db.session() as s:
        s.add(AlarmRow(label="", time="06:30", repeat="daily", source="", volume=0, snooze_minutes=-3, max_ring_minutes=0,
                       enabled=True, last_fired_occurrence=BEFORE.isoformat()))
        s.commit()
    await a.tick(now=AT)
    req, d = a.ring.req, ctx.config.alarm_defaults
    assert req.label == "Alarm" and req.source == "chime:gentle_bell"
    assert (req.volume, req.snooze_minutes, req.max_ring_minutes) == (d.volume, d.snooze_minutes, d.max_ring_minutes)
    await a.stop_ringing()


async def test_engine_liveness_for_the_watchdog(svc) -> None:
    a, ctx = svc
    await asyncio.sleep(0.01)
    assert not a.alive()  # the fixture cancelled the engine task: as good as stuck
    a._task = asyncio.create_task(a._loop())
    await _until(lambda: a.last_tick is not None)
    assert a.alive()
    a.last_tick = time.monotonic() - 60
    assert not a.alive()
    from dawn_core.system.sysinfo import SysInfoService

    assert ctx.svc(SysInfoService)._alarms_alive() is False
    a._task.cancel()


# ---- who may take over a ring ---------------------------------------------------
async def test_a_nap_does_not_replace_a_ringing_alarm(svc) -> None:
    a, ctx = svc
    a.create(AlarmIn(label="Real", time="06:30", repeat="daily", source="chime:birds", ramp_seconds=0), now=BEFORE)
    await a.tick(now=AT)
    real = a.ring
    await a.ring_nap()
    assert a.ring is real and a.ring.req.kind == "alarm"
    assert await a.snooze()
    await a.ring_nap()  # a nap running out while the alarm is snoozed rings the alarm now
    assert a.ring is real and a.ring.snoozed_until is None
    with pytest.raises(RingBusy):
        await a.test_ring(source="chime:birds")
    await a.stop_ringing()


async def test_test_ring_during_an_alarm_is_refused_by_the_api(tmp_config) -> None:
    from httpx import ASGITransport, AsyncClient

    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        a: AlarmService = app.state.ctx.svc(AlarmService)
        a._task.cancel()
        a.create(AlarmIn(label="Real", time="06:30", repeat="daily", source="chime:birds", ramp_seconds=0), now=BEFORE)
        await a.tick(now=AT)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/alarms/test", json={"source": "chime:birds"})
            assert r.status_code == 409 and "Real" in r.json()["detail"]
        await a.stop_ringing()


# ---- a restart of dawn-core does not drop a ring --------------------------------
async def _restart(tmp_config):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    return app, app.router.lifespan_context(app)


async def test_a_ringing_alarm_carries_on_after_a_restart(tmp_config) -> None:
    app, life = await _restart(tmp_config)
    async with life:
        a: AlarmService = app.state.ctx.svc(AlarmService)
        audio: AudioService = app.state.ctx.svc(AudioService)
        await audio.set_volume(22, overlay=False)
        await a.start_ring(RingRequest(kind="alarm", label="Keep", source="chime:birds", volume=60, ramp_seconds=0, alarm_id=7))
        await a.ring.settled()
    # dawn-core stopped mid-ring (shutdown keeps the ring saved) and starts again
    app2, life2 = await _restart(tmp_config)
    async with life2:
        ctx = app2.state.ctx
        a2: AlarmService = ctx.svc(AlarmService)
        assert a2.ring is not None and a2.ring.req.label == "Keep" and ctx.store.state.alarms.ringing is not None
        await a2.ring.settled()
        assert ctx.svc(AudioService).arbiter.slot("alarm") is not None
        assert a2.ring.prev_volume == 22  # stopping it restores the volume from before the alarm, not the alarm's
        assert _events(ctx, "ring_restored")
        await a2.stop_ringing()
    app3, life3 = await _restart(tmp_config)
    async with life3:
        assert app3.state.ctx.svc(AlarmService).ring is None  # stopped by the user: gone for good


async def test_a_snoozed_alarm_stays_snoozed_across_a_restart(tmp_config) -> None:
    app, life = await _restart(tmp_config)
    async with life:
        a: AlarmService = app.state.ctx.svc(AlarmService)
        await a.start_ring(RingRequest(kind="alarm", label="Snz", source="chime:birds", volume=60, ramp_seconds=0, snooze_minutes=9))
        await a.ring.settled()
        await a.snooze()
        until = a.ring.snoozed_until
    app2, life2 = await _restart(tmp_config)
    async with life2:
        ctx = app2.state.ctx
        a2: AlarmService = ctx.svc(AlarmService)
        assert a2.ring is not None and a2.ring.snoozed_until is not None
        assert abs((a2.ring.snoozed_until - until).total_seconds()) < 1 and a2.ring.snooze_count == 1
        assert ctx.svc(AudioService).arbiter.slot("alarm") is None  # quiet until then
        await a2.stop_ringing()


async def test_a_stale_saved_ring_is_dropped(tmp_config) -> None:
    app, life = await _restart(tmp_config)
    async with life:
        ctx = app.state.ctx
        old = (ctx.store.now() - timedelta(hours=2)).isoformat()
        ctx.db.set("alarms.ring", {"req": {"kind": "alarm", "label": "Old", "source": "chime:birds", "volume": 50, "max_ring_minutes": 30},
                                   "started_at": old, "saved_at": old, "snoozed_until": None, "tier": "source"})
    app2, life2 = await _restart(tmp_config)
    async with life2:
        assert app2.state.ctx.svc(AlarmService).ring is None
        assert app2.state.ctx.db.get("alarms.ring") is None


async def test_a_nap_timer_survives_a_restart(tmp_config) -> None:
    from dawn_core.alarms.timers import TimerService

    app, life = await _restart(tmp_config)
    async with life:
        await app.state.ctx.svc(TimerService).start_nap(20)
    app2, life2 = await _restart(tmp_config)
    async with life2:
        st = app2.state.ctx.store.state.timers
        assert st.nap is not None and 19 * 60 <= st.nap.remaining_s <= 20 * 60
        await app2.state.ctx.svc(TimerService).cancel_nap()
        assert app2.state.ctx.db.get("timers.nap") is None


# ---- preparing a DAB alarm minutes ahead ----------------------------------------
class FakeWelle:
    """welle-cli's HTTP surface, enough for tuning, sync and the station list."""

    def __init__(self) -> None:
        self.channel = "9A"
        self.up = True
        self.synced = {"9A": True, "9C": True}
        self.stations = {"9A": [("1001", "Station A")], "9C": [("2002", "Station B")]}
        self.tunes: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        if path == "/mux.json":
            now_ms = int(time.time() * 1000)
            fct0 = now_ms if self.synced.get(self.channel) else now_ms - 120_000
            services = [{"sid": f"0x{sid}", "label": {"label": label}} for sid, label in self.stations.get(self.channel, [])]
            return httpx.Response(200, json={"ensemble": {"label": {"label": f"Ensemble {self.channel}"}, "id": "0x1000"},
                                             "demodulator": {"snr": 15.0, "time_last_fct0_frame": fct0}, "services": services})
        if path == "/channel":
            if request.method == "POST":
                self.channel = request.content.decode().strip().upper()
                self.tunes.append(self.channel)
            return httpx.Response(200, text=self.channel)
        return httpx.Response(404)


@pytest.fixture()
async def radio(svc):
    a, ctx = svc
    dab: DabService = ctx.svc(DabService)
    dab._task.cancel()  # its poll would race the fake below (in the simulator it sets sdr_present from welle)
    await asyncio.sleep(0)
    fw = FakeWelle()
    await dab.client.close()
    dab.client._client = httpx.AsyncClient(transport=httpx.MockTransport(fw.handler))
    dab.channel = "9A"
    ctx.store.state.dab.sdr_present = True
    with ctx.db.session() as s:
        s.add(DabServiceRow(sid="1001", channel="9A", label="Station A"))
        s.add(DabServiceRow(sid="2002", channel="9C", label="Station B"))
        s.commit()
    yield a, ctx, dab, fw


async def _prepare(a: AlarmService, now: datetime) -> None:
    a._prep_due = 0.0
    await a.tick(now=now)
    if a._prep_task:
        await a._prep_task


async def test_a_dab_alarm_is_tuned_and_checked_minutes_ahead(radio) -> None:
    a, ctx, dab, fw = radio
    a.create(AlarmIn(label="Radio", time="06:30", repeat="daily", source="dab:2002", ramp_seconds=0), now=BEFORE)
    await _prepare(a, AT - timedelta(minutes=10))
    assert ctx.store.state.alarms.prepare is None and fw.tunes == []  # too early
    await _prepare(a, AT - timedelta(minutes=4))
    assert fw.tunes == ["9C"]  # tuned to the station's channel minutes before
    assert "dawn-alarm-dab" in ctx.svc(AudioService).players and "dawn-alarm-chime" in ctx.svc(AudioService).players
    await _prepare(a, AT - timedelta(minutes=3))
    p = ctx.store.state.alarms.prepare
    assert p is not None and p.ready and p.start_tier == "source" and p.problems == []
    await a.tick(now=AT)
    await a.ring.settled()
    assert a.ring.tier == "source" and ctx.svc(AudioService).arbiter.slot("alarm").source.kind == "dab"
    assert ctx.store.state.alarms.prepare is None
    await a.stop_ringing()


async def test_a_dab_alarm_that_will_not_play_starts_on_the_chime(radio) -> None:
    a, ctx, dab, fw = radio
    a.create(AlarmIn(label="Radio", time="06:30", repeat="daily", source="dab:2002", ramp_seconds=0), now=BEFORE)
    fw.synced["9C"] = False  # nothing receivable on its channel this morning
    for m in (4, 3, 2):
        await _prepare(a, AT - timedelta(minutes=m))
    p = ctx.store.state.alarms.prepare
    assert not p.ready and p.start_tier == "chime" and any("no DAB signal" in x for x in p.problems)
    await a.tick(now=AT)
    assert a.ring.tier == "chime" and "not ready" in ctx.store.state.alarms.ringing.fallback_reason  # no wait in silence
    await a.stop_ringing()


async def test_listening_to_another_channel_is_not_cut_off_early(radio) -> None:
    a, ctx, dab, fw = radio
    audio: AudioService = ctx.svc(AudioService)
    await audio.play("dab:1001")  # someone is listening on 9A
    fw.tunes.clear()
    a.create(AlarmIn(label="Radio", time="06:30", repeat="daily", source="dab:2002", ramp_seconds=0), now=BEFORE)
    await _prepare(a, AT - timedelta(minutes=4))
    p = ctx.store.state.alarms.prepare
    assert fw.tunes == [] and p.start_tier == "source" and "retunes" in p.problems[0]
    await audio.stop_level("user")


async def test_scans_wait_for_a_dab_alarm_and_retunes_wait_while_it_rings(radio) -> None:
    a, ctx, dab, fw = radio
    now = ctx.store.now()
    soon = now + timedelta(minutes=3)
    a.create(AlarmIn(label="Radio", time=soon.strftime("%H:%M"), repeat="daily", source="dab:2002", ramp_seconds=0), now=now)
    await a.tick()
    with pytest.raises(TunerBusy, match="Radio rings at"):
        await dab.start_scan()
    await a.start_ring(RingRequest(kind="alarm", label="Radio", source="dab:2002", volume=40, ramp_seconds=0))
    await a.ring.settled()
    with pytest.raises(TunerBusy):
        await dab.user_tune("9A")
    with pytest.raises(TunerBusy):
        await dab.restart_welle(force=True, reason="manual")
    assert await dab.restart_welle(force=True, reason="alarm")  # the alarm's own restart is allowed
    await a.stop_ringing()
    await dab.user_tune("9A")  # free again
    assert dab.channel == "9A"


async def test_a_scan_running_into_a_dab_alarm_is_stopped(radio) -> None:
    a, ctx, dab, fw = radio
    dab._scan_task = asyncio.create_task(asyncio.sleep(30))  # a scan in progress
    status, msg = await dab.prepare_for("2002", may_retune=True)
    assert status == "tuning" and "scan" in msg and dab._scan_stop.is_set()
    dab._scan_task.cancel()


async def test_preempted_radio_retunes_when_the_alarm_ends(radio) -> None:
    a, ctx, dab, fw = radio
    audio: AudioService = ctx.svc(AudioService)
    audio.arbiter.duck_seconds = 0.05
    await audio.play("dab:1001")  # 9A
    await a.start_ring(RingRequest(kind="alarm", label="Radio", source="dab:2002", volume=40, ramp_seconds=0))
    await a.ring.settled()
    assert fw.channel == "9C"
    await a.stop_ringing()
    await _until(lambda: fw.channel == "9A" and audio.players["dawn-dab"].loaded_url and audio.players["dawn-dab"].playing)
    await audio.stop_level("user")


# ---- inputs while ringing --------------------------------------------------------
class _Acts:
    def __init__(self, ringing: bool = True) -> None:
        self.ringing = ringing
        self.log: list[str] = []

    async def is_ringing(self) -> bool:
        return self.ringing

    async def stop_ringing(self) -> None:
        self.log.append("stop")

    async def snooze(self) -> None:
        self.log.append("snooze")

    async def open_nap_picker(self) -> None:
        self.log.append("nap")

    async def shutdown_countdown(self, seconds_left: int | None) -> None:
        self.log.append(f"countdown={seconds_left}")

    async def shutdown(self) -> None:
        self.log.append("shutdown")


async def test_holding_the_button_while_ringing_stops_and_never_powers_off() -> None:
    from dawn_core.inputs.controller import InputController

    for held in (0.05, 0.7, 1.3):  # a tap, a sleepy press, a long hold
        acts = _Acts()
        c = InputController(acts, long_press_s=1.0)  # type: ignore[arg-type]
        await c.handle("button_down")
        await asyncio.sleep(held)
        await c.handle("button_up")
        assert "stop" in acts.log and "shutdown" not in acts.log and not any(x.startswith("countdown=") and x != "countdown=None" for x in acts.log)
    acts = _Acts()
    await InputController(acts).handle("button_long")  # type: ignore[arg-type]
    assert acts.log == ["stop"]


async def test_alarm_starting_during_a_shutdown_hold_cancels_it() -> None:
    from dawn_core.inputs.controller import InputController

    acts = _Acts(ringing=False)
    c = InputController(acts, long_press_s=0.8)  # type: ignore[arg-type]
    await c.handle("button_down")
    await asyncio.sleep(0.3)
    acts.ringing = True
    await asyncio.sleep(0.8)
    assert "shutdown" not in acts.log


async def test_encoder_hold_while_ringing_snoozes() -> None:
    from dawn_core.inputs.controller import InputController

    acts = _Acts()
    await InputController(acts).handle("encoder_long")  # type: ignore[arg-type]
    assert acts.log == ["snooze"]


# ---- the screen while ringing ----------------------------------------------------
async def test_ringing_lights_the_screen_like_a_tap(svc) -> None:
    from dawn_core.display.service import DisplayService

    a, ctx = svc
    d: DisplayService = ctx.svc(DisplayService)
    st = ctx.store.state
    await a.start_ring(RingRequest(kind="alarm", label="L", source="chime:birds", volume=40, ramp_seconds=0))
    await _until(lambda: st.face.mode == "ringing")
    d.lux = 0.1  # a dark bedroom: the curve alone gives the backlight minimum
    await d.tick()
    assert d.controller.target == ctx.config.display.sleep.wake_percent
    await a.snooze()
    await d.tick()
    assert d.controller.target < ctx.config.display.sleep.wake_percent  # snoozed: back to the room's level
    await a.stop_ringing()
