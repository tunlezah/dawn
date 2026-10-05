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


def git_rev(repo: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


class SysInfoService(Service):
    name = "sysinfo"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self._task: asyncio.Task[None] | None = None
        self.hw = hwmod.HardwareInfo()
        self.booted = time.time()

    async def start(self) -> None:
        cfg = self.ctx.config
        if self.ctx.sim:
            self.hw = hwmod.HardwareInfo(model="simulator", panel="hdmi", i2c_buses=[])
        else:
            self.hw = await asyncio.to_thread(hwmod.detect, cfg.display.panel, cfg.display.backlight.sysfs_path)
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
                self._tick()
            except Exception:  # noqa: BLE001
                log.exception("sysinfo tick failed")
            await asyncio.sleep(self.ctx.config.system.heartbeat_interval_s)

    def _tick(self) -> None:
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
            th = hwmod.read_throttled(cfg.system.vcgencmd_binary)
            if th != st.throttled and th:
                log.warning("vcgencmd get_throttled=0x%x (%s)", th, ", ".join(hwmod.throttle_flags(th)))
            st.throttled = th
            st.throttle_flags = hwmod.throttle_flags(th)
        st.heartbeat_at = self.ctx.store.iso()
        # heartbeat file + systemd watchdog
        try:
            (self.ctx.runtime_dir / "heartbeat").write_text(str(int(time.time())))
        except OSError:
            pass
        if cfg.system.watchdog:
            sd_notify("WATCHDOG=1")
        if not self.ctx.sim and st.uptime_s % 60 < cfg.system.heartbeat_interval_s:
            # re-check SDR presence once a minute (hot plug)
            present, tuner, _ = hwmod.detect_sdr()
            if present != st.sdr_present:
                log.info("SDR presence changed: %s (%s)", present, tuner)
            st.sdr_present, st.sdr_tuner = present, tuner
            self.ctx.store.state.dab.sdr_present = present
            self.ctx.store.state.dab.tuner = tuner
        self.ctx.store.touch()
