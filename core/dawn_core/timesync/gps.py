"""GPS position/time: gpsd JSON client (preferred) or direct NMEA over serial.

Besides the fix, both keep what diagnostics need: every satellite in view with its
signal (C/N0, dBHz) and whether it is used, the dilutions of precision and error
estimates, the device and driver gpsd found, and (gpsd only, TOFF) how far the GPS
serial time sits from the system clock when it arrives.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

log = logging.getLogger("dawn.gps")

GNSS = {0: "GPS", 1: "SBAS", 2: "Galileo", 3: "BeiDou", 4: "IMES", 5: "QZSS", 6: "GLONASS", 7: "NavIC"}
# NMEA talker -> constellation, for the serial fallback
TALKER_GNSS = {"GP": "GPS", "GL": "GLONASS", "GA": "Galileo", "GB": "BeiDou", "BD": "BeiDou", "GQ": "QZSS", "GN": "GNSS"}


@dataclass
class Satellite:
    prn: int
    gnss: str = "GPS"
    el: float | None = None  # degrees above the horizon
    az: float | None = None  # degrees from north
    ss: float | None = None  # signal, C/N0 in dBHz (0 or None = not tracked)
    used: bool = False
    health: int | None = None  # gpsd: 0 unknown, 1 ok, 2 unhealthy


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
    status: int | None = None  # gpsd fix status (2 = DGPS ...)
    ept: float | None = None  # estimated time error, s
    eph: float | None = None  # horizontal position error, m
    epv: float | None = None
    hdop: float | None = None
    vdop: float | None = None
    pdop: float | None = None
    tdop: float | None = None
    satellites: list[Satellite] = field(default_factory=list)
    toff_ms: float | None = None  # GPS serial time minus system clock at receipt (gpsd TOFF)
    toff_at: float | None = None
    last_msg_at: float | None = None  # wall clock of the last report, any kind
    last_tpv_at: float | None = None  # ... of the last position report
    driver: str | None = None
    subtype: str | None = None
    bps: int | None = None
    activated: str | None = None
    gpsd_version: str | None = None
    error: str | None = None

    @property
    def has_fix(self) -> bool:
        return self.mode >= 2 and self.lat is not None and self.lon is not None

    def snr_summary(self) -> tuple[float | None, float | None]:
        """(mean C/N0 of the satellites in use, strongest C/N0 seen)."""
        used = [s.ss for s in self.satellites if s.used and s.ss]
        seen = [s.ss for s in self.satellites if s.ss]
        return (round(sum(used) / len(used), 1) if used else None, max(seen) if seen else None)

    def as_dict(self) -> dict:
        d = asdict(self)
        d.pop("extra", None)
        d["has_fix"] = self.has_fix
        d["snr_used_avg"], d["snr_max"] = self.snr_summary()
        return d


FixCb = Callable[[GpsFix], None]
STALE_FIX_S = 10  # u-blox 7 reports every second; this many seconds of silence and the fix is gone


def _num(v: object) -> float | None:
    return float(v) if isinstance(v, int | float) else None


class GpsdClient:
    kind = "gpsd"

    def __init__(self, host: str, port: int, on_fix: FixCb):
        self.host, self.port, self.on_fix = host, port, on_fix
        self.fix = GpsFix()
        self._task: asyncio.Task[None] | None = None
        self.connected = False
        self.connect_error: str | None = None

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
                self.connect_error = None
                delay = 2
                # pps: also send TOFF (GPS serial time vs the system clock) every cycle
                writer.write(b'?WATCH={"enable":true,"json":true,"pps":true};\n')
                await writer.drain()
                idle = 0.0
                while True:
                    try:
                        line = await asyncio.wait_for(reader.readline(), 5)
                    except TimeoutError:
                        self._expire()
                        idle += 5
                        if idle >= 30:
                            break  # gpsd itself went quiet: reconnect
                        continue
                    if not line:
                        break
                    idle = 0.0
                    self._handle(line)
                    self._expire()
            except (TimeoutError, OSError) as e:
                self.connect_error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                log.debug("gpsd connection problem: %s", e)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("gpsd client error")
            self.connected = False
            if self.fix.mode != 0:
                self.fix = GpsFix(gpsd_version=self.fix.gpsd_version)
                self.on_fix(self.fix)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    def _expire(self) -> None:
        """A fix is only as current as the last position report: an unplugged receiver just goes quiet."""
        f = self.fix
        if f.mode and (f.last_tpv_at is None or time.time() - f.last_tpv_at > STALE_FIX_S):
            f.mode, f.lat, f.lon, f.alt, f.sats_used = 0, None, None, None, 0
            f.satellites = []
            self.on_fix(f)

    def _handle(self, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return
        cls = msg.get("class")
        f = self.fix
        f.last_msg_at = time.time()
        if cls == "TPV":
            f.last_tpv_at = f.last_msg_at
            f.mode = int(msg.get("mode", 0) or 0)
            f.lat = msg.get("lat") if f.mode >= 2 else None
            f.lon = msg.get("lon") if f.mode >= 2 else None
            f.alt = msg.get("altHAE", msg.get("alt")) if f.mode >= 3 else None
            f.time = msg.get("time")
            f.device = msg.get("device", f.device)
            f.status = msg.get("status")
            f.ept, f.eph, f.epv = _num(msg.get("ept")), _num(msg.get("eph")), _num(msg.get("epv"))
            self.on_fix(f)
        elif cls == "SKY":
            sats = msg.get("satellites")
            if isinstance(sats, list):  # some SKY reports carry only DOPs
                f.satellites = [
                    Satellite(prn=int(s.get("PRN", 0)), gnss=GNSS.get(s.get("gnssid", 0), "GNSS"), el=_num(s.get("el")), az=_num(s.get("az")),
                              ss=_num(s.get("ss")), used=bool(s.get("used")), health=s.get("health"))
                    for s in sats if isinstance(s, dict)
                ]
                f.sats_seen = len(f.satellites)
                f.sats_used = sum(1 for s in f.satellites if s.used)
            if "uSat" in msg:
                f.sats_used = int(msg["uSat"])
            if "nSat" in msg:
                f.sats_seen = int(msg["nSat"])
            for k in ("hdop", "vdop", "pdop", "tdop"):
                if k in msg:
                    setattr(f, k, _num(msg[k]))
            self.on_fix(f)
        elif cls == "TOFF":
            try:
                real = int(msg["real_sec"]) + int(msg["real_nsec"]) / 1e9
                clock = int(msg["clock_sec"]) + int(msg["clock_nsec"]) / 1e9
                f.toff_ms, f.toff_at = round((real - clock) * 1000, 1), time.time()
            except (KeyError, TypeError, ValueError):
                pass
        elif cls in ("DEVICES", "DEVICE"):
            devs = msg.get("devices") if cls == "DEVICES" else [msg]
            if cls == "DEVICE" and msg.get("path") and not msg.get("activated"):  # gpsd lost the receiver
                f.driver = f.subtype = f.bps = f.activated = None
                f.last_tpv_at = None
                self._expire()
                return
            if cls == "DEVICES" and not devs:
                f.driver = f.subtype = f.bps = f.activated = None
            for d in devs or []:
                if isinstance(d, dict) and d.get("path"):
                    f.device = d.get("path")
                    f.driver = d.get("driver", f.driver)
                    f.subtype = d.get("subtype", f.subtype)
                    f.bps = d.get("bps", f.bps)
                    f.activated = d.get("activated", f.activated)
        elif cls == "VERSION":
            f.gpsd_version = msg.get("release")
        elif cls == "ERROR":
            f.error = str(msg.get("message") or "")


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


def _fnum(v: str) -> float | None:
    try:
        return float(v) if v else None
    except ValueError:
        return None


def parse_nmea(line: str, fix: GpsFix, gsv: dict[str, list[Satellite]] | None = None) -> bool:
    """Update `fix` from one NMEA sentence; returns True when something changed.

    `gsv` collects a GSV cycle per talker (satellites arrive over several sentences); pass the same dict
    every call to get per-satellite signal in `fix.satellites`."""
    line = line.strip()
    if not nmea_checksum_ok(line):
        return False
    body = line[1:].split("*")[0]
    f = body.split(",")
    talker = f[0][:2]
    talker_type = f[0][2:] if len(f[0]) >= 5 else f[0]
    fix.last_msg_at = time.time()
    if talker_type == "GGA" and len(f) >= 10:
        quality = int(f[6] or 0)
        fix.sats_used = int(f[7] or 0)
        fix.hdop = _fnum(f[8])
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
        if len(f) >= 18:
            used = {int(p) for p in f[3:15] if p.isdigit()}
            for s in fix.satellites:
                s.used = s.prn in used
            fix.pdop, fix.hdop, fix.vdop = _fnum(f[15]), _fnum(f[16]), _fnum(f[17])
        return True
    if talker_type == "GSV" and len(f) >= 4:
        try:
            total, num = int(f[1] or 1), int(f[2] or 1)
            fix.sats_seen = int(f[3] or 0)
        except ValueError:
            return False
        if gsv is not None:
            if num == 1:
                gsv[talker] = []
            cycle = gsv.setdefault(talker, [])
            used = {s.prn for s in fix.satellites if s.used}
            for i in range(4, len(f) - 3, 4):
                if f[i].isdigit():
                    prn = int(f[i])
                    cycle.append(Satellite(prn=prn, gnss=TALKER_GNSS.get(talker, "GNSS"), el=_fnum(f[i + 1]), az=_fnum(f[i + 2]),
                                           ss=_fnum(f[i + 3]), used=prn in used))
            if num == total:
                fix.satellites = [s for t, c in gsv.items() if t != talker for s in c] + cycle
        return True
    return False


class SerialNmeaClient:
    kind = "serial"

    def __init__(self, device: str, baud: int, on_fix: FixCb):
        self.device, self.baud, self.on_fix = device, baud, on_fix
        self.fix = GpsFix(device=device, bps=baud, driver="NMEA0183 (direct)")
        self._task: asyncio.Task[None] | None = None
        self._gsv: dict[str, list[Satellite]] = {}
        self.connected = False
        self.connect_error: str | None = None

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
            self.connect_error = "pyserial is not installed"
            return
        loop = asyncio.get_running_loop()
        while True:
            try:
                ser = await loop.run_in_executor(None, lambda: serial.Serial(self.device, self.baud, timeout=2))
                self.connected = True
                self.connect_error = None
                log.info("reading NMEA from %s @ %d", self.device, self.baud)
                while True:
                    raw = await loop.run_in_executor(None, ser.readline)
                    if not raw:
                        continue
                    if parse_nmea(raw.decode(errors="ignore"), self.fix, self._gsv):
                        self.on_fix(self.fix)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.debug("serial gps: %s", e)
                self.connect_error = str(e)
                self.connected = False
                await asyncio.sleep(5)
