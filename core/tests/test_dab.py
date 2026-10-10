from __future__ import annotations

from dawn_core.dab.logos import monogram_letters, monogram_svg, service_colour
from dawn_core.dab.scanner import scan, scan_order
from dawn_core.dab.welle import R82XX_GAINS_DB, MuxInfo, ServiceInfo, floats, gain_index, norm_sid, parse_mux, welle_args, welle_sid


def test_norm_sid_variants() -> None:
    assert norm_sid("1002") == "1002"
    assert norm_sid("0x1002") == "1002"
    assert norm_sid(0x1002) == "1002"
    assert norm_sid("100A") == "100a"
    assert norm_sid("E1C00098") == "e1c00098"


# mux.json as upstream welle-cli (welle.io master, jsonconvert.cpp) writes it: labels are objects, ids carry 0x,
# no sync flag, utctime without seconds, every service has a handler (audiolevel time 0 until decoded)
NOW_MS = 1_790_000_000_000
UPSTREAM = {
    "receiver": {"hardware": {"name": "Generic RTL2832U OEM :: 00000001", "gain": 33.8},
                 "software": {"name": "welle.io", "version": "v2.7", "fftwindowplacement": "EarliestPeakWithBinning",
                              "coarsecorrectorenabled": True, "freqsyncmethod": "GetMiddle", "lastchannelchange": NOW_MS - 60_000}},
    "ensemble": {"label": {"label": "ABC Sydney      ", "shortlabel": "ABC", "fig2label": "", "fig2rfu": False, "fig2charset": "Undefined"},
                 "id": "0x1001", "ecc": "0xf0"},
    "services": [
        {"sid": "0x1002", "programType": 10, "ptystring": "Pop Music", "language": 9, "languagestring": "English",
         "label": {"label": "triple j", "shortlabel": "triplej", "fig2label": "", "fig2rfu": False, "fig2charset": "Undefined"},
         "components": [{"componentnr": 0, "primary": True, "caflag": False, "transportmode": "audio", "label": {"label": ""},
                         "subchannel": {"subchid": 3, "bitrate": 64, "cu": 48, "sad": 96, "protection": "EEP 3-A", "language": 0},
                         "scid": None, "ascty": "DAB+", "dscty": None}],
         "channels": 2, "samplerate": 48000, "mode": "HE-AAC v2, 48 kHz Stereo @ 64 kbit/s",
         "mot": {"time": NOW_MS // 1000, "lastchange": NOW_MS // 1000 - 30}, "dls": {"label": "Now playing", "time": NOW_MS // 1000, "lastchange": 1},
         "errorcounters": {"frameerrors": 2, "rserrors": 17, "aacerrors": 1, "time": NOW_MS // 1000},
         "xpaderror": {"haserror": False}, "url_mp3": "/mp3/0x1002",
         "audiolevel": {"time": NOW_MS // 1000 - 1, "left": 12000, "right": 11000}},
        {"sid": "0x100a", "programType": 0, "ptystring": "None", "language": 0, "languagestring": "Unknown language",
         "label": {"label": "SBS Chill", "shortlabel": "SBSChill"},
         "components": [{"transportmode": "audio", "subchannel": {"subchid": 9, "bitrate": 48}, "ascty": "DAB+"}],
         "channels": 2, "samplerate": 0, "mode": "", "mot": {"time": 0, "lastchange": 0}, "dls": {"label": "", "time": 0, "lastchange": 0},
         "errorcounters": {"frameerrors": 0, "rserrors": 0, "aacerrors": 0, "time": 0}, "xpaderror": {"haserror": False},
         "url_mp3": "/mp3/0x100a", "audiolevel": {"time": 0, "left": 0, "right": 0}},
    ],
    "utctime": {"year": 2026, "month": 10, "day": 5, "hour": 20, "minutes": 14, "lto": 11.0},
    "messages": ["Mon Oct  5 20:14:01 2026\n.120 INFO : Tuning to 9A"],
    "tii": [{"comb": 3, "pattern": 7, "delay": 520, "delay_km": 7.8, "error": 0.02}],
    "cir_peaks": [{"index": 1050, "value": -3.1}],
    "demodulator": {"fic": {"numcrcerrors": 12}, "time_last_fct0_frame": NOW_MS - 7_000, "snr": 14.2, "frequencycorrection": 142.0},
}


def test_parse_mux_upstream_shape() -> None:
    m = parse_mux(UPSTREAM, now_ms=NOW_MS)
    assert m.sync and m.ensemble_label == "ABC Sydney" and m.ensemble_short == "ABC" and m.ensemble_id == "1001"
    assert m.snr == 14.2 and m.freq_correction_hz == 142.0 and m.fic_crc_errors == 12 and m.gain_db == 33.8
    assert m.software == "welle.io v2.7" and m.tii[0]["delay_km"] == 7.8 and m.cir_peaks[0]["value"] == -3.1
    assert m.messages and m.utc_time["minutes"] == 14
    tj, chill = m.services
    assert tj.sid == "1002" and tj.label == "triple j" and tj.short_label == "triplej"
    assert tj.codec == "DAB+" and tj.bitrate == 64 and tj.subchannel_id == 3 and tj.protection == "EEP 3-A"
    assert tj.decoding and tj.audio_format == "HE-AAC v2, 48 kHz Stereo @ 64 kbit/s" and tj.samplerate == 48000
    assert (tj.frame_errors, tj.rs_errors, tj.aac_errors) == (2, 17, 1)
    # not decoded: no "invalid"/empty codec leaks into the station list, no stale slide/DLS
    assert chill.sid == "100a" and chill.codec == "DAB+" and not chill.decoding and chill.audio_format is None
    assert chill.dls is None and not chill.mot_lastchange


def test_parse_mux_sync_follows_fct0() -> None:
    # the label survives a lost signal; FIG 0/0 (CIF low part 0, every ~12 s in mode I) stops arriving
    assert parse_mux(UPSTREAM, now_ms=NOW_MS + 20_000).sync
    assert not parse_mux(UPSTREAM, now_ms=NOW_MS + 40_000).sync
    assert parse_mux(UPSTREAM, now_ms=NOW_MS - 9_000).sync  # a small backwards clock step is not a loss
    empty = {**UPSTREAM, "ensemble": {"label": {"label": ""}, "id": "0x0000"}, "services": []}
    assert not parse_mux(empty, now_ms=NOW_MS).sync


def test_decoding_needs_recent_audio() -> None:
    # welle keeps the last mode after decoding stops; only fresh audio counts as decoding
    later = parse_mux(UPSTREAM, now_ms=NOW_MS + 30_000)
    assert not later.services[0].decoding and later.services[0].audio_format


def test_welle_sid_and_floats() -> None:
    # welle-cli matches "0x1002", else parses the id as a *decimal* number
    assert welle_sid("1002") == "0x1002" and welle_sid("0x100A") == "0x100a" and welle_sid("e1c00098") == "0xe1c00098"
    import struct

    assert floats(struct.pack("<3f", 1.0, -2.5, 0.25)) == [1.0, -2.5, 0.25] and floats(b"\x00") == []


def test_welle_args_sanitised() -> None:
    assert welle_args(["-w", "8000"], None) == (["-w", "8000"], [])
    args, notes = welle_args(["-w", "8000", "-C", "-P"], None)  # the old default: -P was eaten as the count
    assert args == ["-w", "8000"] and len(notes) == 2
    args, notes = welle_args(["-w", "8000", "-C"], None)  # trailing -C: welle-cli exits with "Unknown option"
    assert args == ["-w", "8000"] and notes
    assert welle_args(["-w", "8000", "-C", "1", "-P"], None) == (["-w", "8000", "-C", "1", "-P"], [])
    args, notes = welle_args(["-w", "8000", "-g", "5"], 29.7)
    assert args == ["-w", "8000", "-g", str(R82XX_GAINS_DB.index(29.7))] and notes
    assert welle_args(["-w", "8000"], -1)[0] == ["-w", "8000"]  # negative = AGC, as welle-cli's own -g -1


def test_gain_index_nearest_step() -> None:
    # -g is an index into the tuner's gain table, not dB: 29.7 dB is step 16 on R820T/R828D
    assert gain_index(29.7) == 16 and gain_index(0) == 0 and gain_index(100) == len(R82XX_GAINS_DB) - 1
    assert R82XX_GAINS_DB[gain_index(30.5)] == 29.7


def test_parse_mux_real_shape() -> None:
    j = {
        "ensemble": {"label": "ABC Sydney", "shortlabel": "ABC", "id": "1001"},
        "services": [
            {
                "sid": "1002", "label": "triple j ", "shortlabel": "triplej", "ptystring": "Rock", "url_mp3": "/mp3/1002",
                "components": [{"subchannel": {"id": 3, "bitrate": 64}, "ascty": "DAB+"}],
                "audiolevel": {"left": -12, "right": -14}, "mode": "DAB+",
                "dls": {"label": "Now playing", "time": 1}, "mot": {"lastchange": 99, "name": "slide.jpg"},
            }
        ],
        "demodulator": {"snr": 15.2},
        "utctime": {"year": 2026, "month": 1, "day": 2, "hour": 3, "minutes": 4},
    }
    m = parse_mux(j)
    assert m.sync and m.ensemble_label == "ABC Sydney" and m.snr == 15.2
    s = m.services[0]
    assert s.sid == "1002" and s.label == "triple j" and s.bitrate == 64 and s.codec == "DAB+"
    assert s.dls == "Now playing" and s.mot_lastchange == 99 and s.audio_level == -12
    assert m.utc_time["hour"] == 3


def test_parse_mux_no_sync() -> None:
    m = parse_mux({"ensemble": {"label": "", "id": ""}, "services": [], "demodulator": {"snr": 0}})
    assert not m.sync and m.services == []
    m2 = parse_mux({"ensemble": {"label": "X"}, "services": [], "demodulator": {"snr": 9, "sync": False}})
    assert not m2.sync  # explicit flag wins


def test_scan_order_priority_first_and_unique() -> None:
    allc = [f"{n}{c}" for n in range(5, 14) for c in "ABCD"] + ["13E", "13F"]
    order = scan_order(allc, ["9A", "9B", "9C", "8A", "8B", "8C", "8D", "zz"])
    assert order[:7] == ["9A", "9B", "9C", "8A", "8B", "8C", "8D"]
    assert len(order) == len(set(order)) == len(allc)
    assert "ZZ" not in order


def test_monogram() -> None:
    assert monogram_letters("triple j") == "TJ"
    assert monogram_letters("ABC Radio Sydney") == "AR"
    assert monogram_letters("Nova") == "NO"
    svg = monogram_svg("1002", "triple j")
    assert svg.startswith("<svg") and ">TJ<" in svg
    assert service_colour("1002") == service_colour("1002") and service_colour("1002") != service_colour("1003")
    assert monogram_letters("2GB & Co") == "2&"


class _SlowLockClient:
    """9C has a weak ensemble that shows SNR at once but only decodes after a few polls; 9A is empty."""

    def __init__(self, polls_to_lock: int) -> None:
        self.ch = ""
        self.polls = 0
        self.polls_to_lock = polls_to_lock

    async def set_channel(self, ch: str) -> bool:
        self.ch, self.polls = ch, 0
        return True

    async def mux(self) -> MuxInfo:
        self.polls += 1
        if self.ch != "9C":
            return MuxInfo(snr=0.0)
        if self.polls < self.polls_to_lock:
            return MuxInfo(snr=8.0)
        return MuxInfo(ensemble_label="CA ABC&SBS RADIO", snr=11.0, sync=True, services=[ServiceInfo(sid="1001", label="ABC")])


async def test_scan_waits_longer_on_a_channel_with_signal() -> None:
    found = await scan(_SlowLockClient(polls_to_lock=4), ["9A", "9C"], dwell_s=0.6, signal_wait_s=4.0)  # type: ignore[arg-type]
    assert list(found) == ["9C"]
    assert found["9C"].ensemble_label == "CA ABC&SBS RADIO"


async def test_scan_gives_up_at_dwell_without_signal_wait() -> None:
    found = await scan(_SlowLockClient(polls_to_lock=4), ["9C"], dwell_s=0.6)  # type: ignore[arg-type]
    assert found == {}
