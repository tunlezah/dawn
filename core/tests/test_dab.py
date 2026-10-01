from __future__ import annotations

from dawn_core.dab.logos import monogram_letters, monogram_svg, service_colour
from dawn_core.dab.scanner import scan_order
from dawn_core.dab.welle import norm_sid, parse_mux


def test_norm_sid_variants() -> None:
    assert norm_sid("1002") == "1002"
    assert norm_sid("0x1002") == "1002"
    assert norm_sid(0x1002) == "1002"
    assert norm_sid("100A") == "100a"
    assert norm_sid("E1C00098") == "e1c00098"


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
