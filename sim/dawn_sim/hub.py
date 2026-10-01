"""Sim hub: one HTTP server the core's sim backends talk to, plus a small
control panel for a human (sliders for lux, toggles for GPS/DAB/network, input
buttons). Port 8099 by default."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

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
        except asyncio.TimeoutError:
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
