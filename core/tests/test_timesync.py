from __future__ import annotations

from dawn_core.timesync.chrony import active_kind, classify, parse_sources, parse_tracking, to_time_sources
from dawn_core.timesync.gps import GpsFix, nmea_checksum_ok, parse_nmea

SOURCES = """#,*,GPS,0,4,377,13,-0.000012345,-0.000010000,0.000123000
#,+,DAB,0,4,377,8,-0.012000000,-0.011000000,0.001000000
^,-,time.cloudflare.com,3,6,377,45,0.002100000,0.002000000,0.004000000
^,?,ntp.example.au,0,6,0,1800,0.000000000,0.000000000,0.000000000
"""
TRACKING = "47505300,GPS,1,1700000000.123,0.000000400,0.000000300,0.000001000,1.2,0.001,0.5,0.0001,0.0005,1.0,Normal\n"


def test_parse_sources() -> None:
    s = parse_sources(SOURCES)
    assert len(s) == 4
    assert s[0].mode == "#" and s[0].state == "*" and s[0].name == "GPS" and s[0].reach == 377
    assert abs(s[1].offset_s - -0.012) < 1e-9
    assert s[3].state == "?" and s[3].last_rx_s == 1800


def test_to_time_sources_live_flags() -> None:
    ts = to_time_sources(parse_sources(SOURCES), stale_after_s=600)
    by = {t.name: t for t in ts}
    assert by["GPS"].live and by["GPS"].selected and by["GPS"].kind == "gps"
    assert by["DAB"].live and by["DAB"].kind == "dab" and by["DAB"].offset_ms == -12.0
    assert by["time.cloudflare.com"].kind == "ntp" and by["time.cloudflare.com"].live
    assert not by["ntp.example.au"].live


def test_tracking_and_active() -> None:
    t = parse_tracking(TRACKING)
    assert t and t.refname == "GPS" and t.stratum == 1 and t.synced
    assert active_kind(parse_sources(SOURCES), t) == "GPS"
    no_star = SOURCES.replace("#,*,GPS", "#,?,GPS").replace("#,+,DAB", "#,*,DAB")
    assert active_kind(parse_sources(no_star), t) == "DAB"
    unsynced = parse_tracking("7F7F0101,,0,0.0,0,0,0,0,0,0,0,0,0,Not synchronised\n")
    assert unsynced and not unsynced.synced
    assert active_kind([], unsynced) == "none"
    assert classify("2.au.pool.ntp.org") == "ntp" and classify("PPS") == "gps"


def test_nmea_parse_gga_rmc() -> None:
    fix = GpsFix()
    gga = "$GPGGA,120000.00,3352.1280,S,15112.5580,E,1,09,1.2,20.0,M,19.6,M,,"
    cs = 0
    for ch in gga[1:]:
        cs ^= ord(ch)
    line = f"{gga}*{cs:02X}"
    assert nmea_checksum_ok(line)
    assert parse_nmea(line, fix)
    assert fix.mode == 2 and abs(fix.lat - -33.8688) < 1e-4 and abs(fix.lon - 151.2093) < 1e-4 and fix.sats_used == 9
    rmc = "$GPRMC,120000.00,A,3352.1280,S,15112.5580,E,0.05,0.00,021026,,,A"
    cs = 0
    for ch in rmc[1:]:
        cs ^= ord(ch)
    assert parse_nmea(f"{rmc}*{cs:02X}", fix)
    assert fix.time == "2026-10-02T12:00:00Z"
    assert not parse_nmea("$GPGGA,garbage*00", fix)  # bad checksum ignored
