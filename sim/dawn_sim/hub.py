"""Sim hub: one HTTP server the core's sim backends talk to, plus a small
control panel for a human (sliders for lux, toggles for GPS/DAB/network, input
buttons). Port 8099 by default."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from .simstate import STATE

PANEL = """<!doctype html><html><head><meta charset=utf-8><title>Dawn sim hub</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
body{font:14px system-ui;background:#111;color:#eee;margin:0;padding:16px;max-width:720px}
h1{font-size:18px;margin:0 0 12px}section{background:#1b1b1f;border-radius:10px;padding:12px;margin-bottom:12px}
label{display:block;margin:6px 0}input[type=range]{width:100%}button{background:#2d6cdf;color:#fff;border:0;border-radius:8px;padding:8px 12px;margin:3px;cursor:pointer}
button.alt{background:#444}.row{display:flex;flex-wrap:wrap;gap:4px}pre{background:#000;padding:8px;border-radius:8px;max-height:200px;overflow:auto;font-size:12px}
.on{color:#6f6}.off{color:#f66}
</style></head><body>
<h1>Dawn simulator hub</h1>
<section><b>Light sensor</b> <span id=luxv></span><br>
<input id=lux type=range min=0 max=4 step=0.01 oninput="setLux(this.value)">
<div class=row>
<button class=alt onclick="lux(0.5)">night 0.5</button><button class=alt onclick="lux(3)">dim 3</button><button class=alt onclick="lux(30)">lamp 30</button><button class=alt onclick="lux(150)">room 150</button><button class=alt onclick="lux(800)">bright 800</button><button class=alt onclick="lux(5000)">daylight 5000</button>
</div>
<div>Backlight applied by core: <b id=bl></b></div></section>
<section><b>Inputs</b><div class=row>
<button onclick="inp('encoder_ccw')">vol −</button><button onclick="inp('encoder_cw')">vol +</button>
<button onclick="inp('encoder_push')">encoder push</button><button onclick="inp('encoder_long')">encoder hold</button>
<button onclick="inp('button_short')">big button</button><button onclick="inp('button_long')">big button hold 3s</button>
<button onclick="inp('touch')">touch face</button></div></section>
<section><b>Phones</b><div class=row>
<button onclick="fetch('/airplay/start',{method:'POST'})">iPhone: AirPlay start</button><button class=alt onclick="fetch('/airplay/stop',{method:'POST'})">AirPlay stop</button>
<button onclick="fetch('/bluetooth/play',{method:'POST',headers:{'content-type':'application/json'},body:'{}'})">Android: Bluetooth play</button><button class=alt onclick="fetch('/bluetooth/stop',{method:'POST',headers:{'content-type':'application/json'},body:'{}'})">Bluetooth stop</button>
</div></section>
<section><b>Toggles</b><div class=row>
<button class=alt onclick="tog('gps_fix')">GPS fix: <span id=gps_fix></span></button>
<button class=alt onclick="tog('dab_sync')">DAB sync: <span id=dab_sync></span></button>
<button class=alt onclick="tog('sdr_present')">SDR plugged: <span id=sdr_present></span></button>
<button class=alt onclick="tog('network_online')">Network: <span id=network_online></span></button>
<button class=alt onclick="tog('audio_flowing')">Audio flowing: <span id=audio_flowing></span></button>
</div>
<label>chrony active source: <select id=chrony onchange="set({chrony_active:this.value})"><option>GPS</option><option>DAB</option><option>NTP</option><option>none</option></select></label>
</section>
<section><b>Log</b><pre id=log></pre></section>
<script>
const log2=v=>Math.log10(v+1);
async function set(p){await fetch('/set',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(p)});refresh()}
function lux(v){set({lux:v})}
function setLux(sl){lux(Math.round((Math.pow(10,sl)-1)*10)/10)}
async function inp(ev){await fetch('/input',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({event:ev})})}
let S={};async function tog(k){await set({[k]:!S[k]})}
async function refresh(){S=await (await fetch('/state')).json();
document.getElementById('luxv').textContent=S.lux+' lx';document.getElementById('lux').value=log2(S.lux);
document.getElementById('bl').textContent=S.backlight+' %';
for(const k of ['gps_fix','dab_sync','sdr_present','network_online','audio_flowing']){const e=document.getElementById(k);e.textContent=S[k]?'on':'off';e.className=S[k]?'on':'off'}
document.getElementById('chrony').value=S.chrony_active;
document.getElementById('log').textContent=(await (await fetch('/log')).json()).join('\\n')}
refresh();setInterval(refresh,1500);
</script></body></html>"""


def create_app() -> FastAPI:
    app = FastAPI(title="dawn sim hub")

    @app.get("/", response_class=HTMLResponse)
    async def panel() -> str:
        return PANEL

    @app.get("/state")
    async def state() -> dict[str, Any]:
        return STATE.as_dict()

    @app.get("/log")
    async def log() -> list[str]:
        return STATE.log[-60:]

    @app.post("/set")
    async def set_state(patch: dict[str, Any]) -> dict[str, Any]:
        for k, v in patch.items():
            if hasattr(STATE, k) and k not in ("input_queue", "log"):
                setattr(STATE, k, v)
                STATE.note(f"set {k}={v}")
        return STATE.as_dict()

    @app.get("/lux")
    async def lux() -> dict[str, float]:
        return {"lux": STATE.lux}

    @app.post("/backlight")
    async def backlight(body: dict[str, Any]) -> dict[str, Any]:
        pct = int(body.get("percent", 0))
        if pct != STATE.backlight:
            STATE.backlight = pct
            STATE.note(f"backlight -> {pct}%" + (f" ({body.get('driver')})" if body.get("driver") else ""))
        return {"ok": True}

    @app.post("/input")
    async def input_event(body: dict[str, Any]) -> dict[str, Any]:
        ev = {"event": body.get("event", ""), "value": body.get("value")}
        await STATE.input_queue.put(ev)
        STATE.note(f"input {ev['event']}")
        return {"ok": True}

    @app.get("/inputs/poll")
    async def poll(timeout: float = 20.0) -> JSONResponse:
        """Long-poll for the next input event (core's sim input backend)."""
        try:
            ev = await asyncio.wait_for(STATE.input_queue.get(), timeout=timeout)
            return JSONResponse(ev)
        except TimeoutError:
            return JSONResponse({"event": None})

    @app.get("/chrony/sources")
    async def chrony_sources() -> PlainTextResponse:
        """Fake `chronyc -c sources` output."""
        lines = []

        def row(mode: str, name: str, live: bool, selected: bool, offset: float) -> str:
            state = "*" if selected and live else ("+" if live else "?")
            reach = 377 if live else 0
            lastrx = 8 if live else 1800
            return f"{mode},{state},{name},{0 if mode == '#' else 2},4,{reach},{lastrx},{offset:.9f},{offset:.9f},0.000100000"

        gps_live = STATE.gps_fix
        dab_live = STATE.sdr_present and STATE.dab_sync
        ntp_live = STATE.network_online
        act = STATE.chrony_active
        if act == "GPS" and not gps_live:
            act = "DAB" if dab_live else ("NTP" if ntp_live else "none")
        if act == "DAB" and not dab_live:
            act = "NTP" if ntp_live else "none"
        lines.append(row("#", "GPS", gps_live, act == "GPS", 0.0000031))
        lines.append(row("#", "DAB", dab_live, act == "DAB", -0.012))
        lines.append(row("^", "time.cloudflare.com", ntp_live, act == "NTP", 0.0021))
        lines.append(row("^", "ntp.example.au", ntp_live, False, 0.0034))
        return PlainTextResponse("\n".join(lines) + "\n")

    @app.get("/chrony/tracking")
    async def chrony_tracking() -> PlainTextResponse:
        act = STATE.chrony_active
        refid = {"GPS": "47505300,GPS", "DAB": "44414200,DAB", "NTP": "A29FC87B,time.cloudflare.com"}.get(act, "7F7F0101,")
        stratum = 1 if act in ("GPS", "DAB") else (2 if act == "NTP" else 0)
        return PlainTextResponse(f"{refid},{stratum},1700000000.0,0.000000400,0.000000300,0.000001,1.2,0.001,0.5,0.0001,0.0005,0.0010,Normal\n")

    @app.get("/audio/flowing")
    async def audio_flowing() -> dict[str, bool]:
        return {"flowing": STATE.audio_flowing and STATE.sdr_present and STATE.dab_sync}

    @app.get("/network")
    async def network() -> dict[str, Any]:
        return {"online": STATE.network_online, "ip": "192.168.1.42" if STATE.network_online else None, "ssid": "HomeWiFi" if STATE.network_online else None}

    @app.get("/openmeteo")
    async def openmeteo(latitude: float = -33.87, longitude: float = 151.21, temperature_unit: str = "celsius", timezone: str = "Australia/Sydney") -> JSONResponse:
        """Canned Open-Meteo response so the laptop simulator needs no internet."""
        import datetime as _dt
        import zoneinfo

        if not STATE.network_online:
            return JSONResponse({"error": True, "reason": "offline (sim)"}, status_code=503)
        tz = zoneinfo.ZoneInfo(timezone)
        now = _dt.datetime.now(tz)
        hour = now.hour
        is_day = 1 if 6 <= hour < 18 else 0
        code = [0, 1, 2, 3, 61, 80, 95][now.day % 7]
        temp = 14 + 8 * max(0.0, 1 - abs(hour - 14) / 8)
        if temperature_unit == "fahrenheit":
            temp = temp * 9 / 5 + 32
        day = now.date().isoformat()
        return JSONResponse({
            "latitude": latitude, "longitude": longitude, "timezone": timezone,
            "current": {"time": now.strftime("%Y-%m-%dT%H:%M"), "temperature_2m": round(temp, 1), "weather_code": code, "is_day": is_day},
            "daily": {"time": [day], "temperature_2m_max": [round(temp + 3, 1)], "temperature_2m_min": [round(temp - 6, 1)], "sunrise": [f"{day}T05:32"], "sunset": [f"{day}T17:58"], "weather_code": [code]},
        })

    # ---- AirPlay simulation (emits shairport-sync metadata pipe items) ----
    def _item(typ: str, code: str, data: bytes | None = None) -> bytes:
        import base64 as _b64

        t, c = typ.encode().hex(), code.encode().hex()
        if data is None:
            return f"<item><type>{t}</type><code>{c}</code><length>0</length></item>\n".encode()
        return f"<item><type>{t}</type><code>{c}</code><length>{len(data)}</length>\n<data encoding=\"base64\">\n{_b64.b64encode(data).decode()}</data></item>\n".encode()

    def _artwork_png() -> bytes:
        from io import BytesIO

        from PIL import Image, ImageDraw

        img = Image.new("RGB", (300, 300), (40, 60, 110))
        d = ImageDraw.Draw(img)
        d.ellipse((60, 60, 240, 240), fill=(240, 180, 60))
        d.rectangle((0, 230, 300, 300), fill=(20, 20, 30))
        d.text((16, 250), "sim album art", fill=(230, 230, 230))
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @app.post("/airplay/start")
    async def airplay_start(body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or {}
        STATE.airplay_session = True
        STATE.airplay_playing = True
        for chunk in (
            _item("ssnc", "snam", body.get("client", "Sam's iPhone").encode()),
            _item("ssnc", "pbeg"),
            _item("ssnc", "mdst"),
            _item("core", "minm", body.get("title", "Golden Hour").encode()),
            _item("core", "asar", body.get("artist", "Kacey Musgraves").encode()),
            _item("core", "asal", body.get("album", "Golden Hour").encode()),
            _item("core", "astm", int(body.get("duration_s", 224) * 1000).to_bytes(4, "big")),
            _item("ssnc", "mden"),
            _item("ssnc", "PICT", _artwork_png()),
            # progress: start/current/end RTP frames at 44.1 kHz (defaults to 1:42 into a 3:44 track)
            _item("ssnc", "prgr", f"{1_000_000}/{1_000_000 + int(body.get('position_s', 102) * 44100)}/{1_000_000 + int(body.get('duration_s', 224) * 44100)}".encode()),
            _item("ssnc", "pvol", b"-20.0,-144,0,-20"),
        ):
            await STATE.airplay_queue.put(chunk)
        STATE.note("airplay: session started")
        return {"ok": True}

    @app.post("/airplay/stop")
    async def airplay_stop() -> dict[str, Any]:
        STATE.airplay_session = False
        STATE.airplay_playing = False
        await STATE.airplay_queue.put(_item("ssnc", "pend"))
        STATE.note("airplay: session ended")
        return {"ok": True}

    @app.post("/airplay/remote")
    async def airplay_remote(body: dict[str, Any]) -> dict[str, Any]:
        cmd = body.get("command")
        STATE.note(f"airplay: remote {cmd} (sent to the phone)")
        if cmd == "Pause" and STATE.airplay_session:
            STATE.airplay_playing = False
            await STATE.airplay_queue.put(_item("ssnc", "pfls"))
        elif cmd == "Play" and STATE.airplay_session:
            STATE.airplay_playing = True
            await STATE.airplay_queue.put(_item("ssnc", "prsm"))
        return {"ok": True}

    @app.get("/airplay/pipe")
    async def airplay_pipe(timeout: float = 20.0) -> Response:
        try:
            chunk = await asyncio.wait_for(STATE.airplay_queue.get(), timeout=timeout)
        except TimeoutError:
            return Response(status_code=204)
        while not STATE.airplay_queue.empty():
            chunk += STATE.airplay_queue.get_nowait()
        return Response(chunk, media_type="application/octet-stream")

    # ---- Bluetooth simulation ----
    @app.get("/bluetooth/state")
    async def bt_state() -> dict[str, Any]:
        return {"discoverable": STATE.bt_discoverable, "alias": STATE.bt_alias, "devices": STATE.bt_devices, "connected": STATE.bt_connected,
                "player_status": STATE.bt_player_status, "transport_active": STATE.bt_player_status == "playing", "track": STATE.bt_track}

    @app.post("/bluetooth/{action}")
    async def bt_action(action: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or {}
        addr = body.get("address")
        dev = next((d for d in STATE.bt_devices if d["address"] == addr), None)
        if action == "alias":
            STATE.bt_alias = body.get("name", STATE.bt_alias)
        elif action == "discoverable":
            STATE.bt_discoverable = bool(body.get("on"))
        elif action == "pair":
            if dev is None:
                dev = {"address": addr, "name": body.get("name", "New phone"), "paired": False, "connected": False, "trusted": False, "icon": "phone", "rssi": -50}
                STATE.bt_devices.append(dev)
            dev.update(paired=True, trusted=True, connected=True)
            STATE.bt_connected = addr
        elif action == "connect" and dev:
            for d in STATE.bt_devices:
                d["connected"] = False
            dev["connected"] = True
            STATE.bt_connected = addr
        elif action == "disconnect" and dev:
            dev["connected"] = False
            STATE.bt_connected = None
            STATE.bt_player_status = None
            STATE.bt_track = {}
        elif action == "remove" and dev:
            STATE.bt_devices.remove(dev)
            if STATE.bt_connected == addr:
                STATE.bt_connected = None
        elif action == "player":
            cmd = body.get("command")
            if cmd == "Pause":
                STATE.bt_player_status = "paused"
            elif cmd == "Play" and STATE.bt_connected:
                STATE.bt_player_status = "playing"
        elif action == "play":  # panel: phone starts playing
            if not STATE.bt_connected and STATE.bt_devices:
                STATE.bt_devices[0]["connected"] = True
                STATE.bt_connected = STATE.bt_devices[0]["address"]
            STATE.bt_player_status = "playing"
            STATE.bt_track = {"Title": body.get("title", "Here Comes the Sun"), "Artist": body.get("artist", "The Beatles"), "Album": body.get("album", "Abbey Road"), "Duration": 185000}
        elif action == "stop":
            STATE.bt_player_status = "stopped"
            STATE.bt_track = {}
        STATE.note(f"bluetooth: {action} {addr or ''}")
        return {"ok": True}

    @app.post("/log")
    async def add_log(request: Request) -> dict[str, bool]:
        body = await request.body()
        try:
            msg = json.loads(body).get("msg", body.decode())
        except json.JSONDecodeError:
            msg = body.decode()
        STATE.note(msg)
        return {"ok": True}

    return app
