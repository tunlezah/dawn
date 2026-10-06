"""GPS simulator: 1 Hz NMEA to a pty (optional) and a fake gpsd JSON server.

The fake gpsd answers like gpsd 3.25 with a u-blox 7: VERSION on connect, DEVICES/WATCH on ?WATCH, then TPV every
second, SKY (satellites with C/N0, DOPs) every few seconds and TOFF (GPS serial time vs the clock) when the watch
asks for "pps". The hub's `gps_signal` sets the strongest satellite's C/N0; the rest fall off from it.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from datetime import UTC, datetime

from . import nmea
from .simstate import STATE

ACTIVATED = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
NMEA_LATENCY_S = 0.14  # u-blox 7 at 9600 baud: the sentences arrive ~0.1-0.2 s after the second


def satellites() -> list[dict]:
    """GPS PRNs plus two SBAS; C/N0 falls off from the strongest; four or more strong ones make a fix."""
    out = []
    n = max(STATE.gps_sats, 4) + 3
    for i in range(n):
        prn = [5, 12, 13, 15, 18, 20, 23, 24, 25, 26, 29, 31, 10, 2, 7, 16][i % 16]
        base = max(0.0, STATE.gps_signal - i * 2.2) if i < n - 2 else 0.0  # the last two are in view but not tracked
        used = STATE.gps_fix and base >= 24 and i < STATE.gps_sats
        ss = max(0.0, base + random.uniform(-1, 1)) if base else 0.0
        out.append({"PRN": prn, "gnssid": 0, "svid": prn, "el": 15 + (prn * 7) % 70, "az": (prn * 37) % 360, "ss": round(ss), "used": used, "health": 1})
    out += [{"PRN": 133, "gnssid": 1, "svid": 133, "el": 30, "az": 300, "ss": round(max(0.0, STATE.gps_signal - 8)), "used": False, "health": 1},
            {"PRN": 137, "gnssid": 1, "svid": 137, "el": 12, "az": 60, "ss": 0, "used": False, "health": 1}]
    return out


def fix_ok() -> bool:
    return STATE.gps_present and STATE.gps_fix and sum(1 for s in satellites() if s["used"]) >= 4


class GpsSim:
    def __init__(self, gpsd_port: int = 2947, pty: bool = False, host: str = "127.0.0.1"):
        self.gpsd_port = gpsd_port
        self.host = host
        self.pty = pty
        self.pty_path: str | None = None
        self._master_fd: int | None = None
        self._clients: dict[asyncio.StreamWriter, bool] = {}  # writer -> wants TOFF ("pps")
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        if self.pty:
            m, s = os.openpty()
            self._master_fd = m
            self.pty_path = os.ttyname(s)
            STATE.note(f"GPS NMEA on pty {self.pty_path} (point gpsd at it with: gpsd -N {self.pty_path})")
        self._server = await asyncio.start_server(self._client, self.host, self.gpsd_port)
        STATE.note(f"fake gpsd listening on {self.host}:{self.gpsd_port}")
        asyncio.create_task(self._tick(), name="gps-tick")

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(json.dumps({"class": "VERSION", "release": "3.25-sim", "rev": "3.25", "proto_major": 3, "proto_minor": 15}).encode() + b"\n")
        await writer.drain()
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                txt = line.decode(errors="ignore").strip()
                if txt.startswith("?WATCH"):
                    flat = txt.replace(" ", "")
                    enable = '"enable":true' in flat
                    pps = '"pps":true' in flat
                    dev = {"class": "DEVICE", "path": "/dev/gps0", "driver": "u-blox", "subtype": "SW 1.00 (59842),HW 00070000",
                           "activated": ACTIVATED, "flags": 1, "native": 0, "bps": 9600, "parity": "N", "stopbits": 1, "cycle": 1.00}
                    writer.write(json.dumps({"class": "DEVICES", "devices": [dev] if STATE.gps_present else []}).encode() + b"\n")
                    writer.write(json.dumps({"class": "WATCH", "enable": enable, "json": True, "nmea": False, "raw": 0, "scaled": False, "timing": False, "split24": False, "pps": pps}).encode() + b"\n")
                    await writer.drain()
                    if enable:
                        self._clients[writer] = pps
                    else:
                        self._clients.pop(writer, None)
                elif txt.startswith("?POLL"):
                    writer.write(json.dumps({"class": "POLL", "time": _iso(), "active": 1, "tpv": [self.tpv()], "sky": [self.sky()]}).encode() + b"\n")
                    await writer.drain()
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._clients.pop(writer, None)
            writer.close()

    def tpv(self) -> dict:
        ok = fix_ok()
        d = {"class": "TPV", "device": "/dev/gps0", "mode": 3 if ok else 1, "time": _iso(), "ept": 0.005, "leapseconds": 18}
        if ok:
            d.update({"status": 1, "lat": STATE.gps_lat, "lon": STATE.gps_lon, "alt": 20.0, "altHAE": 39.6, "altMSL": 20.0,
                      "epx": 3.0, "epy": 3.4, "epv": 7.8, "eph": 4.6, "speed": 0.0})
        return d

    def sky(self) -> dict:
        sats = satellites()
        used = sum(1 for s in sats if s["used"])
        d = {"class": "SKY", "device": "/dev/gps0", "nSat": len(sats), "uSat": used, "satellites": sats}
        if used >= 4:
            d.update({"hdop": round(1.0 + 6 / used, 2), "vdop": round(1.4 + 8 / used, 2), "pdop": round(1.8 + 10 / used, 2), "tdop": round(0.9 + 4 / used, 2)})
        else:
            d.update({"hdop": 99.99, "pdop": 99.99})
        return d

    def toff(self) -> dict:
        now = time.time()
        sec = int(now)
        clock = sec + NMEA_LATENCY_S + random.uniform(-0.01, 0.01)
        return {"class": "TOFF", "device": "/dev/gps0", "real_sec": sec, "real_nsec": 0, "clock_sec": int(clock),
                "clock_nsec": int((clock % 1) * 1e9), "precision": -1, "shm": "NTP0", "qErr": 0}

    async def _tick(self) -> None:
        while True:
            now = datetime.now(UTC)
            if self._master_fd is not None:
                data = nmea.burst(now, STATE.gps_lat, STATE.gps_lon, STATE.gps_fix, STATE.gps_sats)
                try:
                    os.write(self._master_fd, data.encode())
                except OSError:
                    pass
            dead = []
            # receiver unplugged, or gpsd "failed" in the hub: nothing reaches the clients
            silent = not STATE.gps_present or "gpsd" in STATE.units_down
            for w, pps in ([] if silent else list(self._clients.items())):
                try:
                    w.write(json.dumps(self.tpv()).encode() + b"\n")
                    if now.second % 5 == 0:
                        w.write(json.dumps(self.sky()).encode() + b"\n")
                    if pps and fix_ok():
                        w.write(json.dumps(self.toff()).encode() + b"\n")
                    await w.drain()
                except (ConnectionResetError, OSError):
                    dead.append(w)
            for w in dead:
                self._clients.pop(w, None)
            await asyncio.sleep(1.0)


def _iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
