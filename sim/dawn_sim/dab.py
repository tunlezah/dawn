"""Fake welle-cli web interface.

Mirrors the HTTP surface Dawn relies on:
  GET  /mux.json        ensemble, services (with DLS/MOT info), demodulator SNR, utctime
  GET  /channel         current channel; POST /channel (body = "9A") retunes
  GET  /mp3/<sid>       endless audio stream (looped bundled mp3)
  GET  /slide/<sid>     latest MOT slide (PNG rendered with Pillow)
  GET  /fic             raw FIC stream carrying FIG 0/10 time
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from .fic import fic_frame
from .simstate import STATE

ASSETS = Path(__file__).parent / "assets"
TUNE_DELAY_S = 1.5

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

DLS_LINES = [
    "Now playing: {label} - {song}",
    "{label}: the morning show with Sam and Alex",
    "Text DAWN to 1999 for the song of the day",
    "Up next: news at the top of the hour",
    "{song} - {artist}",
]
SONGS = [("Golden Hour", "Kacey Musgraves"), ("Sunrise", "Norah Jones"), ("Here Comes the Sun", "The Beatles"),
         ("Morning Light", "Example Band"), ("Daybreak", "Dawn Chorus")]


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
    return STATE.sdr_present and STATE.dab_sync and STATE.channel in ENSEMBLES and (time.time() - STATE.tuned_at) > TUNE_DELAY_S


def _slide_n(sid: str) -> int:
    return int(time.time() // 20) + int(sid, 16) % 7


def _dls(sid: str, label: str) -> str:
    i = (int(time.time() // 12) + int(sid, 16)) % len(DLS_LINES)
    song, artist = SONGS[(int(time.time() // 12) + int(sid, 16)) % len(SONGS)]
    return DLS_LINES[i].format(label=label, song=song, artist=artist)


def mux_json() -> dict:
    now = datetime.now(timezone.utc)
    synced = _synced()
    ens = ENSEMBLES.get(STATE.channel)
    services = []
    if synced and ens:
        eid, elabel, svcs = ens
        for sid, label, short, br, has_slide in svcs:
            s = {
                "sid": sid,
                "label": label,
                "shortlabel": short,
                "pty": 10,
                "ptystring": "Pop Music",
                "language": 9,
                "languagestring": "English",
                "url_mp3": f"/mp3/{sid}",
                "components": [
                    {
                        "subchannel": {"id": int(sid, 16) % 60, "bitrate": br, "sad": 0, "protection": "EEP 3-A", "uep": False},
                        "transportmode": "audio",
                        "ascty": "DAB+",
                    }
                ],
                "audiolevel": {"left": -12 if STATE.audio_flowing else -90, "right": -12 if STATE.audio_flowing else -90},
                "channels": 2,
                "samplerate": 48000,
                "mode": "DAB+",
                "dls": {"label": _dls(sid, label), "time": int(time.time())},
                "errorcounters": {"frameerrors": 0, "rsuncorrectederrors": 0},
            }
            if has_slide:
                s["mot"] = {"time": int(time.time()), "lastchange": int(time.time() // 20 * 20), "name": f"slide{_slide_n(sid)}.png", "type": "image/png"}
            services.append(s)
        ensemble = {"label": elabel, "shortlabel": elabel[:8], "id": eid}
    else:
        ensemble = {"label": "", "shortlabel": "", "id": ""}
    j = {
        "ensemble": ensemble,
        "services": services,
        "demodulator": {"snr": 14.5 if synced else 0.0, "frequencycorrection": 120 if synced else 0, "time_last_frequency_correction": int(time.time()), "sync": synced},
        "receiver": {"hardware": {"name": "sim rtl-sdr", "gain": 29.7}, "software": {"name": "welle-cli (dawn sim)", "version": "sim"}},
        "channel": STATE.channel,
        "tii": [],
    }
    if synced:
        j["utctime"] = {"year": now.year, "month": now.month, "day": now.day, "hour": now.hour, "minutes": now.minute, "seconds": now.second, "lto": 0}
    return j


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
            STATE.note(f"welle retuned to {body}")
        return PlainTextResponse(STATE.channel)

    @app.get("/mp3/{sid}")
    async def mp3(sid: str) -> StreamingResponse:
        data = (ASSETS / "tone.mp3").read_bytes()

        async def gen():
            chunk = 2048
            # pace roughly at the file's bitrate so mpv behaves like a live stream
            per_chunk_s = 8.0 / (len(data) / chunk)
            while True:
                if not _synced() or not STATE.audio_flowing:
                    await asyncio.sleep(0.5)
                    continue
                for i in range(0, len(data), chunk):
                    yield data[i : i + chunk]
                    await asyncio.sleep(per_chunk_s)

        return StreamingResponse(gen(), media_type="audio/mpeg")

    @app.get("/slide/{sid}")
    async def slide(sid: str) -> Response:
        ens = ENSEMBLES.get(STATE.channel)
        if not ens:
            return Response(status_code=404)
        for s_sid, label, _short, _br, has_slide in ens[2]:
            if s_sid == sid.lower() and has_slide:
                return Response(render_slide(sid, label, _slide_n(sid)), media_type="image/png")
        return Response(status_code=404)

    @app.get("/fic")
    async def fic() -> StreamingResponse:
        async def gen():
            while True:
                if _synced():
                    yield fic_frame()
                await asyncio.sleep(0.096)

        return StreamingResponse(gen(), media_type="application/octet-stream")

    @app.get("/")
    async def index() -> PlainTextResponse:
        return PlainTextResponse("fake welle-cli (dawn sim)")

    return app
