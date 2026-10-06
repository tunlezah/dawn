from __future__ import annotations

from dawn_core.timesync.chrony import active_kind, classify, parse_sources, parse_tracking, to_time_sources
from dawn_core.timesync.gps import GpsdClient, GpsFix, nmea_checksum_ok, parse_nmea

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


def _nmea(body: str) -> str:
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"${body}*{cs:02X}"


def test_gpsd_reports_build_the_detail() -> None:
    seen: list[GpsFix] = []
    c = GpsdClient("127.0.0.1", 2947, seen.append)
    for msg in (
        '{"class":"VERSION","release":"3.25","rev":"3.25","proto_major":3,"proto_minor":15}',
        '{"class":"DEVICES","devices":[{"class":"DEVICE","path":"/dev/gps0","driver":"u-blox","subtype":"SW 1.00 (59842),HW 00070000","bps":9600,"activated":"2026-10-05T09:00:00.000Z"}]}',
        '{"class":"TPV","device":"/dev/gps0","mode":3,"status":1,"time":"2026-10-05T09:00:01.000Z","ept":0.005,"lat":-33.9,"lon":151.2,"altHAE":39.6,"eph":4.6,"epv":7.8}',
        '{"class":"SKY","device":"/dev/gps0","nSat":3,"uSat":2,"hdop":1.3,"pdop":2.1,"satellites":[{"PRN":5,"gnssid":0,"el":40,"az":120,"ss":41,"used":true},'
        '{"PRN":12,"gnssid":0,"el":22,"az":250,"ss":33,"used":true},{"PRN":133,"gnssid":1,"el":30,"az":300,"ss":0,"used":false}]}',
        '{"class":"SKY","device":"/dev/gps0","hdop":1.4}',  # gpsd also sends DOP-only SKY reports
        '{"class":"TOFF","device":"/dev/gps0","real_sec":1790000000,"real_nsec":0,"clock_sec":1790000000,"clock_nsec":140000000,"precision":-1}',
        "not json",
    ):
        c._handle(msg.encode())
    f = c.fix
    assert f.gpsd_version == "3.25" and f.driver == "u-blox" and f.bps == 9600 and f.device == "/dev/gps0"
    assert f.has_fix and f.mode == 3 and f.eph == 4.6 and f.ept == 0.005
    assert [s.prn for s in f.satellites] == [5, 12, 133] and f.satellites[2].gnss == "SBAS"
    assert f.sats_used == 2 and f.sats_seen == 3 and f.hdop == 1.4 and f.pdop == 2.1  # DOP-only report keeps the satellites
    assert f.snr_summary() == (37.0, 41)
    assert f.toff_ms == -140.0  # the serial time arrives 140 ms after the second it stamps
    d = f.as_dict()
    assert d["snr_used_avg"] == 37.0 and d["has_fix"] and d["satellites"][0]["ss"] == 41
    assert len(seen) >= 3


def test_nmea_gsv_gsa_satellites() -> None:
    fix, gsv = GpsFix(), {}
    assert parse_nmea(_nmea("GPGSA,A,3,05,12,,,,,,,,,,,2.1,1.3,1.6"), fix, gsv)
    assert parse_nmea(_nmea("GPGSV,2,1,05,05,40,120,41,12,22,250,33,13,10,080,,15,05,200,18"), fix, gsv)
    assert fix.satellites == []  # the cycle is only committed on its last sentence
    assert parse_nmea(_nmea("GPGSV,2,2,05,18,60,010,29"), fix, gsv)
    assert [s.prn for s in fix.satellites] == [5, 12, 13, 15, 18] and fix.sats_seen == 5
    assert fix.satellites[2].ss is None and fix.satellites[0].ss == 41.0 and fix.satellites[0].el == 40.0
    assert fix.hdop == 1.3 and fix.pdop == 2.1
    # the next GSA marks which are used
    assert parse_nmea(_nmea("GPGSA,A,3,05,12,18,,,,,,,,,,1.9,1.1,1.5"), fix, gsv)
    assert [s.prn for s in fix.satellites if s.used] == [5, 12, 18]


def test_nmea_multi_gnss_gsa_adds_up() -> None:
    """A GPS+GLONASS receiver sends one GNGSA per system in a burst: the second must not unmark the first."""
    fix, gsv = GpsFix(), {}
    parse_nmea(_nmea("GPGSV,1,1,04,01,40,120,41,03,22,250,33,06,10,080,30,09,05,200,28"), fix, gsv)
    parse_nmea(_nmea("GLGSV,1,1,03,65,50,100,35,66,30,200,31,67,20,300,27"), fix, gsv)
    assert len(fix.satellites) == 7
    parse_nmea(_nmea("GNGSA,A,3,01,03,06,09,,,,,,,,,1.8,1.0,1.5"), fix, gsv)
    parse_nmea(_nmea("GNGSA,A,3,65,66,67,,,,,,,,,,1.8,1.0,1.5"), fix, gsv)
    assert sorted(s.prn for s in fix.satellites if s.used) == [1, 3, 6, 9, 65, 66, 67]
    # the next epoch starts afresh after other sentences
    parse_nmea(_nmea("GNRMC,123519,A,3352.128,S,15112.558,E,0.0,0.0,061026,,"), fix, gsv)
    parse_nmea(_nmea("GNGSA,A,3,01,03,,,,,,,,,,,2.0,1.2,1.6"), fix, gsv)
    parse_nmea(_nmea("GNGSA,A,3,65,,,,,,,,,,,,2.0,1.2,1.6"), fix, gsv)
    assert sorted(s.prn for s in fix.satellites if s.used) == [1, 3, 65]


def test_chrony_diagnostic_commands() -> None:
    from dawn_core.timesync.chrony import (
        NTP_SHM_KEY,
        SELECT_STATES,
        parse_activity,
        parse_ntpdata,
        parse_selectdata,
        parse_sourcestats,
        parse_sysvipc_shm,
        reach_count,
    )

    # chronyc -c sourcestats: name, samples, runs, span, freq, skew, offset, std dev
    st = parse_sourcestats("GPS,12,7,180,-0.002,0.045,-0.000001234,0.000023456\nDAB,8,5,120,0.120,2.400,-0.012000000,0.004500000\n")
    assert st[0].name == "GPS" and st[0].samples == 12 and abs(st[1].std_dev_s - 0.0045) < 1e-12
    # selectdata: every option letter is its own CSV field
    sel = parse_selectdata(
        "*,GPS,N,-,P,T,-,-,-,P,T,-,-,3,1.0,-0.000123000,0.000456000,Normal\n"
        "T,DAB,N,-,-,-,-,-,-,-,-,-,-,9,1.0,-0.900000000,0.950000000,Normal\n"
        "x,ntp.example.au,N,-,-,-,-,-,-,-,-,-,-,45,1.0,0.100000000,0.200000000,Normal\n"
    )
    assert [s.state for s in sel] == ["*", "T", "x"] and sel[0].options == "PT" and sel[0].effective == "PT"
    assert sel[1].last_sample_s == 9 and sel[1].interval_lo_s == -0.9 and sel[2].leap == "Normal"
    assert SELECT_STATES["T"][0] == "disagrees with trusted"
    # ntpdata: 38 fields per NTP source
    nd = parse_ntpdata(
        "162.159.200.1,A29FC801,123,192.168.1.42,C0A8012A,Normal,4,Server,3,6,64,-25,0.000000030,0.012345,0.000456,0A1B2C3D,,"
        "1790000000.123456789,0.002100000,0.012300000,0.000400000,0.000020000,+0.05,111,111,1111,No,No,K,K,20,19,18,18,0,0,0,0\n"
    )
    assert nd[0].remote == "162.159.200.1" and nd[0].stratum == 3 and nd[0].poll_s == 64 and nd[0].offset_s == 0.0021
    assert nd[0].tests == "111 111 1111" and (nd[0].total_tx, nd[0].total_rx, nd[0].total_valid_rx) == (20, 19, 18)
    act = parse_activity("4,1,0,0,2\n")
    assert act and act.online == 4 and act.offline == 1 and act.unresolved == 2
    assert reach_count(377) == 8 and reach_count(1) == 1 and reach_count(0) == 0 and reach_count(17) == 4
    shm = parse_sysvipc_shm(
        "       key      shmid perms                  size  cpid  lpid nattch   uid   gid  cuid  cgid      atime      dtime      ctime                   rss                  swap\n"
        f"{NTP_SHM_KEY}          0   600                    96   612   734      2     0     0     0     0 1790000000          0 1790000000                  4096                     0\n"
        f"{NTP_SHM_KEY + 2}          1   666                    96   612   950      1   106   111   106   111 1790000001          0 1790000000                  4096                     0\n"
        "123456          2   600   1024   1   1   0   0   0   0   0   0   0   0   0   0\n"
    )
    assert [(s.unit, s.nattch, s.perms) for s in shm] == [(0, 2, "600"), (2, 1, "666"), (None, 0, "600")]


def test_tracking_full_row() -> None:
    t = parse_tracking("47505300,GPS,1,1790000000.123456789,-0.000000400,0.000000300,0.000001000,-12.345,0.001,0.050,0.000100000,0.000500000,16.1,Normal\n")
    assert t and t.freq_ppm == -12.345 and t.skew_ppm == 0.05 and t.update_interval_s == 16.1 and t.root_dispersion_s == 0.0005
    assert t.ref_time == 1790000000.123456789
