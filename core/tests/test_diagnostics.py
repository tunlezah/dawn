from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from dawn_core.api.router_diag import constellation_stats, downsample
from dawn_core.db.engine import Database
from dawn_core.db.models import MetricRow
from dawn_core.diagnostics import dab, gps, network, system, timepath
from dawn_core.diagnostics.checks import Check, summarise
from dawn_core.diagnostics.history import History


def by_id(checks: list[Check]) -> dict[str, Check]:
    return {c.id: c for c in checks}


# ---- DAB -----------------------------------------------------------------
def dab_facts(**over) -> dict:
    f = {
        "enabled": True, "sdr": {"present": True, "tuner": "Rafael Micro R828D", "usb": [{"vid": "0bda", "pid": "2838"}]}, "dvb_driver_loaded": False,
        "unit": "active", "service_name": "dawn-dab", "reachable": True, "welle_url": "http://127.0.0.1:8000",
        "cmdline": "/usr/local/bin/welle-cli -c 9A -w 8000", "expected_args": ["-w", "8000"], "arg_notes": [], "gain_config": None,
        "mux": {"channel": "9A", "mhz": 204.928, "ensemble": "ABC Sydney", "ensemble_id": "1001", "sync": True, "snr": 15.0, "signal": 75,
                "freq_correction_hz": 140.0, "fic_crc_errors": 3, "fic_errors_per_min": 0.0, "fct0_age_s": 5.0, "gain_db": 33.8},
        "services": [{"sid": "1002", "label": "triple j", "decoding": True, "audio_format": "HE-AAC v2, 48 kHz Stereo @ 64 kbit/s",
                      "rates": {"frame": 0.0, "rs": 0.0, "aac": 0.0, "at": time.time()}}],
        "playing": None, "stations": 28, "ensembles": [{}, {}, {}], "last_scan_at": "2026-10-05T19:55:50+11:00", "scanning": False,
        "presets_unknown": [], "alarms_unknown": [], "restarts_24h": [], "fallbacks_24h": [], "messages": [],
    }
    f.update(over)
    return f


def test_dab_healthy() -> None:
    c = by_id(dab.checks(dab_facts()))
    assert all(x.status in ("ok", "info") for x in c.values()), [(x.id, x.status, x.detail) for x in c.values()]
    assert "204.928 MHz" in c["dab.sync"].detail and "15.0 dB" in c["dab.snr"].detail


def test_dab_problems_have_causes_and_fixes() -> None:
    c = by_id(dab.checks(dab_facts(sdr={"present": False, "tuner": None, "usb": []}, unit="activating", reachable=False, mux=None, services=[])))
    assert c["dab.sdr"].status == "fail" and c["dab.decoder"].status == "fail" and "dab.restart" in c["dab.decoder"].actions
    assert "dab.sync" not in c  # nothing more to say without the decoder
    weak = dab_facts()
    weak["mux"] = {**weak["mux"], "snr": 6.5, "fic_errors_per_min": 120.0}
    c = by_id(dab.checks(weak))
    assert c["dab.snr"].status == "fail" and "antenna" in (c["dab.snr"].hint or "")
    assert c["dab.fic"].status == "fail"
    c = by_id(dab.checks(dab_facts(dvb_driver_loaded=True, arg_notes=["dropped -C without a programme count"])))
    assert c["dab.dvb"].status == "fail" and c["dab.args"].status == "warn"
    c = by_id(dab.checks(dab_facts(cmdline="/usr/local/bin/welle-cli -c 9A -w 8000", expected_args=["-w", "8000", "-g", "16"])))
    assert c["dab.args_pending"].status == "info" and "dab.restart" in c["dab.args_pending"].actions
    nosync = dab_facts()
    nosync["mux"] = {**nosync["mux"], "sync": False}
    c = by_id(dab.checks(nosync))
    assert c["dab.sync"].status == "fail" and "dab.scan" in c["dab.sync"].actions and "dab.snr" not in c


def test_dab_playing_and_history() -> None:
    f = dab_facts(playing={"sid": "1002", "label": "triple j", "flowing": True})
    f["services"][0]["rates"] = {"frame": 2.0, "rs": 9.0, "aac": 1.0, "at": time.time()}
    c = by_id(dab.checks(f))
    assert c["dab.audio"].status == "warn" and "9 Reed-Solomon" in c["dab.audio"].detail
    c = by_id(dab.checks(dab_facts(playing={"sid": "1002", "label": "triple j", "flowing": False})))
    assert c["dab.audio"].status == "fail"
    c = by_id(dab.checks(dab_facts(alarms_unknown=["Weekday"], fallbacks_24h=[{"at": "x"}], restarts_24h=["x"])))
    assert c["dab.alarm_sources"].status == "warn" and c["dab.history"].detail.startswith("1 alarm fell back to the chime; an alarm found DAB not working")
    assert dab.checks(dab_facts(enabled=False))[0].status == "off"


# ---- GPS -----------------------------------------------------------------
def gps_facts(**over) -> dict:
    sats = [{"prn": i, "ss": 40 - i * 2, "used": i < 6, "gnss": "GPS"} for i in range(9)]
    d = {"source": "gpsd", "connected": True, "connect_error": None, "gpsd_version": "3.25", "driver": "u-blox", "device": "/dev/gps0", "bps": 9600,
         "mode": 3, "has_fix": True, "lat": -33.87, "lon": 151.21, "sats_used": 6, "sats_seen": 9, "satellites": sats, "snr_used_avg": 35.0,
         "eph": 4.6, "hdop": 1.3, "toff_ms": -140.0}
    f = {"enabled": True, "configured_source": "auto", "detail": d, "usb": [{"vid": "1546", "product": "u-blox 7"}], "paths": {"/dev/gps0": True},
         "unit": "active", "last_msg_age_s": 0.5, "toff_age_s": 1.0, "location_source": "gps", "prefer_gps": True, "configured_distance_km": 0.2}
    f.update(over)
    return f


def test_gps_healthy() -> None:
    c = by_id(gps.checks(gps_facts()))
    assert all(x.status in ("ok", "info") for x in c.values()), [(x.id, x.status) for x in c.values()]
    assert "140 ms" in c["gps.time"].detail


def test_gps_unplugged_reports_the_cause_only() -> None:
    f = gps_facts(usb=[], paths={"/dev/gps0": False}, last_msg_age_s=40.0)
    f["detail"] = {**f["detail"], "driver": None, "has_fix": False, "mode": 0, "satellites": []}
    c = by_id(gps.checks(f))
    assert c["gps.receiver"].status == "fail" and c["gps.data"].status == "fail"
    assert "gps.signal" not in c and "gps.fix" not in c  # consequences, not causes


def test_gps_weak_sky() -> None:
    f = gps_facts()
    f["detail"] = {**f["detail"], "satellites": [{"prn": i, "ss": 26 - i, "used": i < 2} for i in range(6)], "has_fix": False, "mode": 1}
    c = by_id(gps.checks(f))
    assert c["gps.signal"].status == "warn" and c["gps.fix"].status == "fail" and "window" in (c["gps.fix"].hint or "")
    f["detail"]["satellites"] = [{"prn": 1, "ss": 0, "used": False}]
    assert by_id(gps.checks(f))["gps.signal"].status == "fail"


# ---- time ------------------------------------------------------------------
SOURCES = "#,*,GPS,0,4,377,8,0.000003100,0.000003100,0.000100000\n#,-,DAB,0,4,377,8,-0.012000000,-0.012000000,0.000100000\n^,?,203.0.113.10,3,6,0,1800,0,0,0\n"
SELECT = ("*,GPS,N,-,P,T,-,-,-,P,T,-,-,8,1.0,-0.000123000,0.000456000,Normal\n"
          "P,DAB,N,-,-,-,-,-,-,-,-,-,-,8,1.0,-0.9,0.9,Normal\nM,203.0.113.10,N,-,-,-,-,-,-,-,-,-,-,1800,1.0,0,0,Normal\n")
NTPDATA = ("203.0.113.10,A29FC801,123,192.168.1.42,C0A8012A,Normal,4,Server,3,6,64,-25,0.000000030,0.012345,0.000456,0A1B2C3D,,"
           "1790000000.1,0.002,0.0123,0.0004,0.00002,+0.05,111,111,1111,No,No,K,K,20,0,0,0,0,0,0,0\n")


def time_facts(**over) -> dict:
    f = {
        "units": {"chrony": "active", "dawn-timed": "active"}, "chronyc_error": None, "select_error": None, "ntpdata_error": None,
        "tracking": {"refid": "47505300", "refname": "GPS", "stratum": 1, "system_offset_s": 0.0000004}, "synced": True,
        "sources": timepath.merge_sources(SOURCES, None, SELECT, NTPDATA, 600), "activity": {"online": 1, "offline": 0},
        "shm": [{"unit": 0, "perms": "600", "nattch": 2}, {"unit": 2, "perms": "666", "nattch": 2}],
        "servers": ["au.pool.ntp.org"], "dns": {"au.pool.ntp.org": {"addresses": ["203.0.113.10"], "error": None}}, "online": True,
        "timed": {"mode": "fic", "samples_written": 120, "last_sample_at": "2026-10-05T09:00:00.000+00:00", "fig010_long": 120, "fig010_short": 0,
                  "fibs": 3000, "shm_attached": True, "shm_unit": 2, "last_offset_ms": -1.5, "dry_run": False},
        "timed_age_s": 1.0, "timed_error": None,
        "gps": {"enabled": True, "has_fix": True, "unit": "active"}, "dab": {"enabled": True, "sync": True, "channel": "9A", "ensemble": "ABC Sydney"},
    }
    f.update(over)
    return f


def test_merge_sources_takes_chronys_reasons() -> None:
    s = {x["name"]: x for x in timepath.merge_sources(SOURCES, None, SELECT, NTPDATA, 600)}
    assert s["GPS"]["used"] and s["GPS"]["selected"] and s["GPS"]["reach_count"] == 8
    assert not s["DAB"]["used"] and s["DAB"]["state_label"] == "not preferred" and "prefer" in s["DAB"]["reason"]
    assert s["203.0.113.10"]["ntp"]["total_tx"] == 20 and s["203.0.113.10"]["reach_count"] == 0


def test_time_chains() -> None:
    c = by_id(timepath.checks(time_facts()))
    assert c["time.synced"].status == "ok" and c["time.gps.source"].status == "ok"
    assert c["time.dab.source"].status == "info" and "prefer option" in c["time.dab.source"].detail
    assert c["time.dab.fig"].status == "ok"
    # NTP: twenty requests, no replies -> blocked port, said plainly
    assert c["time.ntp.replies"].status == "fail" and "123" in (c["time.ntp.replies"].hint or "")
    assert c["time.gps.source"].group == "GPS → chrony"


def test_time_failures() -> None:
    f = time_facts(synced=False, tracking=None, shm=[{"unit": 2, "perms": "666", "nattch": 2}],
                   timed={"mode": "fic", "samples_written": 0, "fibs": 5000, "fig010_long": 0, "fig010_short": 0, "shm_attached": False,
                          "shm_error": "[Errno 13] Permission denied", "shm_unit": 2})
    c = by_id(timepath.checks(f))
    assert c["time.synced"].status == "fail" and "time.burst" in c["time.synced"].actions
    assert c["time.gps.shm"].status == "fail" and "gps.restart" in c["time.gps.shm"].actions
    assert c["time.dab.fig"].status == "warn" and "does not broadcast" in (c["time.dab.fig"].hint or "")
    assert c["time.dab.shm"].status == "fail" and "perm=0666" in (c["time.dab.shm"].hint or "")
    c = by_id(timepath.checks(time_facts(units={"chrony": "failed", "dawn-timed": "inactive"})))
    assert c["time.chrony"].status == "fail" and c["time.dab.timed"].status == "fail"
    c = by_id(timepath.checks(time_facts(select_error="sudo: a password is required")))
    assert c["time.select_access"].status == "info"


def test_dab_time_far_off_is_called_out() -> None:
    src = SOURCES.replace("-0.012000000,-0.012000000", "-4.500000000,-4.500000000")
    c = by_id(timepath.checks(time_facts(sources=timepath.merge_sources(src, None, SELECT, None, 600))))
    assert c["time.dab.offset"].status == "warn" and "-4.5 s" in c["time.dab.offset"].detail


# ---- network and system ------------------------------------------------------
def test_network_parsers_and_wifi() -> None:
    route = "Iface\tDestination\tGateway \tFlags\nwlan0\t00000000\t0101A8C0\t0003\n"
    assert network.parse_route(route) == {"interface": "wlan0", "gateway": "192.168.1.1"}
    wl = "Inter-| sta-|   Quality\n face | tus | link level noise\n wlan0: 0000   40.  -81.  -256  0 0 0 0 0 0\n"
    assert network.parse_wireless(wl) == {"wlan0": {"quality_percent": 57, "level_dbm": -81.0}}
    f = {"online": True, "ip": "192.168.1.42", "ssid": "Home", "interface": "wlan0", "hotspot": False, "hotspot_ssid": None, "check_host": "1.1.1.1",
         "route": {"interface": "wlan0", "gateway": "192.168.1.1"}, "nameservers": ["192.168.1.1"], "dns_host": "api.open-meteo.com",
         "dns_addresses": [], "dns_error": "Temporary failure in name resolution", "wireless": network.parse_wireless(wl),
         "units": {"NetworkManager": "active", "avahi-daemon": "inactive"}, "hostname": "dawn", "port": 8080, "sim": False}
    c = by_id(network.checks(f))
    assert c["net.dns"].status == "fail" and c["net.wifi.wlan0"].status == "fail" and c["net.mdns"].status == "warn"
    assert ":8080" in c["net.url"].detail


def test_system_power_disk_config() -> None:
    f = {
        "units": {"dawn-face": "active", "gpsd": "failed", "shairport-sync": "active", "nqptp": "active", "bluetooth": "active"},
        "audio": {"backend": "pipewire", "sink": {"description": "SHIM", "kind": "hifiberry", "name": "x", "id": "1"}, "sinks": [{"description": "SHIM", "kind": "hifiberry", "name": "x", "id": "1"}],
                  "pinned": "hifiberry", "volume": 0, "muted": False, "active_source": "none", "flowing": False, "eq_present": False, "ceiling": 80, "max_volume": 100},
        "airplay": {"enabled": True, "available": True, "name": "Dawn", "active": False, "pipe": True},
        "bluetooth": {"enabled": False},
        "display": {"face_clients": [], "face_mode": "standby", "backlight": "sysfs", "sysfs": "/sys/class/backlight/x", "sysfs_writable": False,
                    "brightness": 40, "mode": "auto", "sensor_found": False, "sensor": None, "lux": None, "night": False},
        "inputs": {"encoder": True, "button": False, "backend": "gpio", "last": None},
        "weather": {"enabled": True, "available": True, "stale": True, "fetched_at": "2026-10-05T03:00:00+11:00", "error": "ConnectError: x"},
        "system": {"config_error": "display.layout: bad", "failed_services": {}, "throttled": 0x50005, "throttle_flags": ["under-voltage"], "cpu_temp_c": 83.0,
                   "mem_used_percent": 40.0, "disk_data": {"total": 8e9, "free": 1e8}, "disk_root": {"total": 8e9, "free": 4e9}, "heartbeat_age_s": 2,
                   "error_count": 0, "errors": [], "version": "0.1.0", "git_rev": None, "model": "Raspberry Pi 4", "uptime_s": 3600, "sim": False},
    }
    c = by_id(system.checks(f))
    assert c["audio.eq"].status == "warn" and c["audio.volume"].status == "warn"
    assert c["display.backlight"].status == "fail" and c["display.face"].status == "warn" and "display.restart_face" in c["display.face"].actions
    assert c["system.power"].status == "fail" and "5 V 3 A" in (c["system.power"].hint or "")
    assert c["system.temp"].status == "fail" and c["system.disk_data"].status == "warn" and c["system.config"].status == "fail"
    assert c["system.units"].status == "fail" and "gpsd" in c["system.units"].detail
    assert c["weather.fetch"].status == "warn" and c["bluetooth.enabled"].status == "off"
    s = summarise(list(c.values()))
    assert s["counts"]["fail"] >= 5 and s["problems"][0].status == "fail"


# ---- history -------------------------------------------------------------
def test_history_minutes_and_series() -> None:
    db = Database(":memory:")
    h = History(db)
    t0 = (int(time.time() // 60) - 10) * 60.0
    for i in range(6):  # one minute, 10 s apart
        assert not h.add({"dab.snr": 10 + i, "dab.sync": 1.0 if i % 2 else 0.0, "bogus": 1}, now=t0 + i * 10)
    assert h.add({"dab.snr": 20}, now=t0 + 60)  # the next minute closes the first
    with db.session() as s:
        rows = s.exec(__import__("sqlmodel").select(MetricRow)).all()
    assert len(rows) == 1
    out = h.series(["dab.snr", "dab.sync", "nope"], hours=1, points=60, now=t0 + 90)
    assert set(out) == {"dab.snr", "dab.sync"} and out["dab.sync"]["unit"] == "%"
    first = out["dab.snr"]["points"][0]
    assert first[1:] == [12.5, 10.0, 15.0] and out["dab.sync"]["points"][0][1] == 50.0
    assert out["dab.snr"]["points"][-1][1] == 20.0  # the open minute is included
    with db.session() as s:
        s.add(MetricRow(at=datetime.now(UTC) - timedelta(days=9), data="{}"))
        s.commit()
    assert h.prune(7) == 1


def test_plot_helpers() -> None:
    assert downsample([1, 5, 2, 8], 2) == [5, 8] and downsample([1.0, 2.0], 4) == [1.0, 2.0]
    clean = constellation_stats([45, -45, 135, -135] * 100)
    assert clean["phase_error_deg"] == 0.0 and sum(clean["histogram"]) == 400
    noisy = constellation_stats([45 + d for d in (-20, 20, -10, 10)] * 50)
    assert 14 < noisy["phase_error_deg"] < 17


# ---- API ---------------------------------------------------------------------
@pytest.fixture()
async def client(tmp_config):
    from dawn_core.app import create_app
    from dawn_core.config import ConfigManager

    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def test_diag_api_degrades_without_hardware(client: AsyncClient) -> None:
    """No simulator running: every probe fails, and that must come back as findings, never as a 500."""
    closed = "http://127.0.0.1:9"  # discard port: nothing answers, even with a simulator running on this machine
    r = await client.patch("/api/config", json={"dab": {"welle_url": closed}, "sim": {"hub_url": closed}})
    assert r.status_code == 200
    r = await client.post("/api/diag/run")
    assert r.status_code == 200
    rep = r.json()
    assert rep["checks"] and set(rep["counts"]) == {"fail", "warn", "info", "ok", "off"}
    assert {c["area"] for c in rep["checks"]} >= {"dab", "gps", "time", "network", "system"}
    s = (await client.get("/api/state")).json()
    assert s["diagnostics"]["updated_at"] and s["diagnostics"]["fail"] + s["diagnostics"]["warn"] >= 1
    r = await client.get("/api/diag/live/dab")
    assert r.status_code == 200 and "checks" in r.json()
    assert (await client.get("/api/diag/live/nope")).status_code == 404
    r = await client.get("/api/diag/history?metrics=dab.snr,time.system&hours=1")
    assert r.status_code == 200 and set(r.json()["metrics"]) == {"dab.snr", "time.system"}
    r = await client.get("/api/diag/dab/plot/spectrum")
    assert r.status_code == 200 and r.json()["available"] is False
    assert (await client.get("/api/diag/dab/plot/etc")).status_code == 404
    assert (await client.post("/api/diag/action/nope", json={})).status_code == 404
    r = await client.post("/api/diag/action/dab.retune", json={"value": "not-a-channel"})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert any(a["id"] == "time.burst" for a in (await client.get("/api/diag/actions")).json())
