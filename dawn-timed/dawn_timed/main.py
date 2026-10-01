"""dawn-timed: poll welle-cli for the DAB+ ensemble time and feed chrony SHM 2.

Strategy: read /mux.json `utctime` several times a second and emit one sample
at each second transition (edge detection), which gives better than 200 ms
accuracy from a field that only has one-second resolution. If `utctime` is
absent or has no seconds, parse FIG 0/10 from the /fic stream instead (long
form carries milliseconds). Samples are written only while the decoder reports
sync.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone

import httpx

from . import __version__
from .fic import parse_fibs
from .shm import ChronyShm

log = logging.getLogger("dawn-timed")


def utctime_from_mux(j: dict) -> tuple[datetime | None, bool]:
    """(datetime, has_seconds) from welle's utctime field; tolerant of field names."""
    u = j.get("utctime") or j.get("utc_time") or j.get("time")
    if not isinstance(u, dict):
        return None, False
    try:
        y, mo, d = int(u["year"]), int(u["month"]), int(u["day"])
        h = int(u.get("hour", u.get("hours", 0)))
        mi = int(u.get("minutes", u.get("minute", 0)))
        has_s = "seconds" in u or "second" in u
        s = int(u.get("seconds", u.get("second", 0)))
        ms = int(u.get("milliseconds", u.get("ms", 0)))
        if y < 2000:
            return None, False
        return datetime(y, mo, d, h, mi, min(s, 59), ms * 1000, tzinfo=timezone.utc), has_s
    except (KeyError, ValueError, TypeError):
        return None, False


def mux_synced(j: dict) -> bool:
    demod = j.get("demodulator") or {}
    flag = demod.get("sync")
    ens = (j.get("ensemble") or {}).get("label") or ""
    svcs = j.get("services") or []
    if isinstance(flag, bool):
        return flag and (bool(ens) or bool(svcs))
    return bool(ens) or bool(svcs)


class Timed:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.shm = ChronyShm(args.shm_unit, args.dry_run, args.time_t_bytes)
        self.client = httpx.AsyncClient(timeout=3.0)
        self.last_value: datetime | None = None
        self.last_status = 0.0
        self.synced = False
        self.mode = "mux"
        self.written = 0

    async def run(self) -> None:
        self.shm.attach()
        if self.args.fic_fallback == "on":
            self.mode = "fic"
        while True:
            try:
                if self.mode == "mux":
                    await self._poll_mux()
                else:
                    await self._read_fic()
            except Exception:
                log.exception("loop error")
                await asyncio.sleep(2)
            self._status()

    async def _mux(self) -> dict | None:
        try:
            r = await self.client.get(f"{self.args.welle_url}/mux.json")
            return r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    async def _poll_mux(self) -> None:
        period = 1.0 / self.args.poll_hz
        j = await self._mux()
        now = datetime.now(timezone.utc)
        if j is None:
            self.synced = False
            await asyncio.sleep(2)
            return
        self.synced = mux_synced(j)
        if not self.synced:
            self.last_value = None
            await asyncio.sleep(1)
            return
        value, has_s = utctime_from_mux(j)
        if value is None or not has_s:
            if self.args.fic_fallback != "off":
                log.warning("utctime %s in mux.json; switching to FIC parsing", "has no seconds" if value else "absent")
                self.mode = "fic"
                return
            await asyncio.sleep(1)
            return
        if self.last_value is not None and value != self.last_value:
            # edge: the broadcast second just ticked over -> `value` is the time right now
            self.shm.write(value, now, precision=-3)
            self.written += 1
        self.last_value = value
        await asyncio.sleep(period)

    async def _read_fic(self) -> None:
        try:
            async with self.client.stream("GET", f"{self.args.welle_url}/fic", timeout=httpx.Timeout(10.0, read=10.0)) as r:
                buf = b""
                last: datetime | None = None
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) < 32:
                        continue
                    usable = len(buf) - len(buf) % 32
                    times = parse_fibs(buf[:usable])
                    buf = buf[usable:]
                    for t, has_ms in times:
                        now = datetime.now(timezone.utc)
                        self.synced = True
                        if has_ms:
                            self.shm.write(t, now, precision=-6)
                            self.written += 1
                        elif last is not None and t != last:
                            self.shm.write(t, now, precision=-3)
                            self.written += 1
                        last = t
                    self._status()
        except (httpx.HTTPError, OSError) as e:
            self.synced = False
            log.debug("fic stream problem: %s", e)
            await asyncio.sleep(2)

    def _status(self) -> None:
        if time.monotonic() - self.last_status > 60:
            self.last_status = time.monotonic()
            log.info("status: mode=%s synced=%s samples_written=%d", self.mode, self.synced, self.written)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dawn-timed", description=__doc__.split("\n")[0])
    ap.add_argument("--welle-url", default="http://127.0.0.1:8000")
    ap.add_argument("--shm-unit", type=int, default=2)
    ap.add_argument("--poll-hz", type=float, default=5.0)
    ap.add_argument("--fic-fallback", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--time-t-bytes", choices=["auto", "4", "8"], default="auto")
    ap.add_argument("--dry-run", action="store_true", help="log samples instead of writing SHM")
    ap.add_argument("--log-level", default="INFO")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        asyncio.run(Timed(args).run())
    except KeyboardInterrupt:
        pass
    return 0
