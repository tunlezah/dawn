"""System status service: uptime, CPU temp, load, memory, heartbeat, watchdog."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
import time
from pathlib import Path

from .. import __version__
from ..context import DawnContext
from ..services import Service
from . import hw as hwmod

log = logging.getLogger("dawn.sysinfo")
REPO_DIR = str(Path(__file__).resolve().parents[3])


def sd_notify(msg: str) -> None:
    """Minimal sd_notify(3) without a dependency."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(msg.encode())
    except OSError:
        pass


def alarms_alive(ctx: DawnContext) -> bool:
    """The alarm engine is ticking (True when there is no alarm service to ask)."""
    try:
        from ..alarms.service import AlarmService

        return ctx.svc(AlarmService).alive()
    except (ImportError, KeyError):
        return True


def git_rev(repo: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


# a stick that has just appeared: udev sets its permissions a moment after sysfs shows it
REPLUG_SETTLE_S = 2.0

class SysInfoService(Service):
    name = "sysinfo"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self._task: asyncio.Task[None] | None = None
        self.hw = hwmod.HardwareInfo()
        self.booted = time.time()
        self._stall_logged = False
        self._sdr_id: tuple[str, ...] | None = None
        self._sdr_restart: asyncio.Task | None = None

    async def start(self) -> None:
        cfg = self.ctx.config
        if self.ctx.sim:
            self.hw = hwmod.HardwareInfo(model="simulator", panel="hdmi", i2c_buses=[])
        else:
            self.hw = await asyncio.to_thread(hwmod.detect, cfg.display.panel, cfg.display.backlight.sysfs_path)
            self._sdr_id = hwmod.sdr_identity(await asyncio.to_thread(hwmod.sdr_usb))
        st = self.ctx.store.state.system
        st.model = self.hw.model
        st.panel = self.hw.panel
        st.sdr_present = self.hw.sdr_present
        st.sdr_tuner = self.hw.sdr_tuner
        st.hostname = socket.gethostname()
        st.version = __version__
        st.git_rev = await asyncio.to_thread(lambda: git_rev(cfg.system.update_repo_dir) or git_rev(REPO_DIR))
        st.booted_at = self.ctx.store.iso()
        self.ctx.store.state.dab.sdr_present = self.hw.sdr_present
        self.ctx.store.state.dab.tuner = self.hw.sdr_tuner
        low = cfg.display.low_cpu
        self.ctx.store.state.display.low_cpu = (self.hw.low_power if low == "auto" else low == "on")
        self.ctx.refresh_settings_summary()
        sd_notify("READY=1")
        self._task = asyncio.create_task(self._loop(), name="sysinfo")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        sd_notify("STOPPING=1")

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:  # noqa: BLE001
                log.exception("sysinfo tick failed")
            await asyncio.sleep(self.ctx.config.system.heartbeat_interval_s)

    async def _check_sdr(self, st) -> None:
        """Hot plug, from sysfs only (rtl_test would open the stick, and a welle-cli starting at that moment would find
        it taken). A stick that is swapped or replugged gets a fresh welle-cli: one left running when its stick went
        away keeps answering on its web port with no samples, so neither systemd nor the supervisor would restart it."""
        u = await asyncio.to_thread(hwmod.sdr_usb)
        ident = hwmod.sdr_identity(u)
        if ident == self._sdr_id:
            return
        prev, self._sdr_id = self._sdr_id, ident
        present = u is not None
        log.info("SDR %s: %s", "plugged in" if present else "unplugged",
                 f"{u['manufacturer']} {u['product']} at {u['path']} (#{u['devnum']})" if u else (prev[0] if prev else "-"))
        tuner = hwmod.tuner_from_usb(u) if u else None  # rtl_test's name was for the stick that was there at boot
        st.sdr_present, st.sdr_tuner = present, tuner
        self.ctx.store.state.dab.sdr_present = present
        self.ctx.store.state.dab.tuner = tuner
        if present and self.ctx.config.dab.enabled and not (self._sdr_restart and not self._sdr_restart.done()):
            self._sdr_restart = asyncio.create_task(self._restart_dab_for_new_stick(), name="sdr-replug")

    async def _restart_dab_for_new_stick(self) -> None:
        from ..dab.service import DabService  # dab imports system: keep the cycle out of module load

        await asyncio.sleep(REPLUG_SETTLE_S)  # let udev apply the plugdev permissions first
        try:
            dab = self.ctx.svc(DabService)
        except KeyError:
            return
        if not await dab.restart_welle(force=True, reason="replugged"):
            await asyncio.sleep(REPLUG_SETTLE_S * 3)  # restarted moments ago (forced restarts are 5 s apart): once more for this stick
            await dab.restart_welle(force=True, reason="replugged")

    def _alarms_alive(self) -> bool:
        return alarms_alive(self.ctx)

    async def _tick(self) -> None:
        cfg = self.ctx.config
        st = self.ctx.store.state.system
        temp = hwmod._read(cfg.system.cpu_temp_path)
        st.cpu_temp_c = round(int(temp) / 1000, 1) if temp and temp.isdigit() else None
        up = hwmod._read("/proc/uptime")
        st.uptime_s = int(float(up.split()[0])) if up else int(time.time() - self.booted)
        try:
            st.load1 = round(os.getloadavg()[0], 2)
        except OSError:
            st.load1 = None
        mem = hwmod._read("/proc/meminfo") or ""
        tot = avail = None
        for line in mem.splitlines():
            if line.startswith("MemTotal:"):
                tot = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                avail = int(line.split()[1])
        if tot and avail is not None:
            st.mem_used_percent = round(100 * (1 - avail / tot), 1)
        if self.hw.is_pi:
            # under-voltage (bit 0x10000 since boot) is the first thing to check on a single 5 V supply,
            # and with a passive heatsink thermal throttling has to stay visible too
            th = await asyncio.to_thread(hwmod.read_throttled, cfg.system.vcgencmd_binary)
            if th != st.throttled and th:
                log.warning("vcgencmd get_throttled=0x%x (%s)", th, ", ".join(hwmod.throttle_flags(th)))
            st.throttled = th
            st.throttle_flags = hwmod.throttle_flags(th)
        st.heartbeat_at = self.ctx.store.iso()
        # heartbeat file + systemd watchdog. The watchdog is only fed while the alarm engine ticks: an engine stuck
        # on something is a reason for systemd to restart dawn-core (a ring in progress carries on afterwards).
        try:
            (self.ctx.runtime_dir / "heartbeat").write_text(str(int(time.time())))
        except OSError:
            pass
        alive = self._alarms_alive()
        self.ctx.store.state.alarms.engine_alive = alive  # the face rings by itself when this stays false
        if cfg.system.watchdog:
            if alive:
                sd_notify("WATCHDOG=1")
                self._stall_logged = False
            elif not self._stall_logged:
                self._stall_logged = True
                log.error("the alarm engine has stopped ticking; holding back the watchdog so systemd restarts dawn-core")
        if not self.ctx.sim:
            await self._check_sdr(st)
        self.ctx.store.touch()
