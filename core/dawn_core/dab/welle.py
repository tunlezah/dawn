"""welle-cli web interface client and mux.json normalisation.

Upstream welle-cli (the version install.sh builds) sends labels as objects
(`{"label": "ABC Sydney", "shortlabel": "ABC", "fig2label": ...}`), names the
sub-channel id `subchid`, reports `mode: "invalid"` for services it is not
decoding, has no sync flag and no seconds in `utctime`. Older builds and the
simulator used plain strings; both shapes are accepted.
"""

from __future__ import annotations

import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("dawn.dab.welle")

# FIG 0/0 with a CIF count low part of 0 (what welle stamps as time_last_fct0_frame) only comes round
# every ~12 s in transmission mode I; two missed cycles plus margin means the ensemble is gone.
FCT0_STALE_MS = 30_000

# welle-cli's -g takes an index into the tuner's gain table, not dB. R820T/R828D (RTL-SDR Blog V3/V4)
# table from librtlsdr, in dB.
R82XX_GAINS_DB = [0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7, 20.7, 22.9, 25.4, 28.0, 29.7,
                  32.8, 33.8, 36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9, 44.5, 48.0, 49.6]


# Band III channel centre frequencies (MHz), ETSI EN 300 401 (same table as web/src/shared/dab.ts)
CHANNEL_MHZ = {
    "5A": 174.928, "5B": 176.64, "5C": 178.352, "5D": 180.064, "6A": 181.936, "6B": 183.648, "6C": 185.36, "6D": 187.072,
    "7A": 188.928, "7B": 190.64, "7C": 192.352, "7D": 194.064, "8A": 196.928, "8B": 198.64, "8C": 200.352, "8D": 202.064,
    "9A": 204.928, "9B": 206.64, "9C": 208.352, "9D": 210.064, "10A": 211.648, "10B": 213.36, "10C": 215.072, "10D": 216.928,
    "11A": 218.64, "11B": 220.352, "11C": 222.064, "11D": 223.936, "12A": 225.648, "12B": 227.36, "12C": 229.072, "12D": 230.784,
    "13A": 232.496, "13B": 234.208, "13C": 235.776, "13D": 237.488, "13E": 239.2, "13F": 240.8,
}


def norm_sid(v: Any) -> str:
    """Normalise a service id to lowercase hex without 0x (4 digits for audio services)."""
    if v is None:
        return ""
    if isinstance(v, int):
        return f"{v:04x}"
    s = str(v).strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    try:
        return f"{int(s, 16):04x}"
    except ValueError:
        return s


def welle_sid(sid: str) -> str:
    """The id form welle-cli's /slide and /mp3 expect: "0x1002". Anything else is parsed as a *decimal*
    number (stoul), so "1002" asks for service 0x03ea and "c1d2" throws inside welle-cli."""
    return f"0x{norm_sid(sid)}"


def gain_index(db: float) -> int:
    """Nearest R82xx gain-table index for a gain in dB (what welle-cli's -g wants)."""
    return min(range(len(R82XX_GAINS_DB)), key=lambda i: abs(R82XX_GAINS_DB[i] - db))


def welle_args(args: list[str], gain_db: float | None) -> tuple[list[str], list[str]]:
    """The welle-cli arguments to run with, and what had to be corrected.

    `-C` needs a programme count: `-C -P` makes getopt eat `-P` as the count (carousel silently off) and a
    trailing `-C` makes welle-cli exit with "Unknown option", so a `-C` without a number is dropped."""
    out: list[str] = []
    notes: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-C" and (i + 1 >= len(args) or not args[i + 1].isdigit()):
            notes.append("dropped -C without a programme count (welle-cli needs e.g. -C 1)")
            i += 1
            continue
        if a == "-g":  # gain comes from dab.gain
            notes.append("ignored -g in dab.welle_args; set dab.gain instead")
            i += 2
            continue
        out.append(a)
        i += 1
    if "-P" in out and "-C" not in out:
        out.remove("-P")
        notes.append("dropped -P (it only applies with -C <count>)")
    if gain_db is not None and gain_db >= 0:  # welle-cli's own convention: negative = AGC
        out += ["-g", str(gain_index(gain_db))]
    return out, notes


@dataclass
class ServiceInfo:
    sid: str
    label: str
    short_label: str = ""
    bitrate: int | None = None
    codec: str | None = None  # "DAB+" / "DAB"
    pty: str | None = None
    url_mp3: str | None = None
    dls: str | None = None
    dls_time: int | None = None
    mot_lastchange: int | None = None
    mot_name: str | None = None
    audio_level: float | None = None  # max of L/R (welle reports raw sample peaks)
    audio_time: int | None = None  # epoch s of the last decoded audio
    subchannel_id: int | None = None
    protection: str | None = None
    samplerate: int | None = None
    decoding: bool = False  # welle is decoding this programme right now
    audio_format: str | None = None  # while decoding, e.g. "HE-AAC v2, 48 kHz Stereo @ 64 kbit/s"
    frame_errors: int | None = None  # cumulative while decoded: superframe sync failures
    rs_errors: int | None = None  # uncorrectable Reed-Solomon blocks
    aac_errors: int | None = None  # AAC decoder errors
    errors_time: int | None = None


@dataclass
class MuxInfo:
    ensemble_label: str = ""
    ensemble_id: str = ""
    ensemble_short: str = ""
    channel: str | None = None
    snr: float | None = None
    sync: bool = False
    services: list[ServiceInfo] = field(default_factory=list)
    utc_time: dict[str, Any] | None = None
    freq_correction_hz: float | None = None
    fic_crc_errors: int | None = None  # cumulative since welle-cli started
    last_fct0_ms: int | None = None
    gain_db: float | None = None
    hardware: str | None = None
    software: str | None = None
    tii: list[dict[str, Any]] = field(default_factory=list)  # transmitters: comb, pattern, delay_km, error
    cir_peaks: list[dict[str, Any]] = field(default_factory=list)  # channel impulse response peaks (dB)
    messages: list[str] = field(default_factory=list)  # welle's own log lines since the previous read
    raw: dict[str, Any] = field(default_factory=dict)


def _label(v: Any) -> tuple[str, str]:
    """(label, short label) from a DabLabel object or a plain string."""
    if isinstance(v, dict):
        return str(v.get("label") or "").strip(), str(v.get("shortlabel") or "").strip()
    return (str(v).strip() if v else ""), ""


def parse_mux(j: dict[str, Any], now_ms: int | None = None) -> MuxInfo:
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    ens = j.get("ensemble") or {}
    demod = j.get("demodulator") or {}
    recv = j.get("receiver") or {}
    hw, sw = recv.get("hardware") or {}, recv.get("software") or {}
    elabel, eshort = _label(ens.get("label"))
    fic = demod.get("fic") or {}
    info = MuxInfo(
        ensemble_label=elabel,
        ensemble_short=eshort or str(ens.get("shortlabel") or "").strip(),
        ensemble_id=norm_sid(ens.get("id")) if ens.get("id") not in (None, "") else "",
        channel=j.get("channel"),
        snr=_f(demod.get("snr")),
        utc_time=j.get("utctime"),
        freq_correction_hz=_f(demod.get("frequencycorrection")),
        fic_crc_errors=_i(fic.get("numcrcerrors")) if isinstance(fic, dict) else None,
        last_fct0_ms=_i(demod.get("time_last_fct0_frame")),
        gain_db=_f(hw.get("gain")),
        hardware=str(hw.get("name")) if hw.get("name") else None,
        software=" ".join(str(x) for x in (sw.get("name"), sw.get("version")) if x) or None,
        tii=[t for t in (j.get("tii") or []) if isinstance(t, dict)],
        cir_peaks=[p for p in (j.get("cir_peaks") or []) if isinstance(p, dict)],
        messages=[str(m) for m in (j.get("messages") or [])],
        raw=j,
    )
    for s in j.get("services") or []:
        comps = s.get("components") or []
        sub = (comps[0].get("subchannel") if comps else None) or {}
        asc = comps[0].get("ascty") if comps else None
        mode = s.get("mode")
        # ascty is the broadcast's declared type; `mode` is only meaningful while welle decodes the service
        codec = asc if asc in ("DAB", "DAB+") else (mode if mode in ("DAB", "DAB+") else None)
        al = s.get("audiolevel") or {}
        level = None
        if isinstance(al, dict) and al:
            vals = [v for v in (al.get("left"), al.get("right")) if isinstance(v, int | float)]
            level = max(vals) if vals else None
        mot = s.get("mot") or {}
        dls = s.get("dls") or {}
        errs = s.get("errorcounters") or {}
        label, short = _label(s.get("label"))
        # every service has a handler (audiolevel present, time 0 until decoded) and keeps its last mode after
        # decoding stops, so "decoding" means audio decoded in the last few seconds
        audio_time = _i(al.get("time")) if isinstance(al, dict) else None
        decoding = bool(audio_time) and mode not in (None, "", "invalid") and now_ms / 1000 - (audio_time or 0) < 5
        info.services.append(
            ServiceInfo(
                sid=norm_sid(s.get("sid")),
                label=label,
                short_label=short or str(s.get("shortlabel") or "").strip(),
                bitrate=_i(sub.get("bitrate")),
                codec=str(codec) if codec else None,
                pty=s.get("ptystring") or None,
                url_mp3=s.get("url_mp3"),
                dls=(str(dls.get("label")).strip() if isinstance(dls, dict) and dls.get("label") else (str(dls).strip() if isinstance(dls, str) and dls else None)),
                dls_time=_i(dls.get("time")) if isinstance(dls, dict) else None,
                mot_lastchange=_i(mot.get("lastchange") or mot.get("time")) if isinstance(mot, dict) else None,
                mot_name=mot.get("name") if isinstance(mot, dict) else None,
                audio_level=level,
                audio_time=audio_time,
                subchannel_id=_i(sub.get("subchid", sub.get("id"))),
                protection=str(sub.get("protection")) if sub.get("protection") else None,
                samplerate=_i(s.get("samplerate")) or None,
                decoding=decoding,
                audio_format=str(mode) if mode not in (None, "", "invalid", "DAB", "DAB+") else None,
                frame_errors=_i(errs.get("frameerrors")) if isinstance(errs, dict) else None,
                rs_errors=_i(errs.get("rserrors", errs.get("rsuncorrectederrors"))) if isinstance(errs, dict) else None,
                aac_errors=_i(errs.get("aacerrors")) if isinstance(errs, dict) else None,
                errors_time=_i(errs.get("time")) if isinstance(errs, dict) else None,
            )
        )
    # Sync: an explicit flag when the build has one; upstream has none, so "an ensemble was decoded and FIG 0/0
    # is still arriving" (the label survives a lost signal, the FCT0 time stops moving).
    has_ensemble = bool(info.ensemble_label) or bool(info.services)
    explicit = demod.get("sync")
    if isinstance(explicit, bool):
        info.sync = explicit and has_ensemble
    elif info.last_fct0_ms:
        age = now_ms - info.last_fct0_ms
        info.sync = has_ensemble and -5_000 < age < FCT0_STALE_MS  # small negative: a clock step
    else:
        info.sync = has_ensemble
    return info


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def floats(data: bytes) -> list[float]:
    """welle-cli's /spectrum, /impulseresponse and /constellation are raw native float32 arrays."""
    n = len(data) // 4
    return list(struct.unpack(f"<{n}f", data[: n * 4])) if n else []


class WelleClient:
    def __init__(self, base_url: str, timeout: float = 4.0):
        self.base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def mux(self) -> MuxInfo | None:
        try:
            r = await self._client.get(f"{self.base}/mux.json")
            if r.status_code != 200:
                return None
            return parse_mux(r.json())
        except (httpx.HTTPError, ValueError):
            return None

    async def reachable(self) -> bool:
        try:
            r = await self._client.get(f"{self.base}/mux.json")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def channel(self) -> str | None:
        try:
            r = await self._client.get(f"{self.base}/channel")
            return r.text.strip().upper() if r.status_code == 200 else None
        except httpx.HTTPError:
            return None

    async def set_channel(self, channel: str) -> bool:
        try:
            r = await self._client.post(f"{self.base}/channel", content=channel.upper())
            return r.status_code in (200, 204)
        except httpx.HTTPError:
            return False

    async def slide(self, sid: str) -> tuple[bytes, str] | None:
        try:
            r = await self._client.get(f"{self.base}/slide/{welle_sid(sid)}")
            if r.status_code != 200 or not r.content:
                return None
            return r.content, r.headers.get("content-type", "image/jpeg")
        except httpx.HTTPError:
            return None

    async def plot(self, name: str) -> list[float] | None:
        """One of welle's plot buffers: spectrum, nullspectrum, impulseresponse, constellation."""
        try:
            r = await self._client.get(f"{self.base}/{name}")
            return floats(r.content) if r.status_code == 200 else None
        except httpx.HTTPError:
            return None

    def stream_url(self, svc: ServiceInfo) -> str:
        path = svc.url_mp3 or f"/mp3/{welle_sid(svc.sid)}"
        return f"{self.base}{path}"
