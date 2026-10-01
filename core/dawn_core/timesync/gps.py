"""GPS position/time: gpsd JSON client (preferred) or direct NMEA over serial."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger("dawn.gps")


@dataclass
class GpsFix:
    mode: int = 0  # 0 unknown, 1 no fix, 2 2D, 3 3D
    lat: float | None = None
    lon: float | None = None
    alt: float | None = None
    time: str | None = None
    sats_used: int = 0
    sats_seen: int = 0
    device: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def has_fix(self) -> bool:
        return self.mode >= 2 and self.lat is not None and self.lon is not None


FixCb = Callable[[GpsFix], None]


class GpsdClient:
    def __init__(self, host: str, port: int, on_fix: FixCb):
        self.host, self.port, self.on_fix = host, port, on_fix
        self.fix = GpsFix()
        self._task: asyncio.Task[None] | None = None
        self.connected = False

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="gpsd-client")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        delay = 2
        while True:
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), 5)
                self.connected = True
                delay = 2
                writer.write(b'?WATCH={"enable":true,"json":true};\n')
                await writer.drain()
                while True:
                    line = await asyncio.wait_for(reader.readline(), 30)
                    if not line:
                        break
                    self._handle(line)
            except (TimeoutError, OSError) as e:
                log.debug("gpsd connection problem: %s", e)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("gpsd client error")
            self.connected = False
            if self.fix.mode != 0:
                self.fix = GpsFix()
                self.on_fix(self.fix)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    def _handle(self, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return
        cls = msg.get("class")
        if cls == "TPV":
            self.fix.mode = int(msg.get("mode", 0) or 0)
            self.fix.lat = msg.get("lat") if self.fix.mode >= 2 else None
            self.fix.lon = msg.get("lon") if self.fix.mode >= 2 else None
            self.fix.alt = msg.get("altHAE", msg.get("alt")) if self.fix.mode >= 3 else None
            self.fix.time = msg.get("time")
            self.fix.device = msg.get("device", self.fix.device)
            self.on_fix(self.fix)
        elif cls == "SKY":
            sats = msg.get("satellites") or []
            self.fix.sats_seen = len(sats)
            self.fix.sats_used = sum(1 for s in sats if s.get("used"))
            if "uSat" in msg:
                self.fix.sats_used = int(msg["uSat"])
            if "nSat" in msg:
                self.fix.sats_seen = int(msg["nSat"])
            self.on_fix(self.fix)


# ---- NMEA (serial fallback) -------------------------------------------------
def nmea_checksum_ok(line: str) -> bool:
    if not line.startswith("$") or "*" not in line:
        return False
    body, _, cs = line[1:].partition("*")
    c = 0
    for ch in body:
        c ^= ord(ch)
    try:
        return c == int(cs[:2], 16)
    except ValueError:
        return False


def _coord(v: str, hemi: str, deg_digits: int) -> float | None:
    if not v or len(v) <= deg_digits:
        return None
    try:
        deg = int(v[:deg_digits])
        minutes = float(v[deg_digits:])
    except ValueError:
        return None
    val = deg + minutes / 60
    return -val if hemi in ("S", "W") else val


def parse_nmea(line: str, fix: GpsFix) -> bool:
    """Update `fix` from one NMEA sentence; returns True when something changed."""
    line = line.strip()
    if not nmea_checksum_ok(line):
        return False
    body = line[1:].split("*")[0]
    f = body.split(",")
    talker_type = f[0][2:] if len(f[0]) >= 5 else f[0]
    if talker_type == "GGA" and len(f) >= 10:
        quality = int(f[6] or 0)
        fix.sats_used = int(f[7] or 0)
        if quality > 0:
            fix.lat = _coord(f[2], f[3], 2)
            fix.lon = _coord(f[4], f[5], 3)
            fix.alt = float(f[9]) if f[9] else None
            if fix.mode < 2:
                fix.mode = 2
        else:
            fix.mode = 1
            fix.lat = fix.lon = None
        return True
    if talker_type == "RMC" and len(f) >= 10:
        if f[2] == "A":
            fix.lat = _coord(f[3], f[4], 2)
            fix.lon = _coord(f[5], f[6], 3)
            if fix.mode < 2:
                fix.mode = 2
        else:
            fix.mode = 1
        if f[1] and f[9] and len(f[9]) == 6:
            t, d = f[1], f[9]
            fix.time = f"20{d[4:6]}-{d[2:4]}-{d[0:2]}T{t[0:2]}:{t[2:4]}:{t[4:6]}Z"
        return True
    if talker_type == "GSA" and len(f) >= 3:
        try:
            m = int(f[2] or 1)
        except ValueError:
            return False
        fix.mode = m if m >= 2 else min(fix.mode, 1) if fix.mode else 1
        return True
    if talker_type == "GSV" and len(f) >= 4:
        try:
            fix.sats_seen = int(f[3] or 0)
        except ValueError:
            return False
        return True
    return False


class SerialNmeaClient:
    def __init__(self, device: str, baud: int, on_fix: FixCb):
        self.device, self.baud, self.on_fix = device, baud, on_fix
        self.fix = GpsFix(device=device)
        self._task: asyncio.Task[None] | None = None
        self.connected = False

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="nmea-serial")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        try:
            import serial  # type: ignore
        except ImportError:
            log.warning("pyserial not installed; serial GPS disabled")
            return
        loop = asyncio.get_running_loop()
        while True:
            try:
                ser = await loop.run_in_executor(None, lambda: serial.Serial(self.device, self.baud, timeout=2))
                self.connected = True
                log.info("reading NMEA from %s @ %d", self.device, self.baud)
                while True:
                    raw = await loop.run_in_executor(None, ser.readline)
                    if not raw:
                        continue
                    if parse_nmea(raw.decode(errors="ignore"), self.fix):
                        self.on_fix(self.fix)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.debug("serial gps: %s", e)
                self.connected = False
                await asyncio.sleep(5)
