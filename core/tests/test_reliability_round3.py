"""Regression tests for the third reliability round (0.2.5): volume and mute sent to a PipeWire sink id that died
with a PipeWire restart, a stream that runs without reaching the speaker, a DAB station that is on air but silent,
a database made by an older version, the setup hotspot that never went back to Wi-Fi, and the update that was killed
half-way by the restart it asked for."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dawn_core.alarms.schemas import AlarmIn
from dawn_core.alarms.service import AlarmService
from dawn_core.app import create_app
from dawn_core.audio import backend as backend_module
from dawn_core.audio.backend import PipeWireBackend, Sink
from dawn_core.audio.service import AudioService
from dawn_core.config import ConfigManager
from dawn_core.dab.service import DabService, DabSource
from dawn_core.dab.welle import ServiceInfo

SYD = ZoneInfo("Australia/Sydney")
BEFORE = datetime(2026, 10, 1, 12, 0, tzinfo=SYD)
AT = datetime(2026, 10, 2, 6, 30, 3, tzinfo=SYD)
HW = "alsa_output.platform-soc_sound.stereo-fallback"


@pytest.fixture()
async def svc(tmp_config):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        ctx = app.state.ctx
        a: AlarmService = ctx.svc(AlarmService)
        a._task.cancel()  # drive ticks by hand
        yield a, ctx


# ---- 1. a sink id that died with a PipeWire restart ---------------------------------------------------------------
class FakePipeWire(PipeWireBackend):
    """PipeWireBackend with its tools replaced: one sound card whose id is whatever the "server" says now; wpctl on
    any other id fails, as the real one does."""

    def __init__(self, monkeypatch) -> None:
        super().__init__()
        self.sink_id = "55"
        self.calls: list[tuple[str, ...]] = []
        self.broken = False  # every wpctl fails

        async def run(*cmd: str, timeout: float = 5.0) -> tuple[int, str]:
            self.calls.append(cmd)
            if cmd[0] == "wpctl" and cmd[1] in ("set-volume", "set-mute"):
                target = cmd[-2]
                return (0, "") if target == self.sink_id and not self.broken else (1, f"Object '{target}' not found")
            return 0, ""

        monkeypatch.setattr(backend_module, "run", run)

    async def list_sinks(self) -> list[Sink]:
        return [Sink(id=self.sink_id, name=HW, description="Audio Amp SHIM", kind="hifiberry")]

    def targets(self) -> list[str]:
        return [c[-2] for c in self.calls if c[0] == "wpctl" and c[1] in ("set-volume", "set-mute")]


async def _on_pipewire(ctx, monkeypatch) -> tuple[AudioService, FakePipeWire]:
    audio: AudioService = ctx.svc(AudioService)
    pw = FakePipeWire(monkeypatch)
    audio.backend, audio._sink = pw, None
    await audio.refresh_sinks(select_now=True)
    assert pw._hw_sink is not None and pw._hw_sink.id == "55" and audio.output_error is None
    return audio, pw


async def test_a_sink_that_comes_back_with_a_new_id_is_selected_again(svc, monkeypatch) -> None:
    _, ctx = svc
    audio, pw = await _on_pipewire(ctx, monkeypatch)
    pw.sink_id = "61"  # PipeWire crashed and systemd started it again: same node name, a new id
    pw.calls.clear()
    await audio.refresh_sinks()  # what the audio loop does every 10 s
    assert audio._sink.id == "61" and pw._hw_sink.id == "61"
    assert pw.targets() and set(pw.targets()) == {"61"}  # volume and mute put on the new node
    pw.calls.clear()
    await audio.set_volume(70, persist=False, overlay=False)
    assert pw.targets() == ["61"] and audio.output_error is None


async def test_a_refused_volume_chooses_the_sink_again_and_is_made_once_more(svc, monkeypatch) -> None:
    """The ring starts in the seconds between PipeWire's restart and the audio loop's next look at the sinks."""
    _, ctx = svc
    audio, pw = await _on_pipewire(ctx, monkeypatch)
    pw.sink_id = "61"
    pw.calls.clear()
    await audio.ensure_unmuted()
    await audio.set_volume(70, persist=False, overlay=False)
    assert pw.targets()[0] == "55" and pw.targets()[-1] == "61"  # refused on the dead id, made again on the live one
    assert pw._hw_sink.id == "61" and audio.volume == 70 and audio.output_error is None


async def test_an_output_that_cannot_be_set_is_reported_and_fails_nothing(svc, monkeypatch) -> None:
    from copy import deepcopy

    from dawn_core.diagnostics import system
    from tests.test_reliability_round2 import FACTS

    _, ctx = svc
    audio, pw = await _on_pipewire(ctx, monkeypatch)
    pw.broken = True
    assert await audio.set_volume(40, persist=False, overlay=False) == 40  # no exception: a fade or a ramp carries on
    assert audio.output_error and "not found" in audio.output_error
    facts = deepcopy(FACTS)
    facts["audio"].update(output_error=audio.output_error, link_problem="the tone control is not connected to Audio Amp SHIM")
    found = {c.id: c for c in system.checks(facts)}
    assert found["audio.output"].status == "fail" and found["audio.link"].status == "fail"
    pw.broken = False
    await audio.set_volume(41, persist=False, overlay=False)
    assert audio.output_error is None


async def test_the_alarm_ramp_makes_a_refused_volume_again(svc, monkeypatch) -> None:
    a, ctx = svc
    audio, pw = await _on_pipewire(ctx, monkeypatch)
    monkeypatch.setattr("dawn_core.alarms.ringing.OUTPUT_RETRY_S", 0.05)
    pw.broken = True
    a.create(AlarmIn(label="Wake", time="06:30", repeat="daily", source="chime:birds", volume=70, ramp_seconds=0), now=BEFORE)
    await a.tick(now=AT)
    assert a.ring is not None
    await a.ring.settled()
    await _until(lambda: audio.output_error is not None)  # nothing reached the output, and the ring knows
    pw.broken = False
    pw.calls.clear()
    await _until(lambda: audio.output_error is None)  # the ramp tried again by itself, and it took
    assert ("wpctl", "set-volume", "-l", "1.0", "55", "0.700") in pw.calls
    assert ("wpctl", "set-mute", "55", "0") in pw.calls  # with the unmute that had gone the same way
    await a.stop_ringing()


async def _until(pred, timeout: float = 5.0) -> None:
    import asyncio

    end = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < end, "timed out"
        await asyncio.sleep(0.02)


# ---- 2. "running" is not "heard" ---------------------------------------------------------------------------------
def _node(i: int, name: str, cls: str, state: str = "running", **props) -> dict:
    return {"id": i, "type": "PipeWire:Interface:Node", "info": {"state": state, "props": {"node.name": name, "media.class": cls, **props}}}


def _link(i: int, out: int, inp: int, state: str = "active") -> dict:
    return {"id": i, "type": "PipeWire:Interface:Link", "info": {"output-node-id": out, "input-node-id": inp, "state": state, "props": {"link.output.node": out, "link.input.node": inp}}}


GRAPH = [
    _node(40, HW, "Audio/Sink"),
    _node(41, "dawn_eq", "Audio/Sink"),
    _node(42, "output.dawn_eq", "Stream/Output/Audio"),
    _node(50, "dawn-alarm-chime", "Stream/Output/Audio", **{"application.name": "dawn-alarm-chime"}),
]


def _backend(dump: list[dict], monkeypatch) -> PipeWireBackend:
    b = PipeWireBackend()
    b._eq_present = True
    b._hw_sink = Sink(id="40", name=HW, description="Audio Amp SHIM", kind="hifiberry")

    async def fake_dump() -> list[dict]:
        return dump

    monkeypatch.setattr(b, "_dump", fake_dump)
    return b


async def test_a_stream_through_the_tone_control_to_the_sound_card_is_heard(monkeypatch) -> None:
    b = _backend(GRAPH + [_link(60, 50, 41), _link(61, 42, 40)], monkeypatch)
    assert await b.stream_running("dawn-alarm-chime") is True and b.link_problem is None
    direct = _backend(GRAPH + [_link(60, 50, 40)], monkeypatch)  # no tone control in the way
    assert await direct.stream_running("dawn-alarm-chime") is True


async def test_a_running_stream_whose_tone_control_is_not_linked_to_the_card_is_not_heard(monkeypatch) -> None:
    b = _backend(GRAPH + [_link(60, 50, 41)], monkeypatch)  # PipeWire came back and the link's target was lost
    assert await b.stream_running("dawn-alarm-chime") is False
    assert b.link_problem and "tone control" in b.link_problem
    gone = _backend([n for n in GRAPH if n["id"] != 40] + [_link(60, 50, 41)], monkeypatch)  # no sound card at all
    assert await gone.stream_running("dawn-alarm-chime") is False and "not in PipeWire" in gone.link_problem
    null = _backend(GRAPH + [_node(43, "auto_null", "Audio/Sink"), _link(60, 50, 43)], monkeypatch)  # playing into nothing
    assert await null.stream_running("dawn-alarm-chime") is False


async def test_a_graph_whose_links_cannot_be_read_never_counts_against_a_stream(monkeypatch) -> None:
    """The check may only add a "no" it is sure of: a pw-dump without link objects it understands changes nothing."""
    b = _backend(GRAPH, monkeypatch)
    assert await b.stream_running("dawn-alarm-chime") is True and b.link_problem is None
    b2 = _backend(GRAPH + [_link(60, 50, 41), _link(61, 42, 40)], monkeypatch)
    b2._hw_sink = None  # nothing chosen yet
    assert await b2.stream_running("dawn-alarm-chime") is True
    assert await _backend([_node(50, "dawn-alarm-chime", "Stream/Output/Audio", state="idle")], monkeypatch).stream_running("dawn-alarm-chime") is False


async def test_a_null_sink_is_not_an_output(monkeypatch) -> None:
    b = _backend(GRAPH + [_node(43, "auto_null", "Audio/Sink"), _node(44, "x", "Audio/Sink", **{"factory.name": "support.null-audio-sink"})], monkeypatch)
    assert [s.name for s in await b.list_sinks()] == [HW]


async def test_an_unlinked_output_is_set_up_again(svc, monkeypatch) -> None:
    _, ctx = svc
    audio, pw = await _on_pipewire(ctx, monkeypatch)
    problem = "the tone control is not connected to Audio Amp SHIM"
    pw.link_problem = problem
    pw.calls.clear()
    await audio._relink_if_unheard()
    assert pw.calls == [] and pw.link_problem is None  # nothing is playing: nothing to go by, and nothing to mend
    await audio.play("chime:birds")
    pw.link_problem = problem
    await audio._relink_if_unheard()
    assert any(c[:2] == ("wpctl", "set-default") for c in pw.calls)  # the output selected afresh
    pw.calls.clear()
    await audio._relink_if_unheard()
    assert pw.calls == [] and pw.link_problem == problem  # not again at once
    await audio.stop_level("user")


class _Player:
    client_name = "dawn-alarm-dab"
    flowing = playing = True
    loaded_url = "x"

    def on_change(self, fn) -> None: ...
    def off_change(self, fn) -> None: ...


def _playing(ctx, **svc_fields) -> tuple[DabService, DabSource, ServiceInfo]:
    dab: DabService = ctx.svc(DabService)
    info = ServiceInfo(sid="1002", label="ABC Classic", **svc_fields)
    dab.live["1002"], dab.live_sync, dab.last_mux_at = info, True, time.time()
    src = DabSource(_Player(), "http://x/mp3/0x1002", info, dab)
    src._started = True
    return dab, src, info


async def test_a_station_that_is_on_air_but_silent_stops_counting_as_sound(svc) -> None:
    _, ctx = svc
    now = time.time()
    dab, src, info = _playing(ctx, decoding=True, audio_level=11000, audio_time=int(now))
    dab._track_audio(info)
    assert src.flowing
    info.audio_level = 3  # dead air: the carrier is there, the programme is not
    dab._track_audio(info)
    assert src.flowing  # a pause is not a failure
    dab._quiet_since["1002"] = now - ctx.config.dab.silence_after_s - 1
    assert not src.flowing and "silent" in dab.audio_problem("1002")
    info.audio_level = 9000  # the programme is back
    dab._track_audio(info)
    assert src.flowing and dab.audio_problem("1002") is None


async def test_a_station_welle_has_stopped_decoding_is_not_sound(svc) -> None:
    _, ctx = svc
    dab, src, info = _playing(ctx, decoding=False, audio_level=11000, audio_time=int(time.time()) - 60)
    assert not src.flowing and "no audio decoded" in dab.audio_problem("1002")
    info.audio_time = int(time.time())
    assert src.flowing


async def test_a_decoder_that_reports_no_levels_never_fails_a_station(svc) -> None:
    """An older welle-cli (no audiolevel in mux.json), and silence detection switched off."""
    _, ctx = svc
    dab, src, info = _playing(ctx)  # no audio_level, no audio_time
    dab._track_audio(info)
    assert src.flowing and dab.audio_problem("1002") is None
    info.decoding, info.audio_level, info.audio_time = True, 0, int(time.time())
    dab._track_audio(info)
    dab._quiet_since["1002"] = time.time() - 3600
    assert not src.flowing
    info.audio_time = int(time.time()) - 60  # and not decoded for a minute
    assert "no audio decoded" in dab.audio_problem("1002")
    await ctx.cfg_mgr.update({"dab": {"silence_after_s": 0}})
    assert src.flowing and dab.audio_problem("1002") is None  # one switch for both checks


# ---- 4. a database made by an older version ----------------------------------------------------------------------
def _older_database(path: Path) -> None:
    """dawn.db as a version before `light_wake` and `light_wake_minutes` existed would have left it: one alarm in it."""
    from dawn_core.db.engine import Database
    from dawn_core.db.models import AlarmRow

    db = Database(path)
    with db.session() as s:
        s.add(AlarmRow(label="Wake", time="06:30", repeat="daily", source="chime:birds", ramp_seconds=0, last_fired_occurrence=BEFORE.isoformat()))
        s.commit()
    db.engine.dispose()
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE alarm DROP COLUMN light_wake")
    con.execute("ALTER TABLE alarm DROP COLUMN light_wake_minutes")
    con.execute("DROP INDEX ix_event_kind")
    con.execute("ALTER TABLE event DROP COLUMN kind")
    con.execute("PRAGMA user_version = 0")
    con.commit()
    con.close()


def test_columns_added_since_the_database_was_made_are_added_on_open(tmp_path) -> None:
    from dawn_core.db.engine import SCHEMA_VERSION, Database
    from dawn_core.db.models import AlarmRow

    path = tmp_path / "dawn.db"
    _older_database(path)
    db = Database(path)
    assert db.mode == "rw" and db.migration_error is None
    assert {"alarm.light_wake", "alarm.light_wake_minutes", "event.kind"} <= set(db.migrated)
    with db.session() as s:
        row = s.get(AlarmRow, 1)
        assert row is not None and row.label == "Wake"
        assert row.light_wake is False and row.light_wake_minutes == 10  # the model's defaults, on the existing row
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    # a row written by the older code (an update rolled back after the migration) gets the defaults from the table
    con.execute("INSERT INTO alarm (label, enabled, time, repeat, days, skip_public_holidays, source, volume, ramp_seconds, snooze_minutes, fallback_after_s,"
                " max_ring_minutes, skip_next, created_at, updated_at) VALUES ('Old', 1, '07:00', 'daily', '', 0, 'chime:birds', 70, 60, 9, 15, 30, 0, '2026-10-01', '2026-10-01')")
    con.commit()
    assert con.execute("SELECT light_wake, light_wake_minutes FROM alarm WHERE label = 'Old'").fetchone() == (0, 10)
    assert "ix_event_kind" in {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    con.close()
    db.log_event("after", ok=True)  # the event table is writable again
    assert db.recent_events(1)[0]["kind"] == "after"
    db.engine.dispose()
    again = Database(path)
    assert again.migrated == [] and again.migration_error is None  # nothing left to do the second time


def test_numbered_migrations_run_once_and_in_order(tmp_path, monkeypatch) -> None:
    from dawn_core.db import engine as engine_module
    from dawn_core.db.engine import Database

    path = tmp_path / "dawn.db"
    Database(path).engine.dispose()
    monkeypatch.setattr(engine_module, "SCHEMA_VERSION", 3)
    monkeypatch.setattr(engine_module, "MIGRATIONS", {2: ["INSERT INTO kv (key, value) VALUES ('m2', '1')"], 3: ["UPDATE kv SET value = '2' WHERE key = 'm2'"]})
    db = Database(path)
    assert db.migrated == ["schema 2", "schema 3"] and db.get("m2") == 2
    db.engine.dispose()
    assert Database(path).migrated == []  # the stored version is 3 now
    monkeypatch.setattr(engine_module, "SCHEMA_VERSION", 2)  # the previous version, after a rolled-back update
    older = Database(path)
    assert older.migration_error is None and older.get("m2") == 2


def test_a_migration_that_fails_does_not_stop_the_database_opening(tmp_path, monkeypatch) -> None:
    from dawn_core.db import engine as engine_module
    from dawn_core.db.engine import Database

    path = tmp_path / "dawn.db"
    Database(path).engine.dispose()
    monkeypatch.setattr(engine_module, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(engine_module, "MIGRATIONS", {2: ["INSERT INTO kv (key, value) VALUES ('half', '1')", "THIS IS NOT SQL"]})
    db = Database(path)
    assert db.mode == "rw" and db.migration_error and "syntax" in db.migration_error
    assert db.get("half") is None  # the step is all or nothing
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 1  # and is tried again at the next start
    con.close()


async def test_alarms_in_an_older_database_ring_after_an_update(tmp_config, tmp_path) -> None:
    """The reproduction: before 0.2.5 this database made every read of the alarm table fail, so nothing rang."""
    (tmp_path / "data").mkdir(exist_ok=True)
    _older_database(tmp_path / "data" / "dawn.db")
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        ctx = app.state.ctx
        a: AlarmService = ctx.svc(AlarmService)
        a._task.cancel()
        a.patch(1, last_fired_occurrence=BEFORE.isoformat())  # the engine's first tick ran on today's clock
        a.reset_memory()
        await a.tick(now=AT)
        assert a._rows_error is None and not ctx.store.state.alarms.degraded
        assert a.ring is not None and a.ring.req.label == "Wake"
        await a.stop_ringing()


# ---- 3. the update -----------------------------------------------------------------------------------------------
async def test_no_update_into_an_alarm(svc) -> None:
    from dawn_core.system import update

    a, ctx = svc
    assert update.blocked(ctx) is None
    a.create(AlarmIn(label="Wake", time="06:30", repeat="daily", source="chime:birds", ramp_seconds=0), now=BEFORE)
    nxt = datetime.fromisoformat(ctx.store.state.alarms.next.at)  # published against today's clock
    assert update.blocked(ctx, now=nxt - timedelta(hours=2)) is None
    why = update.blocked(ctx, now=nxt - timedelta(minutes=20))
    assert why and "Wake rings at 06:30" in why
    await a.tick(now=AT)
    assert a.ring is not None and "ringing" in update.blocked(ctx)
    with pytest.raises(update.UpdateBlocked):
        await update.run_update(ctx)
    assert not ctx.store.state.system.update_running
    await a.ring.snooze()
    assert "snoozed" in update.blocked(ctx)
    await a.stop_ringing()


async def test_the_update_api_says_why_it_will_not_start(tmp_config) -> None:
    from httpx import ASGITransport, AsyncClient

    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        ctx = app.state.ctx
        await ctx.svc(AlarmService).test_ring(label="Morning")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/system/update")
            assert r.status_code == 409 and "Morning" in r.json()["detail"]
            await ctx.svc(AlarmService).stop_ringing()
            r = await client.post("/api/system/update")
            assert r.status_code == 200


def test_the_update_log_is_read_from_the_latest_run_on(tmp_path) -> None:
    from dawn_core.system import update

    path = tmp_path / "dawn-update.log"
    assert update.log_section(path=path) == ""
    path.write_text("=== dawn-update 2026-10-01T10:00:00 branch=main ===\nupdated a -> b\n=== done ===\n"
                    "=== dawn-update 2026-11-12T10:00:00 branch=main ===\nupdated b -> c (version 0.2.5)\n")
    text = update.log_section(path=path)
    assert text.startswith("=== dawn-update 2026-11-12") and "b -> c" in text and "a -> b" not in text
    assert update.log_section(since=time.time() + 60, path=path) == ""  # not written since this run began: another run's


async def test_a_core_restarted_by_the_update_carries_on_showing_it(svc, tmp_path, monkeypatch) -> None:
    """The update restarts dawn-core half-way: the new dawn-core finds the update's unit still running."""
    from dawn_core.system import update

    _, ctx = svc
    path = tmp_path / "dawn-update.log"
    path.write_text("=== dawn-update 2026-11-12T10:00:00 branch=main ===\nupdated b -> c (version 0.2.5)\n")
    states = ["active", "active", "active", "inactive", "inactive", "inactive"]

    async def unit_state() -> str:
        return states.pop(0) if len(states) > 1 else states[0]

    real_sleep = __import__("asyncio").sleep
    monkeypatch.setattr(update, "LOG_PATH", path)
    monkeypatch.setattr(update, "_unit_state", unit_state)
    monkeypatch.setattr(update.asyncio, "sleep", lambda s: real_sleep(0.01))
    monkeypatch.setattr(ctx, "sim", False)
    await update.resume(ctx)
    st = ctx.store.state.system
    assert st.update_running and "b -> c" in st.update_log
    with path.open("a") as f:
        f.write("=== done 2026-11-12T10:04:00: c (version 0.2.5) is running ===\n")
    await _until(lambda: not st.update_running)
    assert "=== done" in st.update_log


def test_the_web_build_has_an_id_that_follows_its_pages(tmp_path) -> None:
    from dawn_core.app import web_build_id

    assert web_build_id(tmp_path) is None
    (tmp_path / "face").mkdir()
    (tmp_path / "index.html").write_text('<script src="/assets/control-abc.js">')
    (tmp_path / "face/index.html").write_text('<script src="/assets/face-abc.js">')
    one = web_build_id(tmp_path)
    assert one and one == web_build_id(tmp_path)
    (tmp_path / "face/index.html").write_text('<script src="/assets/face-def.js">')  # a new build of the face
    assert web_build_id(tmp_path) != one


# ---- 5. the setup hotspot goes back to Wi-Fi ---------------------------------------------------------------------
class FakeNm:
    """nmcli as NetworkService uses it: saved profiles, what is in range, and whether joining works."""

    def __init__(self, monkeypatch, *, saved=("HomeWiFi",), in_range=("HomeWiFi", "Neighbour"), joins=True) -> None:
        self.saved, self.in_range, self.joins = list(saved), list(in_range), joins
        self.calls: list[tuple[str, ...]] = []
        from dawn_core.net import service as net_module

        monkeypatch.setattr(net_module, "run", self.run)

    async def run(self, *args: str, timeout: float = 15.0) -> tuple[int, str]:
        self.calls.append(args)
        a = args[1:]
        if a[:5] == ("-t", "-f", "NAME,TYPE", "con", "show"):
            return 0, "".join(f"{n}:802-11-wireless\n" for n in [*self.saved, "dawn-hotspot"]) + "Wired:802-3-ethernet\n"
        if a[0] == "-g":
            return 0, f"{a[-1]}\n{'ap' if a[-1] == 'dawn-hotspot' else 'infrastructure'}\n"
        if a[:2] == ("-t", "-f") and "list" in a:
            return 0, "".join(f"no:{s}:70:WPA2\n" for s in self.in_range)
        if a[:2] == ("con", "up"):
            return (0, "Connection successfully activated") if self.joins else (4, "Error: Connection activation failed")
        return 0, ""

    def did(self, *prefix: str) -> bool:
        return any(c[1:1 + len(prefix)] == prefix for c in self.calls)


async def _on_hotspot(ctx, monkeypatch, **nm):
    from dawn_core.net.service import NetworkService

    net: NetworkService = ctx.svc(NetworkService)
    if net._task:
        net._task.cancel()
    monkeypatch.setattr(ctx, "sim", False)
    fake = FakeNm(monkeypatch, **nm)
    await net._hotspot(True)
    assert net.hotspot_active and ctx.store.state.face.setup is not None
    fake.calls.clear()
    return net, fake


async def test_the_hotspot_goes_back_to_a_saved_wifi_that_is_in_range_again(svc, monkeypatch) -> None:
    """A power cut: the Pi is up before the router, starts its hotspot, and used to stay on it for good."""
    _, ctx = svc
    net, nm = await _on_hotspot(ctx, monkeypatch)
    now = time.monotonic()
    assert not await net._retry_wifi(now + 10) and nm.calls == []  # not yet: every retry_wifi_s
    assert await net._retry_wifi(now + ctx.config.network.hotspot.retry_wifi_s + 1)
    assert nm.did("con", "down", "dawn-hotspot") and nm.did("con", "up", "HomeWiFi")
    assert not net.hotspot_active and ctx.store.state.face.setup is None  # the face leaves the setup screen


async def test_the_hotspot_comes_straight_back_when_the_wifi_is_still_away(svc, monkeypatch) -> None:
    _, ctx = svc
    net, nm = await _on_hotspot(ctx, monkeypatch, in_range=("Neighbour",))
    due = time.monotonic() + ctx.config.network.hotspot.retry_wifi_s + 1
    assert not await net._retry_wifi(due)
    assert nm.did("con", "down", "dawn-hotspot") and not nm.did("con", "up") and nm.did("dev", "wifi", "hotspot")
    assert net.hotspot_active and ctx.store.state.face.setup is not None
    nm.calls.clear()
    nm.in_range, nm.joins = ["HomeWiFi"], False  # in range, but it will not let us in (a changed password)
    assert not await net._retry_wifi(due + ctx.config.network.hotspot.retry_wifi_s + 1)
    assert nm.did("con", "up", "HomeWiFi") and nm.did("dev", "wifi", "hotspot") and net.hotspot_active


async def test_the_hotspot_is_left_alone_while_it_is_being_used_or_is_all_there_is(svc, monkeypatch) -> None:
    from dawn_core.net import service as net_module

    _, ctx = svc
    net, nm = await _on_hotspot(ctx, monkeypatch, saved=())
    step = ctx.config.network.hotspot.retry_wifi_s + 1
    due = time.monotonic() + step
    assert not await net._retry_wifi(due) and not nm.did("con", "down")  # first set-up: no network was ever saved
    nm.saved = ["HomeWiFi"]
    ctx.ws_hub.info["phone"] = {"remote": "10.42.0.57", "role": None}  # someone is on the hotspot's page
    assert not await net._retry_wifi(due + step) and not nm.did("con", "down")
    for i in range(net_module.MAX_DEFERRALS):  # ...but a page left open does not hold it off for ever
        await net._retry_wifi(due + (i + 2) * step)
    assert nm.did("con", "down") and not net.hotspot_active
    ctx.ws_hub.info.pop("phone")
    net2, nm2 = await _on_hotspot(ctx, monkeypatch)
    await ctx.cfg_mgr.update({"network": {"hotspot": {"retry_wifi_s": 0}}})  # switched off
    assert not await net2._retry_wifi(time.monotonic() + 99999) and nm2.calls == []
