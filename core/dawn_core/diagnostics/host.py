"""The OS seam for diagnostics: systemd unit states, chronyc, /proc and /sys files, helper commands.

Everything Diagnostics reads from or runs on the machine goes through `Host`. The simulator swaps in
`SimHost`, which answers from the sim hub, the same way DAWN_SIM swaps every other hardware backend, so
the Diagnostics page shows the same checks on a laptop.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import shutil
import socket
from pathlib import Path
from typing import Any

import httpx

from ..context import DawnContext

log = logging.getLogger("dawn.diag.host")


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


class Host:
    def __init__(self, ctx: DawnContext):
        self.ctx = ctx

    async def close(self) -> None:
        pass

    async def run(self, *args: str, timeout: float = 5.0) -> tuple[int, str]:
        """(exit code, stdout + stderr). 124 = timed out, 127 = could not run."""
        try:
            proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        except OSError as e:
            return 127, str(e)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            return 124, f"{args[0]} timed out after {timeout:.0f} s"
        return proc.returncode or 0, out.decode(errors="ignore")

    async def sudo(self, *args: str, timeout: float = 15.0) -> tuple[int, str]:
        """One of the commands in deploy/sudoers/dawn, without a password prompt."""
        return await self.run(self.ctx.config.system.sudo_binary, "-n", *args, timeout=timeout)

    async def units(self, names: list[str]) -> dict[str, str]:
        """systemd ActiveState per unit: active, inactive, failed, activating..."""
        _, out = await self.run("systemctl", "is-active", *names)
        states = out.split()
        return dict(zip(names, states, strict=True)) if len(states) == len(names) else {n: "unknown" for n in names}

    async def chronyc(self, *args: str, privileged: bool = False) -> tuple[str | None, str | None]:
        """(csv output, error). -n: no reverse DNS (it hangs exactly when DNS is what is broken). selectdata and
        ntpdata need chronyd's Unix socket, i.e. root, on chrony 4.6; deploy/sudoers/dawn allows these exact lines."""
        binary = self.ctx.config.time_sources.chronyc_binary
        rc, out = await (self.sudo(binary, "-n", "-c", *args) if privileged else self.run(binary, "-n", "-c", *args))
        return (out, None) if rc == 0 else (None, out.strip() or f"exit status {rc}")

    async def read(self, path: str) -> str | None:
        return _read(path)

    async def exists(self, path: str) -> bool:
        return os.path.exists(path)

    async def usb_devices(self) -> list[dict[str, str]]:
        out = []
        for dev in glob.glob("/sys/bus/usb/devices/*"):
            vid, pid = _read(os.path.join(dev, "idVendor")), _read(os.path.join(dev, "idProduct"))
            if vid and pid:
                out.append({"vid": vid.strip(), "pid": pid.strip(), "product": (_read(os.path.join(dev, "product")) or "").strip(),
                            "manufacturer": (_read(os.path.join(dev, "manufacturer")) or "").strip(), "path": os.path.basename(dev),
                            "power": (_read(os.path.join(dev, "bMaxPower")) or "").strip()})
        return out

    async def process_cmdline(self, name: str) -> str | None:
        """The command line of the first running process called `name` (e.g. welle-cli)."""
        for d in glob.glob("/proc/[0-9]*"):
            if (_read(os.path.join(d, "comm")) or "").strip() == name:
                raw = _read(os.path.join(d, "cmdline")) or ""
                return " ".join(p for p in raw.split("\x00") if p) or None
        return None

    async def resolve(self, host: str) -> tuple[list[str], str | None]:
        try:
            infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, 123, type=socket.SOCK_DGRAM), 5)
            return sorted({i[4][0] for i in infos}), None
        except (OSError, TimeoutError) as e:
            return [], str(e) or type(e).__name__

    async def disk(self, path: str) -> tuple[int, int] | None:
        try:
            u = shutil.disk_usage(path)
            return u.total, u.free
        except OSError:
            return None


class SimHost(Host):
    """Answers from the sim hub: unit states follow the hub's toggles, chronyc is the hub's fake chrony."""

    def __init__(self, ctx: DawnContext):
        super().__init__(ctx)
        self._hub = httpx.AsyncClient(timeout=3.0)

    @property
    def hub(self) -> str:
        return self.ctx.config.sim.hub_url

    async def close(self) -> None:
        await self._hub.aclose()

    async def _get(self, route: str, **params: Any) -> httpx.Response | None:
        try:
            return await self._hub.get(f"{self.hub}{route}", params=params or None)
        except httpx.HTTPError:
            return None

    async def sudo(self, *args: str, timeout: float = 15.0) -> tuple[int, str]:
        r = await self._get("/sudo", cmd=" ".join(args))
        return (0, r.text) if r is not None and r.status_code == 200 else (1, r.text if r is not None else "sim hub unreachable")

    async def units(self, names: list[str]) -> dict[str, str]:
        r = await self._get("/systemctl", units=",".join(names))
        return r.json() if r is not None and r.status_code == 200 else {n: "unknown" for n in names}

    async def chronyc(self, *args: str, privileged: bool = False) -> tuple[str | None, str | None]:
        r = await self._get(f"/chrony/{args[-1]}")
        return (r.text, None) if r is not None and r.status_code == 200 else (None, "chronyc: sim hub unreachable")

    async def read(self, path: str) -> str | None:
        if path.startswith(("/proc/sysvipc/", "/proc/net/wireless", "/proc/modules", "/etc/chrony/")):
            r = await self._get("/proc", path=path)
            return r.text if r is not None and r.status_code == 200 else None
        return await super().read(path)

    async def exists(self, path: str) -> bool:
        r = await self._get("/exists", path=path)
        return bool(r is not None and r.status_code == 200 and r.json().get("exists"))

    async def usb_devices(self) -> list[dict[str, str]]:
        r = await self._get("/usb")
        return r.json() if r is not None and r.status_code == 200 else []

    async def process_cmdline(self, name: str) -> str | None:
        if name != "welle-cli":
            return None
        env = await super().read(str(self.ctx.data_dir / "dab.env")) or ""
        kv = dict(line.split("=", 1) for line in env.splitlines() if "=" in line)
        return f"/usr/local/bin/welle-cli -c {kv.get('DAWN_DAB_CHANNEL', '9A')} {kv.get('DAWN_WELLE_ARGS', '-w 8000')}"

    async def resolve(self, host: str) -> tuple[list[str], str | None]:
        r = await self._get("/network")
        online = bool(r is not None and r.status_code == 200 and r.json().get("online"))
        return (["203.0.113.10", "203.0.113.11"], None) if online else ([], "Temporary failure in name resolution (sim offline)")


def make_host(ctx: DawnContext) -> Host:
    return SimHost(ctx) if ctx.sim else Host(ctx)
