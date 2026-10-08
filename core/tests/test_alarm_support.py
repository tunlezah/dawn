"""The pieces around the alarm engine: storage that cannot fail an alarm, a source that reloads when it resumes,
backups that cannot smuggle in a broken alarm, the Diagnostics that report all this, the simulator's streams."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dawn_core.app import create_app
from dawn_core.audio.player import SimPlayer
from dawn_core.audio.service import AudioService
from dawn_core.audio.sources import UrlSource
from dawn_core.config import ConfigManager
from dawn_core.db import Database
from dawn_core.diagnostics import system


@pytest.fixture()
async def ctx(tmp_config):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        yield app.state.ctx


def test_event_log_and_try_set_never_raise(monkeypatch) -> None:
    db = Database(":memory:")

    def broken():
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(db, "session", broken)
    db.log_event("alarm_fire", id=1)  # logged to the journal only
    assert db.try_set("audio.volume", 40) is False
    assert db.write_errors == 2 and "readonly" in (db.last_write_error or "")


async def test_a_paused_stream_reloads_when_its_player_lost_it() -> None:
    p = SimPlayer("dawn-media")
    src = UrlSource(p, "http://radio.invalid/a")
    await src.start()
    await src.pause()
    await p.load("http://other.invalid/b")  # the player was used for something else meanwhile
    await src.resume()
    assert p.loaded_url == "http://radio.invalid/a" and p.playing
    await p.unload()  # the stream went away while paused
    await src.resume()
    assert p.loaded_url == "http://radio.invalid/a" and p.playing


async def test_a_backup_with_a_broken_alarm_is_refused(ctx) -> None:
    from dawn_core.system.backup import make_backup, restore_backup

    doc = make_backup(ctx)
    doc["db"]["alarms"] = [{"id": 1, "label": "Bad", "time": "25:61", "repeat": "daily", "days": "", "source": "chime:birds", "volume": 70}]
    with pytest.raises(ValueError, match="Bad"):
        await restore_backup(ctx, doc)
    doc["db"]["alarms"] = [{"id": 1, "label": "Good", "time": "06:30", "repeat": "custom", "days": "0,2", "source": "chime:birds", "volume": 70}]
    counts = await restore_backup(ctx, doc)
    assert counts["alarms"] == 1


async def test_alarm_players_are_separate_and_prepared(ctx) -> None:
    audio: AudioService = ctx.svc(AudioService)
    assert audio.player_name("dab", "alarm") == "dawn-alarm-dab" and audio.player_name("dab") == "dawn-dab"
    assert await audio.prepare_players(["dab", "chime"]) == []
    assert {"dawn-alarm-dab", "dawn-alarm-chime"} <= set(audio.players)
    assert audio.has_output()


async def test_audio_switches_to_pipewire_once_it_answers(ctx, monkeypatch) -> None:
    from dawn_core.audio import backend as be

    audio: AudioService = ctx.svc(AudioService)
    audio.backend = be.AudioBackend()  # what core ends up with when PipeWire was not up at start
    monkeypatch.setattr(ctx, "sim", False)
    ctx.config.audio.backend = "auto"

    async def up(self) -> None:
        self._eq_present = False

    monkeypatch.setattr(be.PipeWireBackend, "start", up)
    calls: list[tuple] = []

    async def fake_run(*cmd, timeout=5.0):
        calls.append(cmd)
        return 0, "[]"

    monkeypatch.setattr(be, "run", fake_run)
    await audio._retry_pipewire()
    assert audio.backend.name == "pipewire" and ctx.store.state.audio.backend == "pipewire"


def test_diagnostics_report_the_alarm_ladder_and_failing_storage() -> None:
    f = {
        "units": {}, "audio": {"backend": "pipewire", "sink": None, "sinks": [], "pinned": None, "volume": 40, "muted": False,
                               "active_source": "buzzer", "flowing": False, "eq_present": True, "ceiling": 100, "max_volume": 100},
        "alarms": {
            "engine_alive": True, "prepare": {"alarm_id": 1, "label": "Work", "at": "2026-10-07T06:30:00+11:00", "source": "dab:2002", "ready": False,
                                              "problems": ["no DAB signal on 9C"], "start_tier": "chime"},
            "ringing": {"label": "Work", "tier": "buzzer", "fallback_reason": "no audio after 8 s"},
            "backup_tone_24h": [{"at": "x"}], "missed_24h": [], "restored_24h": [{"at": "y"}], "backup_tone_problem": None, "gpio_buzzer": None,
        },
        "airplay": {"enabled": False}, "bluetooth": {"enabled": False},
        "display": {"face_clients": [{"connected_at": "2026-10-06T22:00:00"}], "face_mode": "ringing", "backlight": "sysfs", "sysfs": "x", "sysfs_writable": True,
                    "brightness": 15, "mode": "auto", "sensor_found": True, "sensor": "veml6030", "lux": 0.4, "night": True},
        "inputs": {"encoder": False, "button": False, "backend": None, "last": None},
        "weather": {"enabled": False},
        "system": {"config_error": None, "failed_services": {}, "throttled": None, "throttle_flags": [], "cpu_temp_c": None, "mem_used_percent": None,
                   "disk_data": None, "disk_root": None, "heartbeat_age_s": 1, "error_count": 0, "errors": [], "version": "0.1.0", "git_rev": None,
                   "model": "Raspberry Pi 4", "uptime_s": 60, "sim": False, "db_write_errors": 3, "db_last_write_error": "alarm Work: readonly"},
    }
    c = {x.id: x for x in system.checks(f)}
    assert c["audio.alarm.ringing"].status == "fail" and "backup tone" in c["audio.alarm.ringing"].detail
    assert c["audio.alarm.next"].status == "fail" and "the chime" in c["audio.alarm.next"].detail
    assert c["audio.alarm.backup"].status == "fail" and c["audio.alarm.restored"].status == "warn"
    assert c["system.db_writes"].status == "fail" and "read-only" in (c["system.db_writes"].hint or "")
    assert all(x.group == "Alarms" for k, x in c.items() if k.startswith("audio.alarm."))


def test_the_core_unit_keeps_the_alarms_going() -> None:
    """What the alarms rely on from systemd: restarted for ever, the watchdog, and /run/dawn kept across a restart
    (the default deletes it when the service stops, and with it the marker of a ring stopped on a read-only card)."""
    unit = (Path(__file__).resolve().parents[2] / "deploy" / "systemd" / "dawn-core.service").read_text()
    lines = {ln.strip() for ln in unit.splitlines()}
    for want in ("Restart=always", "StartLimitIntervalSec=0", "WatchdogSec=60", "RuntimeDirectory=dawn", "RuntimeDirectoryPreserve=yes"):
        assert want in lines, want


async def test_sim_hub_streams_flow_per_client() -> None:
    from dawn_sim.hub import create_app as hub_app
    from dawn_sim.simstate import STATE
    from httpx import ASGITransport, AsyncClient

    async def flowing(c: AsyncClient, client: str) -> bool:
        return (await c.get("/audio/flowing", params={"client": client})).json()["flowing"]

    old = (STATE.sdr_present, STATE.audio_flowing)
    try:
        async with AsyncClient(transport=ASGITransport(app=hub_app()), base_url="http://hub") as c:
            STATE.sdr_present = False  # the stick is out: DAB is silent, the chime and the backup tone are not
            assert not await flowing(c, "dawn-alarm-dab")
            assert await flowing(c, "dawn-alarm-chime") and await flowing(c, "dawn-backup-tone")
            STATE.audio_flowing = False  # nothing reaches the speaker at all
            assert not await flowing(c, "dawn-backup-tone")
    finally:
        STATE.sdr_present, STATE.audio_flowing = old
