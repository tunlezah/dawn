"""Fake welle-cli web interface.

Mirrors upstream welle-cli (welle.io master, src/welle-cli: webradiointerface.cpp, jsonconvert.cpp), which is
what install.sh builds, so core and dawn-timed run unchanged against it:
  GET  /mux.json        labels as {"label", "shortlabel", ...} objects, ids as "0x1002", no sync flag (the
                        demodulator has snr, frequencycorrection, time_last_fct0_frame and fic.numcrcerrors),
                        utctime without seconds, per-service audiolevel/errorcounters (time 0 until decoded),
                        tii, cir_peaks and the messages logged since the previous read
  GET  /channel         current channel; POST /channel (body = "9A") retunes
  GET  /mp3/<sid>       endless audio stream; decoding happens on demand while a client listens
  GET  /slide/<sid>     latest MOT slide (PNG rendered with Pillow)
  GET  /fic             raw FIC stream carrying FIG 0/10 time
  GET  /spectrum /nullspectrum /impulseresponse /constellation   native float32 arrays
<sid> is matched like upstream: "0x1002", else parsed as a *decimal* number (std::stoul).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import random
import struct
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from .fic import fic_frame
from .simstate import STATE

ASSETS = Path(__file__).parent / "assets"
TUNE_DELAY_S = 1.5
T_U = 2048  # mode I FFT size
K = 1536  # carriers
CONSTELLATION_POINTS = (76 - 1) * K // 96

# Canned ensembles: channel -> (eid, label, [(sid, label, short, bitrate, has_slide)])
ENSEMBLES: dict[str, tuple[str, str, list[tuple[str, str, str, int, bool]]]] = {
    "9A": (
        "1001",
        "ABC Sydney",
        [
            ("1001", "ABC Radio Sydney", "ABC SYD", 64, True),
            ("1002", "triple j", "triplej", 64, True),
            ("1003", "ABC Classic", "Classic", 80, True),
            ("1004", "ABC NewsRadio", "NewsRadio", 48, True),
            ("1005", "Double J", "DoubleJ", 64, True),
            ("1006", "ABC Jazz", "ABC Jazz", 48, False),
            ("1007", "ABC Country", "Country", 48, True),
            ("1008", "ABC Kids listen", "ABC Kids", 48, False),
            ("1009", "SBS Radio 1", "SBS 1", 64, True),
            ("100a", "SBS Chill", "SBSChill", 64, True),
            ("100b", "SBS PopDesi", "PopDesi", 48, False),
        ],
    ),
    "9B": (
        "2001",
        "Sydney DAB+ 1",
        [
            ("2001", "Nova 96.9", "Nova", 64, True),
            ("2002", "smoothfm 95.3", "smooth", 64, True),
            ("2003", "KIIS 1065", "KIIS", 64, True),
            ("2004", "WSFM 101.7", "WSFM", 64, True),
            ("2005", "2GB", "2GB", 48, True),
            ("2006", "Triple M Sydney", "TripleM", 64, True),
            ("2007", "2Day FM", "2DayFM", 64, False),
            ("2008", "Gold 104.3 Sydney", "Gold", 64, True),
            ("2009", "Hope 103.2", "Hope", 48, False),
            ("200a", "FBi Radio", "FBi", 48, False),
            ("200b", "2SER 107.3", "2SER", 48, False),
        ],
    ),
    "9C": (
        "3001",
        "Sydney DAB+ 2",
        [
            ("3001", "Classic Hits Plus", "Hits+", 48, True),
            ("3002", "Buddha Hits", "Buddha", 48, True),
            ("3003", "Chill Lounge", "Chill", 48, False),
            ("3004", "Nova Throwbacks", "Throwbck", 48, True),
            ("3005", "80s 90s Plus", "80s90s", 48, True),
            ("3006", "Easy Sydney", "Easy", 48, False),
        ],
    ),
}
# transmitters heard per channel: (comb, pattern, km); the first is the main site
TRANSMITTERS: dict[str, list[tuple[int, int, float]]] = {
    "9A": [(3, 7, 7.8), (11, 7, 21.6)],
    "9B": [(3, 9, 7.8), (11, 9, 21.6)],
    "9C": [(3, 12, 7.8)],
}

DLS_LINES = [
    "Now playing: {label} - {song}",
    "{label}: the morning show with Sam and Alex",
    "Text DAWN to 1999 for the song of the day",
    "Up next: news at the top of the hour",
    "{song} - {artist}",
]
SONGS = [("Golden Hour", "Kacey Musgraves"), ("Sunrise", "Norah Jones"), ("Here Comes the Sun", "The Beatles"),
         ("Morning Light", "Example Band"), ("Daybreak", "Dawn Chorus")]


class Decoder:
    """What welle-cli keeps per service and per receiver between reads."""

    def __init__(self) -> None:
        self.listeners: dict[str, int] = {}  # sid -> open /mp3 streams (decoding on demand)
        self.last_audio: dict[str, float] = {}  # sid -> last decoded audio (kept after decoding stops)
        self.errors: dict[str, list[float]] = {}  # sid -> [frame, rs, aac, time]
        self.fic_crc = 0.0
        self.fct0_ms = int(time.time() * 1000)
        self.messages: list[str] = []
        self.last_tick = time.time()
        self.last_sync = False
        self.utc: dict | None = None

    def log(self, level: str, text: str) -> None:
        # upstream formats ctime() (which ends in a newline) + "." + milliseconds
        now = time.time()
        self.messages.append(f"{time.ctime(now)}\n.{int(now * 1000) % 1000} {level}: {text}")
        del self.messages[:-100]

    def tick(self) -> None:
        """Advance counters by the time since the last read, according to the simulated SNR."""
        now = time.time()
        dt, self.last_tick = min(10.0, now - self.last_tick), now
        synced = _synced()
        if synced != self.last_sync:
            self.log("INFO ", f"{'Ensemble found' if synced else 'Signal lost'} on {STATE.channel}")
            self.last_sync = synced
        if not synced:
            return
        snr = STATE.dab_snr
        self.fct0_ms = int(now // 12 * 12 * 1000)  # FIG 0/0 with CIF low part 0: every 12 s in mode I
        self.fic_crc += max(0.0, 9 - snr) * 2.5 * dt
        for sid, n in self.listeners.items():
            if n <= 0:
                continue
            self.last_audio[sid] = now
            e = self.errors.setdefault(sid, [0.0, 0.0, 0.0, now])
            e[0] += max(0.0, 5 - snr) * 0.8 * dt
            e[1] += max(0.0, 8 - snr) * 1.5 * dt
            e[2] += max(0.0, 6 - snr) * 0.6 * dt
            e[3] = now


DEC = Decoder()


def colour_for(sid: str) -> tuple[int, int, int]:
    h = hashlib.sha1(sid.encode()).digest()
    hue = h[0] / 255
    # pastel-ish HSV -> RGB
    import colorsys

    r, g, b = colorsys.hsv_to_rgb(hue, 0.55, 0.75)
    return int(r * 255), int(g * 255), int(b * 255)


def render_slide(sid: str, label: str, n: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (320, 240), colour_for(sid))
    d = ImageDraw.Draw(img)
    try:
        font_big = ImageFont.truetype("DejaVuSans-Bold.ttf", 28)
        font_small = ImageFont.truetype("DejaVuSans.ttf", 16)
    except OSError:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()
    d.rectangle((0, 170, 320, 240), fill=(20, 20, 24))
    d.text((16, 20), label, fill=(255, 255, 255), font=font_big)
    d.text((16, 180), f"MOT slide #{n}", fill=(230, 230, 230), font=font_small)
    d.text((16, 205), time.strftime("%H:%M:%S"), fill=(180, 180, 190), font=font_small)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _synced() -> bool:
    return (STATE.sdr_present and STATE.dab_sync and STATE.channel in ENSEMBLES and STATE.dab_snr >= 3
            and (time.time() - STATE.tuned_at) > TUNE_DELAY_S)


def _slide_n(sid: str) -> int:
    return int(time.time() // 20) + int(sid, 16) % 7


def _dls(sid: str, label: str) -> str:
    i = (int(time.time() // 12) + int(sid, 16)) % len(DLS_LINES)
    song, artist = SONGS[(int(time.time() // 12) + int(sid, 16)) % len(SONGS)]
    return DLS_LINES[i].format(label=label, song=song, artist=artist)


def _label(label: str, short: str) -> dict:
    return {"label": label, "shortlabel": short, "fig2label": "", "fig2rfu": False, "fig2charset": "Undefined"}


def match_sid(stream: str, sid: str) -> bool | None:
    """Upstream: to_hex(id, 4) == stream, else (uint32_t)stoul(stream) == id. None where stoul would throw."""
    if stream.lower() == f"0x{sid}":
        return True
    digits = ""
    for ch in stream.strip():
        if not ch.isdigit():
            break
        digits += ch
    if not digits:
        return None
    return int(digits) == int(sid, 16)


def _find(stream: str) -> tuple[str, str, bool] | None:
    ens = ENSEMBLES.get(STATE.channel)
    if not ens:
        return None
    for sid, label, _short, _br, has_slide in ens[2]:
        m = match_sid(stream, sid)
        if m is None:
            raise ValueError(stream)
        if m:
            return sid, label, has_slide
    return None


def mux_json() -> dict:
    DEC.tick()
    now = time.time()
    synced = _synced()
    ens = ENSEMBLES.get(STATE.channel)
    services = []
    if synced and ens:
        eid, elabel, svcs = ens
        for sid, label, short, br, has_slide in svcs:
            heard = DEC.last_audio.get(sid)
            decoding = DEC.listeners.get(sid, 0) > 0
            e = DEC.errors.get(sid, [0, 0, 0, 0])
            s = {
                "sid": f"0x{sid}",
                "programType": 10,
                "ptystring": "Pop Music",
                "language": 9,
                "languagestring": "English",
                "label": _label(label, short),
                "url_mp3": f"/mp3/0x{sid}",
                "components": [
                    {
                        "componentnr": 0, "primary": True, "caflag": False, "transportmode": "audio", "label": _label("", ""),
                        "subchannel": {"subchid": int(sid, 16) % 60, "bitrate": br, "cu": br * 3 // 4, "sad": 0, "protection": "EEP 3-A", "language": 0, "languagestring": "Unknown language"},
                        "scid": None, "ascty": "DAB+", "dscty": None,
                    }
                ],
                "channels": 2,
                "samplerate": 48000 if heard else 0,
                "mode": f"HE-AAC v2, 48 kHz Stereo @ {br} kbit/s" if heard else "",
                # every service has a handler: level and time stay 0 until it has been decoded
                "audiolevel": {"time": int(heard) if heard else 0, "left": (11000 if STATE.audio_flowing else 0) if decoding else 0, "right": (10500 if STATE.audio_flowing else 0) if decoding else 0},
                "mot": {"time": int(now) if heard and has_slide else 0, "lastchange": int(now // 20 * 20) if heard and has_slide else 0},
                "dls": {"label": _dls(sid, label) if heard else "", "time": int(now) if heard else 0, "lastchange": int(now // 12 * 12) if heard else 0},
                "errorcounters": {"frameerrors": int(e[0]), "rserrors": int(e[1]), "aacerrors": int(e[2]), "time": int(e[3])},
                "xpaderror": {"haserror": False},
            }
            services.append(s)
        ensemble = {"label": _label(elabel, elabel[:8]), "id": f"0x{eid}", "ecc": "0xf0"}
    else:
        ensemble = {"label": _label("", ""), "id": "0x0000", "ecc": "0x00"}
    if synced:
        u = datetime.now(UTC)
        lto = (datetime.now().astimezone().utcoffset() or timedelta()).total_seconds() / 3600
        DEC.utc = {"year": u.year, "month": u.month, "day": u.day, "hour": u.hour, "minutes": u.minute, "lto": lto}
    snr = (STATE.dab_snr + random.uniform(-0.4, 0.4)) if synced else random.uniform(0.2, 1.8)
    j = {
        "receiver": {
            "hardware": {"name": "Rafael Micro R828D (sim)", "gain": 33.8},
            "software": {"name": "welle.io", "version": "v2.7-sim", "fftwindowplacement": "EarliestPeakWithBinning", "coarsecorrectorenabled": True, "freqsyncmethod": "GetMiddle", "lastchannelchange": int(STATE.tuned_at * 1000)},
        },
        "ensemble": ensemble,
        "services": services,
        "utctime": DEC.utc or {"year": 0, "month": 0, "day": 0, "hour": 0, "minutes": 0, "lto": 0},
        "messages": DEC.messages,
        "tii": [{"comb": c, "pattern": p, "delay": int(km / 0.015), "delay_km": km, "error": round(random.uniform(0.01, 0.15), 3)} for c, p, km in TRANSMITTERS.get(STATE.channel, [])] if synced else [],
        "cir_peaks": [{"index": 1024 + int(km / 0.29), "value": round(-3 - i * 14 + random.uniform(-1, 1), 1)} for i, (_c, _p, km) in enumerate(TRANSMITTERS.get(STATE.channel, []))] if synced else [],
        "demodulator": {
            "fic": {"numcrcerrors": int(DEC.fic_crc)},
            "time_last_fct0_frame": DEC.fct0_ms,
            "snr": round(snr, 2),
            "frequencycorrection": 142 + random.randint(-6, 6) if synced else 0,
        },
    }
    DEC.messages = []  # upstream drains pending messages on every read
    return j


def _pack(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def spectrum(null: bool = False) -> list[float]:
    """|FFT| of one symbol, FFT-shifted like welle: K carriers in the middle, noise either side."""
    synced = _synced()
    sig = 10 ** (STATE.dab_snr / 20) if synced and not null else 0.0
    lo, hi = (T_U - K) // 2, (T_U + K) // 2
    out = []
    for i in range(T_U):
        noise = abs(random.gauss(1.0, 0.25))
        out.append(noise + (sig * abs(random.gauss(1.0, 0.08)) if lo <= i < hi and i != T_U // 2 else 0.0))
    return out


def impulse_response() -> list[float]:
    """Channel impulse response in dB: a main peak plus one per further transmitter, on a noise floor."""
    cir = [-42 + random.uniform(-3, 3) for _ in range(T_U)]
    if _synced():
        for i, (_c, _p, km) in enumerate(TRANSMITTERS.get(STATE.channel, [])):
            at = 1024 + int(km / 0.29)
            for d in range(-3, 4):
                if 0 <= at + d < T_U:
                    cir[at + d] = max(cir[at + d], -3 - i * 14 - abs(d) * 6)
    return cir


def constellation() -> list[float]:
    """DQPSK phases in degrees; the spread grows as the SNR falls."""
    spread = 4 + max(0.0, 20 - STATE.dab_snr) * 2.4 if _synced() else 180
    return [random.choice((-135, -45, 45, 135)) + random.gauss(0, spread) for _ in range(CONSTELLATION_POINTS)]


def create_app() -> FastAPI:
    app = FastAPI(title="fake welle-cli")

    @app.middleware("http")
    async def _down(request: Request, call_next):
        if not STATE.sdr_present:
            return PlainTextResponse("no device", status_code=503)
        return await call_next(request)

    @app.get("/mux.json")
    async def mux() -> JSONResponse:
        return JSONResponse(mux_json())

    @app.get("/channel")
    async def get_channel() -> PlainTextResponse:
        return PlainTextResponse(STATE.channel)

    @app.post("/channel")
    async def set_channel(request: Request) -> PlainTextResponse:
        body = (await request.body()).decode().strip().strip('"').upper()
        if body and body != STATE.channel:
            STATE.channel = body
            STATE.tuned_at = time.time()
            DEC.listeners.clear()
            DEC.fct0_ms = int(time.time() * 1000)  # clearEnsemble() stamps it on retune
            DEC.log("INFO ", f"Tuning to {body}")
            STATE.note(f"welle retuned to {body}")
        return PlainTextResponse("Retuning...")

    @app.get("/mp3/{stream}")
    async def mp3(stream: str) -> Response:
        try:
            found = _find(stream)
        except ValueError:
            return PlainTextResponse(f"stoul('{stream}') throws in upstream welle-cli; use 0x-prefixed ids", status_code=500)
        if not found or not _synced():
            return PlainTextResponse("Could not understand request.", status_code=404)
        sid = found[0]
        data = (ASSETS / "tone.mp3").read_bytes()

        async def gen():
            chunk = 2048
            # pace roughly at the file's bitrate so mpv behaves like a live stream
            per_chunk_s = 8.0 / (len(data) / chunk)
            DEC.listeners[sid] = DEC.listeners.get(sid, 0) + 1
            try:
                while True:
                    if not _synced() or not STATE.audio_flowing:
                        await asyncio.sleep(0.5)
                        continue
                    for i in range(0, len(data), chunk):
                        yield data[i : i + chunk]
                        await asyncio.sleep(per_chunk_s)
            finally:
                DEC.listeners[sid] = max(0, DEC.listeners.get(sid, 1) - 1)

        return StreamingResponse(gen(), media_type="audio/mpeg")

    @app.get("/slide/{stream}")
    async def slide(stream: str) -> Response:
        try:
            found = _find(stream)
        except ValueError:
            return PlainTextResponse(f"stoul('{stream}') throws in upstream welle-cli; use 0x-prefixed ids", status_code=500)
        if not found or not found[2] or not DEC.last_audio.get(found[0]):
            return Response(b"404 Not Found\r\nSlide not available.\r\n", status_code=404)
        sid, label, _ = found
        return Response(render_slide(sid, label, _slide_n(sid)), media_type="image/png")

    @app.get("/fic")
    async def fic() -> StreamingResponse:
        async def gen():
            while True:
                if _synced():
                    yield fic_frame()
                await asyncio.sleep(0.096)

        return StreamingResponse(gen(), media_type="application/octet-stream")

    @app.get("/spectrum")
    async def get_spectrum() -> Response:
        return Response(_pack(spectrum()), media_type="application/octet-stream")

    @app.get("/nullspectrum")
    async def get_null_spectrum() -> Response:
        return Response(_pack(spectrum(null=True)), media_type="application/octet-stream")

    @app.get("/impulseresponse")
    async def get_cir() -> Response:
        return Response(_pack(impulse_response()), media_type="application/octet-stream")

    @app.get("/constellation")
    async def get_constellation() -> Response:
        if not _synced():
            return PlainTextResponse("Could not understand request.", status_code=404)
        return Response(_pack(constellation()), media_type="application/octet-stream")

    @app.get("/")
    async def index() -> PlainTextResponse:
        return PlainTextResponse("fake welle-cli (dawn sim)")

    return app

