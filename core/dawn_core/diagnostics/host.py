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
import time
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


def _cmdline_of(name: str) -> str | None:
    for d in glob.glob("/proc/[0-9]*"):
        if (_read(os.path.join(d, "comm")) or "").strip() == name:
            raw = _read(os.path.join(d, "cmdline")) or ""
            return " ".join(p for p in raw.split("\x00") if p) or None
    return None


class Host:
    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self._cache: dict[tuple[Any, ...], tuple[float, Any]] = {}  # slow reads that change slowly (process list, sudo chronyc)

    def forget(self) -> None:
        """After a fix: read everything afresh."""
        self._cache.clear()

    def _cached(self, key: tuple[Any, ...], max_age_s: float) -> tuple[bool, Any]:
        hit = self._cache.get(key)
        return (True, hit[1]) if hit and time.monotonic() - hit[0] < max_age_s else (False, None)

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

    async def unit_enabled(self, name: str) -> bool | None:
        """Whether a system unit is meant to run here (enabled, static or wanted by something), None when systemd
        cannot say. The answer is reused for an hour: units are not enabled or disabled behind dawn-core's back."""
        hit, value = self._cached(("enabled", name), 3600)
        if hit:
            return value  # type: ignore[no-any-return]
        rc, out = await self.run("systemctl", "is-enabled", name)
        word = (out.strip().splitlines() or [""])[0].strip()
        if rc == 127 or rc == 124 or not word:
            return None
        value = word in ("enabled", "enabled-runtime", "static", "indirect", "alias", "linked", "linked-runtime", "generated", "transient")
        self._cache[("enabled", name)] = (time.monotonic(), value)
        return value

    async def unit_active_for(self, name: str) -> float | None:
        """Seconds since the unit last became active, None when systemd cannot say."""
        rc, out = await self.run("systemctl", "show", "-p", "ActiveEnterTimestampMonotonic", "--value", name)
        word = out.strip().splitlines()[0].strip() if out.strip() else ""
        if rc != 0 or not word.isdigit() or int(word) == 0:
            return None
        return max(0.0, time.monotonic() - int(word) / 1e6)  # both are CLOCK_MONOTONIC on Linux

    async def user_units(self, names: list[str]) -> dict[str, str] | None:
        """ActiveState of the dawn user's own services (PipeWire and its session manager), None when the user
        manager cannot be reached (no user session: not a Pi install)."""
        rc, out = await self.run("systemctl", "--user", "is-active", *names)
        states = out.split()
        if rc == 127 or len(states) != len(names) or any(s.startswith("Failed") for s in states):
            return None
        return dict(zip(names, states, strict=True))

    async def user_restart(self, name: str) -> tuple[int, str]:
        """Restart one of the dawn user's own services (no sudo: it is the same user)."""
        return await self.run("systemctl", "--user", "restart", name, timeout=30)

    async def chronyc(self, *args: str, privileged: bool = False, max_age_s: float = 0) -> tuple[str | None, str | None]:
        """(csv output, error). -n: no reverse DNS (it hangs exactly when DNS is what is broken). selectdata and
        ntpdata need chronyd's Unix socket, i.e. root, on chrony 4.6; deploy/sudoers/dawn allows these exact lines.
        Every sudo writes to the journal, so privileged answers may be reused for `max_age_s`."""
        key = ("chronyc", *args)
        if privileged and max_age_s > 0:
            hit, value = self._cached(key, max_age_s)
            if hit:
                return value  # type: ignore[no-any-return]
        binary = self.ctx.config.time_sources.chronyc_binary
        rc, out = await (self.sudo(binary, "-n", "-c", *args) if privileged else self.run(binary, "-n", "-c", *args))
        res = (out, None) if rc == 0 else (None, out.strip() or f"exit status {rc}")
        if privileged:
            self._cache[key] = (time.monotonic(), res)
        return res

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
        """The command line of the first running process called `name` (e.g. welle-cli). Scanning /proc reads every
        process (a few hundred on the Pi), so it runs in a thread and is reused for 30 s."""
        hit, value = self._cached(("cmdline", name), 30)
        if hit:
            return value  # type: ignore[no-any-return]
        value = await asyncio.to_thread(_cmdline_of, name)
        self._cache[("cmdline", name)] = (time.monotonic(), value)
        return value

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

    async def unit_enabled(self, name: str) -> bool | None:
        return True  # the hub's units are all "installed"

    async def unit_active_for(self, name: str) -> float | None:
        return 3600.0

    async def user_units(self, names: list[str]) -> dict[str, str] | None:
        return None  # the laptop's own PipeWire is not Dawn's to restart

    async def user_restart(self, name: str) -> tuple[int, str]:
        return 1, "not in the simulator"

    async def chronyc(self, *args: str, privileged: bool = False, max_age_s: float = 0) -> tuple[str | None, str | None]:
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
