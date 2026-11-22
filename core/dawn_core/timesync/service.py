"""TimeSourceService: chrony status every 30 s, GPS position/fix, state publishing.

Alarms are evaluated against the system clock only (see AlarmService); this
service just reports which reference chrony is using and whether it is live.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import datetime

import httpx

from ..config import DawnConfig
from ..context import DawnContext
from ..services import Service
from ..state.ui import GpsInfo
from ..tasks import spawn
from .chrony import active_kind, parse_sources, parse_tracking, to_time_sources
from .gps import GpsdClient, GpsFix, SerialNmeaClient
from .presence import PresenceInputs, gps_presence

log = logging.getLogger("dawn.timesync")

LAST_SEEN_KEY = "gps.last_seen_at"
PRESENCE_EVERY_S = 30.0
LAST_SEEN_WRITE_S = 60.0  # the KV row is refreshed at most this often while the receiver is there
SOURCE_RECHECK_S = 300.0  # source "auto" on the serial reader: look for gpsd again this often


class TimeSourceService(Service):
    name = "timesync"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self.gps: GpsdClient | SerialNmeaClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._last_pos: tuple[float, float] | None = None
        self._hub = httpx.AsyncClient(timeout=3.0)
        self.presence = "absent"
        self._presence_known = False  # the first evaluation after start is not a change worth an event
        self._presence_at: float | None = None
        self._presence_task: asyncio.Task[None] | None = None
        self._last_seen_written: float | None = None
        self._source_checked: float | None = None
        self._host = None

    async def start(self) -> None:
        await self._start_gps()
        with suppress(Exception):
            await self.check_presence()
        self._task = spawn(self._loop(), name="chrony-poll")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.gps:
            await self.gps.stop()
        await self._hub.aclose()

    async def on_config(self, old: DawnConfig, new: DawnConfig) -> None:
        if old.time_sources.gps != new.time_sources.gps:
            if self.gps:
                await self.gps.stop()
            await self._start_gps()

    # ---- GPS -------------------------------------------------------------
    async def _start_gps(self) -> None:
        g = self.ctx.config.time_sources.gps
        self.gps = None
        if not g.enabled or g.source == "none":
            self._publish_gps(GpsFix())
            return
        source = g.source
        if self.ctx.sim or source == "sim":
            source = "gpsd"  # the simulator speaks the gpsd protocol
        if source == "auto":
            source = "gpsd" if await _port_open(g.gpsd_host, g.gpsd_port) else "serial"
        if source == "gpsd":
            client = GpsdClient(g.gpsd_host, g.gpsd_port, self._on_fix)
            client.on_device = self._device_changed  # a receiver plugged in or pulled out: look again at once
            self.gps = client
        else:
            self.gps = SerialNmeaClient(g.serial_device, g.serial_baud, self._on_fix)
        await self.gps.start()
        log.info("gps source: %s", source)

    async def _reconsider_source(self, now: float) -> None:
        """`source: auto` is decided at start: gpsd if its port answers, otherwise the serial reader. gpsd can come up
        later (socket activation, a late start), and two readers must never share one tty, so while on the serial
        reader look for gpsd every few minutes and move over when it answers."""
        g = self.ctx.config.time_sources.gps
        if self.ctx.sim or g.source != "auto" or not isinstance(self.gps, SerialNmeaClient):
            return
        if self._source_checked is not None and now - self._source_checked < SOURCE_RECHECK_S:
            return
        self._source_checked = now
        if await _port_open(g.gpsd_host, g.gpsd_port):
            log.info("gpsd answers now; moving the GPS from the serial reader to gpsd")
            await self.gps.stop()
            await self._start_gps()

    # ---- is a receiver fitted? ---------------------------------------------
    def _device_changed(self) -> None:
        if self._presence_task is None or self._presence_task.done():
            self._presence_task = spawn(self.check_presence(), name="gps-presence")

    def _host_for_presence(self):  # noqa: ANN202
        if self._host is None:
            from ..diagnostics.host import make_host

            self._host = make_host(self.ctx)
        return self._host

    async def check_presence(self, now: float | None = None, wall: datetime | None = None) -> str:
        """Work out `presence` (see presence.py), publish it, and log a change as a `gps_presence` event."""
        g = self.ctx.config.time_sources.gps
        now = time.monotonic() if now is None else now
        wall = self.ctx.store.now() if wall is None else wall
        self._presence_at = now
        enabled = g.enabled and g.source != "none"
        usb = gps0 = device = False
        nmea_age = None
        if enabled:
            host = self._host_for_presence()
            try:
                usb = await host.gps_on_usb()
                gps0 = await host.exists("/dev/gps0")
            except Exception:  # noqa: BLE001
                log.debug("gps presence: host read failed", exc_info=True)
            c = self.gps
            if isinstance(c, GpsdClient):
                device = bool(c.connected and c.fix.activated)
            elif isinstance(c, SerialNmeaClient) and c.fix.last_msg_at:
                nmea_age = max(0.0, time.time() - c.fix.last_msg_at)
        last_seen = self._last_seen()
        presence = gps_presence(PresenceInputs(enabled, usb, gps0, device, nmea_age, last_seen), wall, g.forget_after_days)
        if presence == "present" and (self._last_seen_written is None or now - self._last_seen_written >= LAST_SEEN_WRITE_S):
            if self.ctx.db.try_set(LAST_SEEN_KEY, wall.isoformat(timespec="seconds")):
                self._last_seen_written = now
        if presence != self.presence:
            if self._presence_known:
                log.info("gps receiver: %s -> %s", self.presence, presence)
                try:
                    self.ctx.db.log_event("gps_presence", presence=presence, was=self.presence)
                except Exception:  # noqa: BLE001
                    log.debug("could not log gps_presence", exc_info=True)
            self.presence = presence
        self._presence_known = True
        self._publish_presence()
        return presence

    def _last_seen(self) -> datetime | None:
        try:
            raw = self.ctx.db.get(LAST_SEEN_KEY)
            return datetime.fromisoformat(raw) if raw else None
        except Exception:  # noqa: BLE001
            return None

    def forget_gps(self) -> None:
        """Diagnostics' "Forget GPS module": a receiver removed on purpose stops being "missing"."""
        self.ctx.db.try_set(LAST_SEEN_KEY, None)
        self._last_seen_written = None
        if self.presence == "missing":
            self.presence = "absent"
            self.ctx.db.log_event("gps_presence", presence="absent", was="missing", forgotten=True)
        self._publish_presence()

    def _publish_presence(self) -> None:
        st = self.ctx.store.state.time_sources.gps
        last = self._last_seen()
        st.presence = self.presence  # type: ignore[assignment]
        st.last_seen_at = last.isoformat(timespec="seconds") if last else None
        self.ctx.store.touch()

    def _on_fix(self, fix: GpsFix) -> None:
        self._publish_gps(fix)
        if fix.has_fix and fix.lat is not None and fix.lon is not None:
            pos = (round(fix.lat, 3), round(fix.lon, 3))
            if pos != self._last_pos:
                self._last_pos = pos
                self.ctx.refresh_settings_summary()

    def _publish_gps(self, fix: GpsFix) -> None:
        st = self.ctx.store.state.time_sources
        st.gps = GpsInfo(
            presence=self.presence, last_seen_at=st.gps.last_seen_at,  # type: ignore[arg-type]
            available=bool(self.gps and getattr(self.gps, "connected", False)), fix=fix.mode if fix.has_fix else (1 if fix.mode else 0),
            lat=fix.lat if fix.has_fix else None, lon=fix.lon if fix.has_fix else None, sats_used=fix.sats_used, sats_seen=fix.sats_seen,
            time=fix.time, device=fix.device, source=self.gps.kind if self.gps else "none", hdop=fix.hdop, snr_avg=fix.snr_summary()[0],
        )
        self.ctx.store.touch()

    def gps_detail(self) -> dict:
        """Everything the GPS client knows, for Diagnostics."""
        g = self.gps
        d = g.fix.as_dict() if g else GpsFix().as_dict()
        d.update(source=g.kind if g else "none", connected=bool(g and g.connected), connect_error=getattr(g, "connect_error", None),
                 enabled=self.ctx.config.time_sources.gps.enabled)
        return d

    # ---- chrony ----------------------------------------------------------
    async def _loop(self) -> None:
        while True:
            try:
                await self.poll()
            except Exception:  # noqa: BLE001
                log.exception("chrony poll failed")
            now = time.monotonic()
            try:
                if self._presence_at is None or now - self._presence_at >= (PRESENCE_EVERY_S if not self.ctx.sim else 5):
                    await self.check_presence(now)
                await self._reconsider_source(now)
            except Exception:  # noqa: BLE001
                log.exception("gps presence check failed")
            await asyncio.sleep(self.ctx.config.time_sources.chrony_poll_s if not self.ctx.sim else 5)

    async def _chronyc(self, *args: str) -> str | None:
        if self.ctx.sim:
            try:
                r = await self._hub.get(f"{self.ctx.config.sim.hub_url}/chrony/{args[-1]}")
                return r.text if r.status_code == 200 else None
            except httpx.HTTPError:
                return None
        try:
            proc = await asyncio.create_subprocess_exec(self.ctx.config.time_sources.chronyc_binary, "-c", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        except OSError:
            return None
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 5)
        except TimeoutError:
            with suppress(ProcessLookupError):
                proc.kill()  # a chronyc that hangs (its socket gone) is not left behind every poll
            return None
        return out.decode(errors="ignore") if proc.returncode == 0 else None

    async def poll(self) -> None:
        cfg = self.ctx.config.time_sources
        st = self.ctx.store.state.time_sources
        src_txt = await self._chronyc("sources")
        trk_txt = await self._chronyc("tracking")
        st.chrony_available = src_txt is not None
        if src_txt is None:
            st.sources = []
            st.active = "none"
            st.synced = False
            st.updated_at = self.ctx.store.iso()
            self.ctx.store.touch()
            return
        sources = parse_sources(src_txt)
        tracking = parse_tracking(trk_txt) if trk_txt else None
        st.sources = to_time_sources(sources, cfg.stale_after_s)
        st.active = active_kind(sources, tracking)  # type: ignore[assignment]
        st.synced = bool(tracking and tracking.synced)
        st.system_offset_ms = round(tracking.system_offset_s * 1000, 3) if tracking else None
        st.stratum = tracking.stratum if tracking else None
        st.dab_time_live = any(s.kind == "dab" and s.live for s in st.sources)
        st.updated_at = self.ctx.store.iso()
        self.ctx.store.touch()


async def _port_open(host: str, port: int) -> bool:
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, port), 1.5)
        w.close()
        return True
    except (TimeoutError, OSError):
        return False
