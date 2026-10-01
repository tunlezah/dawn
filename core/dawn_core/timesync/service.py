"""TimeSourceService: chrony status every 30 s, GPS position/fix, state publishing.

Alarms are evaluated against the system clock only (see AlarmService); this
service just reports which reference chrony is using and whether it is live.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from ..config import DawnConfig
from ..context import DawnContext
from ..services import Service
from ..state.ui import GpsInfo
from .chrony import active_kind, parse_sources, parse_tracking, to_time_sources
from .gps import GpsdClient, GpsFix, SerialNmeaClient

log = logging.getLogger("dawn.timesync")


class TimeSourceService(Service):
    name = "timesync"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self.gps: GpsdClient | SerialNmeaClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._last_pos: tuple[float, float] | None = None
        self._hub = httpx.AsyncClient(timeout=3.0)

    async def start(self) -> None:
        await self._start_gps()
        self._task = asyncio.create_task(self._loop(), name="chrony-poll")

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
            self.gps = GpsdClient(g.gpsd_host, g.gpsd_port, self._on_fix)
        else:
            self.gps = SerialNmeaClient(g.serial_device, g.serial_baud, self._on_fix)
        await self.gps.start()
        log.info("gps source: %s", source)

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
            available=bool(self.gps and getattr(self.gps, "connected", False)), fix=fix.mode if fix.has_fix else (1 if fix.mode else 0),
            lat=fix.lat if fix.has_fix else None, lon=fix.lon if fix.has_fix else None, sats_used=fix.sats_used, sats_seen=fix.sats_seen,
            time=fix.time, device=fix.device,
        )
        self.ctx.store.touch()

    # ---- chrony ----------------------------------------------------------
    async def _loop(self) -> None:
        while True:
            try:
                await self.poll()
            except Exception:  # noqa: BLE001
                log.exception("chrony poll failed")
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
            out, _ = await asyncio.wait_for(proc.communicate(), 5)
            return out.decode(errors="ignore") if proc.returncode == 0 else None
        except (TimeoutError, OSError):
            return None

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
