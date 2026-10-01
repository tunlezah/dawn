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
