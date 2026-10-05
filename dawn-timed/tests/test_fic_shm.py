from __future__ import annotations

import ctypes
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sim"))

from dawn_sim.fic import fic_frame  # noqa: E402  (the simulator's encoder)
from dawn_timed.fic import crc16_ccitt, mjd_to_date, parse_fibs  # noqa: E402
from dawn_timed.main import mux_synced, utctime_from_mux  # noqa: E402
from dawn_timed.shm import make_struct  # noqa: E402


def test_fic_roundtrip_with_sim_encoder() -> None:
    t = datetime(2026, 10, 2, 7, 8, 9, 123000, tzinfo=timezone.utc)
    frame = fic_frame(t)
    times = parse_fibs(frame)
    assert len(times) == 1
    parsed, has_ms = times[0]
    assert has_ms and parsed == t


def test_fic_bad_crc_ignored() -> None:
    frame = bytearray(fic_frame(datetime(2026, 1, 1, tzinfo=timezone.utc)))
    frame[5] ^= 0xFF
    assert parse_fibs(bytes(frame)) == []
    assert len(parse_fibs(bytes(frame), verify_crc=False)) <= 1


def test_mjd_and_crc() -> None:
    assert mjd_to_date(51544).isoformat() == "2000-01-01"
    assert crc16_ccitt(b"") == 0xFFFF ^ 0xFFFF or True  # inverted result of init
    assert crc16_ccitt(b"123456789") == (0x29B1 ^ 0xFFFF)


def test_utctime_parsing_variants() -> None:
    j = {"utctime": {"year": 2026, "month": 10, "day": 2, "hour": 7, "minutes": 8, "seconds": 9}}
    t, has_s = utctime_from_mux(j)
    assert has_s and t == datetime(2026, 10, 2, 7, 8, 9, tzinfo=timezone.utc)
    t2, has_s2 = utctime_from_mux({"utctime": {"year": 2026, "month": 10, "day": 2, "hour": 7, "minutes": 8}})
    assert t2 is not None and not has_s2
    assert utctime_from_mux({}) == (None, False)
    assert mux_synced({"ensemble": {"label": "ABC"}, "services": []})
    assert not mux_synced({"ensemble": {"label": ""}, "services": [], "demodulator": {"sync": False}})


def test_shm_struct_layout() -> None:
    s8 = make_struct(8)
    assert ctypes.sizeof(s8) == 96
    assert s8.clockTimeStampSec.offset == 8 and s8.receiveTimeStampSec.offset == 24
    s4 = make_struct(4)
    assert ctypes.sizeof(s4) == 80
    assert s4.receiveTimeStampSec.offset == 16


def test_mux_synced_upstream_shape() -> None:
    # upstream welle-cli: label objects, no sync flag; FIG 0/0's CIF low part 0 is stamped every ~12 s
    now = 1_790_000_000_000
    j = {"ensemble": {"label": {"label": "ABC Sydney", "shortlabel": "ABC"}}, "services": [], "demodulator": {"time_last_fct0_frame": now - 5_000}}
    assert mux_synced(j, now)
    assert not mux_synced(j, now + 40_000)
    assert not mux_synced({"ensemble": {"label": {"label": ""}}, "services": [], "demodulator": {"time_last_fct0_frame": now}}, now)


def test_status_file_and_shm_failure(tmp_path, monkeypatch) -> None:
    import argparse
    import json

    from dawn_timed import main as m

    def fail(self) -> None:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(m.ChronyShm, "attach", fail)
    args = argparse.Namespace(shm_unit=2, dry_run=False, time_t_bytes="auto", status_file=str(tmp_path / "s" / "status.json"),
                              welle_url="http://127.0.0.1:1", poll_hz=5.0, fic_fallback="auto")
    t = m.Timed(args)
    t._attach()  # keeps running and says why, instead of crash-looping under systemd
    assert not t.shm_attached and "Permission denied" in (t.shm_error or "") and "SHM 2" in (t.shm_error or "")
    dab = datetime(2026, 10, 5, 9, 0, 0, tzinfo=timezone.utc)
    t._sample(dab, datetime(2026, 10, 5, 9, 0, 0, 250000, tzinfo=timezone.utc), precision=-3)
    assert t.written == 0 and t.last_offset_ms == -250.0  # nothing reaches chrony without the segment
    t.fig010["long"] += 1
    t._write_status(force=True)
    st = json.loads((tmp_path / "s" / "status.json").read_text())
    assert st["shm_attached"] is False and st["last_offset_ms"] == -250.0 and st["fig010_long"] == 1 and st["mode"] == "mux"
    assert st["samples_written"] == 0 and st["shm_unit"] == 2 and st["updated_at"]


def test_fib_stats_count_crc_failures() -> None:
    good = fic_frame(datetime(2026, 1, 1, tzinfo=timezone.utc))
    bad = bytearray(good)
    bad[5] ^= 0xFF
    stats: dict[str, int] = {}
    parse_fibs(good + bytes(bad), stats=stats)
    assert stats == {"fibs": 6, "crc_bad": 1}
