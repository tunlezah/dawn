"""dawn-timed: poll welle-cli for the DAB+ ensemble time and feed chrony SHM 2.

Strategy: read /mux.json `utctime` several times a second and emit one sample
at each second transition (edge detection), which gives better than 200 ms
accuracy from a field that only has one-second resolution. If `utctime` is
absent or has no seconds (upstream welle-cli sends none), parse FIG 0/10 from
the /fic stream instead (long form carries milliseconds). Samples are written
only while the decoder reports sync.

What the daemon sees is written to a small JSON status file (atomically, every
couple of seconds) so dawn-core can show the DAB -> chrony path in the web UI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from . import __version__
from .fic import parse_fibs
from .shm import ChronyShm

log = logging.getLogger("dawn-timed")

# FIG 0/0 with CIF count low part 0 (welle's time_last_fct0_frame) arrives every ~12 s in mode I
FCT0_STALE_MS = 30_000


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
        return datetime(y, mo, d, h, mi, min(s, 59), ms * 1000, tzinfo=UTC), has_s
    except (KeyError, ValueError, TypeError):
        return None, False


def mux_synced(j: dict, now_ms: int | None = None) -> bool:
    """Same rule as core: an explicit flag if the build has one, else an ensemble whose FIG 0/0 is still arriving."""
    demod = j.get("demodulator") or {}
    flag = demod.get("sync")
    ens = (j.get("ensemble") or {}).get("label") or ""
    if isinstance(ens, dict):  # upstream welle-cli: {"label": ..., "shortlabel": ...}
        ens = ens.get("label") or ""
    has = bool(str(ens).strip()) or bool(j.get("services"))
    if isinstance(flag, bool):
        return flag and has
    fct0 = demod.get("time_last_fct0_frame")
    if isinstance(fct0, int | float) and fct0 > 0:
        age = (now_ms if now_ms is not None else int(time.time() * 1000)) - fct0
        return has and -5_000 < age < FCT0_STALE_MS
    return has


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="milliseconds") if dt else None


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
        # status file
        self.status_path = Path(args.status_file) if args.status_file else None
        self.started_at = datetime.now(UTC)
        self.welle_reachable: bool | None = None
        self.utctime_has_seconds: bool | None = None
        self.shm_attached = False
        self.shm_error: str | None = None
        self.last_sample_at: datetime | None = None
        self.last_dab_time: datetime | None = None
        self.last_offset_ms: float | None = None
        self.last_fig010_at: datetime | None = None
        self.last_fic_at: datetime | None = None
        self.fig010 = {"long": 0, "short": 0}
        self.fic_stats: dict[str, int] = {}
        self.last_error: str | None = None
        self._status_written = 0.0
        self._attach_tried = 0.0

    async def run(self) -> None:
        self._attach()
        if self.args.fic_fallback == "on":
            self.mode = "fic"
        while True:
            try:
                if not self.shm_attached and not self.args.dry_run and time.monotonic() - self._attach_tried > 60:
                    self._attach()
                if self.mode == "mux":
                    await self._poll_mux()
                else:
                    await self._read_fic()
            except Exception as e:
                log.exception("loop error")
                self.last_error = f"{type(e).__name__}: {e}"
                await asyncio.sleep(2)
            self._status()
            self._write_status()

    def _attach(self) -> None:
        """Attach SHM; on failure keep running (and retry) so the failure shows up in the status file."""
        self._attach_tried = time.monotonic()
        try:
            self.shm.attach()
            self.shm_attached = not self.args.dry_run
            self.shm_error = None
        except OSError as e:
            self.shm_attached = False
            self.shm_error = f"{e} (is chrony running with 'refclock SHM {self.args.shm_unit}:perm=0666'?)"
            log.error("cannot attach SHM unit %d: %s", self.args.shm_unit, e)

    def _sample(self, dab: datetime, now: datetime, precision: int) -> None:
        if self.shm_attached or self.args.dry_run:
            self.shm.write(dab, now, precision=precision)
            self.written += 1
        self.last_sample_at, self.last_dab_time = now, dab
        self.last_offset_ms = round((dab - now).total_seconds() * 1000, 1)

    async def _mux(self) -> dict | None:
        try:
            r = await self.client.get(f"{self.args.welle_url}/mux.json")
            self.welle_reachable = r.status_code == 200
            return r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            self.welle_reachable = False
            return None

    async def _poll_mux(self) -> None:
        period = 1.0 / self.args.poll_hz
        j = await self._mux()
        now = datetime.now(UTC)
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
        if value is not None:
            self.utctime_has_seconds = has_s
        if value is None or not has_s:
            if self.args.fic_fallback != "off":
                log.warning("utctime %s in mux.json; switching to FIC parsing", "has no seconds" if value else "absent")
                self.mode = "fic"
                return
            await asyncio.sleep(1)
            return
        if self.last_value is not None and value != self.last_value:
            # edge: the broadcast second just ticked over -> `value` is the time right now
            self._sample(value, now, precision=-3)
        self.last_value = value
        await asyncio.sleep(period)

    async def _read_fic(self) -> None:
        try:
            async with self.client.stream("GET", f"{self.args.welle_url}/fic", timeout=httpx.Timeout(10.0, read=10.0)) as r:
                self.welle_reachable = r.status_code == 200
                buf = b""
                last: datetime | None = None
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) < 32:
                        continue
                    usable = len(buf) - len(buf) % 32
                    times = parse_fibs(buf[:usable], stats=self.fic_stats)
                    buf = buf[usable:]
                    self.last_fic_at = datetime.now(UTC)
                    for t, has_ms in times:
                        now = datetime.now(UTC)
                        self.synced = True
                        self.last_fig010_at = now
                        self.fig010["long" if has_ms else "short"] += 1
                        if has_ms:
                            self._sample(t, now, precision=-6)
                        elif last is not None and t != last:
                            self._sample(t, now, precision=-3)
                        last = t
                    self._status()
                    self._write_status()
        except (httpx.HTTPError, OSError) as e:
            self.synced = False
            self.welle_reachable = False
            log.debug("fic stream problem: %s", e)
            await asyncio.sleep(2)

    def _status(self) -> None:
        if time.monotonic() - self.last_status > 60:
            self.last_status = time.monotonic()
            log.info("status: mode=%s synced=%s samples_written=%d", self.mode, self.synced, self.written)

    def status(self) -> dict[str, Any]:
        return {
            "version": __version__, "pid": os.getpid(), "started_at": _iso(self.started_at), "updated_at": _iso(datetime.now(UTC)),
            "welle_url": self.args.welle_url, "welle_reachable": self.welle_reachable, "mode": self.mode, "synced": self.synced,
            "utctime_has_seconds": self.utctime_has_seconds, "samples_written": self.written,
            "last_sample_at": _iso(self.last_sample_at), "last_dab_time": _iso(self.last_dab_time), "last_offset_ms": self.last_offset_ms,
            "last_fic_at": _iso(self.last_fic_at), "last_fig010_at": _iso(self.last_fig010_at),
            "fig010_long": self.fig010["long"], "fig010_short": self.fig010["short"],
            "fibs": self.fic_stats.get("fibs", 0), "fib_crc_errors": self.fic_stats.get("crc_bad", 0),
            "shm_unit": self.args.shm_unit, "shm_attached": self.shm_attached, "shm_error": self.shm_error,
            "dry_run": bool(self.args.dry_run), "last_error": self.last_error,
        }

    def _write_status(self, force: bool = False) -> None:
        if not self.status_path or (not force and time.monotonic() - self._status_written < 2):
            return
        self._status_written = time.monotonic()
        try:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.status_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.status()))
            tmp.replace(self.status_path)
        except OSError as e:
            log.debug("cannot write status file %s: %s", self.status_path, e)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dawn-timed", description=__doc__.split("\n")[0])
    ap.add_argument("--welle-url", default="http://127.0.0.1:8000")
    ap.add_argument("--shm-unit", type=int, default=2)
    ap.add_argument("--poll-hz", type=float, default=5.0)
    ap.add_argument("--fic-fallback", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--time-t-bytes", choices=["auto", "4", "8"], default="auto")
    ap.add_argument("--status-file", default="/run/dawn-timed/status.json", help="JSON status for dawn-core ('' = none)")
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
