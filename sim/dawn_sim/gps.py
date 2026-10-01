"""GPS simulator: 1 Hz NMEA to a pty (optional) and a fake gpsd JSON server."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone

from . import nmea
from .simstate import STATE


class GpsSim:
    def __init__(self, gpsd_port: int = 2947, pty: bool = False, host: str = "127.0.0.1"):
        self.gpsd_port = gpsd_port
        self.host = host
        self.pty = pty
        self.pty_path: str | None = None
        self._master_fd: int | None = None
        self._clients: set[asyncio.StreamWriter] = set()
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
        writer.write(json.dumps({"class": "VERSION", "release": "3.25-sim", "rev": "dawn", "proto_major": 3, "proto_minor": 15}).encode() + b"\n")
        await writer.drain()
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                txt = line.decode(errors="ignore").strip()
                if txt.startswith("?WATCH"):
                    enable = '"enable":true' in txt.replace(" ", "")
                    dev = {"class": "DEVICE", "path": "/dev/ttyACM0", "driver": "NMEA0183", "activated": _iso(), "bps": 9600}
                    writer.write(json.dumps({"class": "DEVICES", "devices": [dev]}).encode() + b"\n")
                    writer.write(json.dumps({"class": "WATCH", "enable": enable, "json": True, "nmea": False}).encode() + b"\n")
                    await writer.drain()
                    if enable:
                        self._clients.add(writer)
                    else:
                        self._clients.discard(writer)
                elif txt.startswith("?POLL"):
                    writer.write(json.dumps({"class": "POLL", "time": _iso(), "active": 1, "tpv": [self.tpv()], "sky": [self.sky()]}).encode() + b"\n")
                    await writer.drain()
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._clients.discard(writer)
            writer.close()

    def tpv(self) -> dict:
        d = {"class": "TPV", "device": "/dev/ttyACM0", "mode": 3 if STATE.gps_fix else 1, "time": _iso()}
        if STATE.gps_fix:
            d.update({"lat": STATE.gps_lat, "lon": STATE.gps_lon, "alt": 20.0, "altHAE": 39.6, "ept": 0.005, "epx": 3.0, "epy": 3.0, "speed": 0.0})
        return d

    def sky(self) -> dict:
        sats = []
        for prn in range(1, max(STATE.gps_sats, 4) + 1):
            sats.append({"PRN": prn, "el": 20 + (prn * 7) % 60, "az": (prn * 37) % 360, "ss": 30 + prn % 15, "used": STATE.gps_fix and prn <= STATE.gps_sats})
        return {"class": "SKY", "device": "/dev/ttyACM0", "satellites": sats, "hdop": 1.2 if STATE.gps_fix else 99.99}

    async def _tick(self) -> None:
        while True:
            now = datetime.now(timezone.utc)
            if self._master_fd is not None:
                data = nmea.burst(now, STATE.gps_lat, STATE.gps_lon, STATE.gps_fix, STATE.gps_sats)
                try:
                    os.write(self._master_fd, data.encode())
                except OSError:
                    pass
            dead = []
            for w in list(self._clients):
                try:
                    w.write(json.dumps(self.tpv()).encode() + b"\n")
                    if now.second % 5 == 0:
                        w.write(json.dumps(self.sky()).encode() + b"\n")
                    await w.drain()
                except (ConnectionResetError, OSError):
                    dead.append(w)
            for w in dead:
                self._clients.discard(w)
            await asyncio.sleep(1.0)


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
