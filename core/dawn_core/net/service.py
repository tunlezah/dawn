"""NetworkService: online check, IP/SSID, setup hotspot when there is no network."""

from __future__ import annotations

import asyncio
import logging
import socket
import time

import httpx

from ..config import DawnConfig
from ..context import DawnContext
from ..services import Service
from ..state.ui import SetupInfo
from .nmcli import parse_active, parse_wifi_list, run

log = logging.getLogger("dawn.net")

HOTSPOT_CON = "dawn-hotspot"
HOTSPOT_IP = "10.42.0.1"  # NetworkManager's default shared-connection gateway


class NetworkService(Service):
    name = "network"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self._task: asyncio.Task[None] | None = None
        self._offline_since: float | None = None
        self._booted = time.monotonic()
        self.hotspot_active = False
        self._hub = httpx.AsyncClient(timeout=2.0)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="network")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self._hub.aclose()

    async def on_config(self, old: DawnConfig, new: DawnConfig) -> None:
        if old.general.hostname != new.general.hostname:
            self.ctx.store.state.system.network.mdns_name = f"{new.general.hostname}.local"

    async def _loop(self) -> None:
        while True:
            try:
                await self.poll()
            except Exception:  # noqa: BLE001
                log.exception("network poll failed")
            await asyncio.sleep(5 if self.ctx.sim else self.ctx.config.network.online_check_interval_s)

    # ---- status ----------------------------------------------------------
    async def _probe(self) -> tuple[bool, str | None, str | None, str | None]:
        """(online, ip, ssid, interface)."""
        if self.ctx.sim:
            try:
                r = await self._hub.get(f"{self.ctx.config.sim.hub_url}/network")
                j = r.json()
                return bool(j.get("online")), j.get("ip"), j.get("ssid"), "wlan0" if j.get("online") else None
            except Exception:  # noqa: BLE001
                return False, None, None, None
        ip = _local_ip()
        online = await asyncio.to_thread(_tcp_reachable, self.ctx.config.network.online_check_host, 53, 2.0)
        ssid = iface = None
        rc, out = await run("nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "con", "show", "--active", timeout=5)
        if rc == 0:
            ssid, iface = parse_active(out)
            if ssid == HOTSPOT_CON:
                ssid = None
        return online, ip, ssid, iface

    async def poll(self) -> None:
        cfg = self.ctx.config.network
        online, ip, ssid, iface = await self._probe()
        st = self.ctx.store.state.system.network
        has_net = online or (ip is not None and not ip.startswith(HOTSPOT_IP.rsplit(".", 1)[0]))
        now = time.monotonic()
        if has_net:
            self._offline_since = None
            if self.hotspot_active:
                await self._hotspot(False)
        else:
            if self._offline_since is None:
                self._offline_since = now
            waited = now - max(self._offline_since, self._booted)
            if cfg.hotspot.enabled and not self.hotspot_active and waited >= (cfg.hotspot.wait_for_network_s if not self.ctx.sim else 10):
                await self._hotspot(True)
        st.online = online
        st.ip = ip if has_net else (HOTSPOT_IP if self.hotspot_active else None)
        st.ssid = ssid if has_net else None
        st.interface = iface
        st.hotspot_active = self.hotspot_active
        st.hotspot_ssid = self._hotspot_ssid() if self.hotspot_active else None
        st.mdns_name = f"{self.ctx.config.general.hostname}.local"
        self.ctx.store.touch()

    def _hotspot_ssid(self) -> str:
        cfg = self.ctx.config
        if cfg.network.hotspot.ssid:
            return cfg.network.hotspot.ssid
        serial = (self.ctx.store.state.system.hostname or "dawn").upper()[-4:]
        from ..system.sysinfo import SysInfoService

        try:
            hw = self.ctx.svc(SysInfoService).hw
            if hw.serial:
                serial = hw.serial[-4:].upper()
        except Exception:  # noqa: BLE001
            pass
        return f"{cfg.general.name.split(' ')[0]}-{serial}"

    async def _hotspot(self, on: bool) -> None:
        cfg = self.ctx.config.network.hotspot
        ssid = self._hotspot_ssid()
        if on:
            log.warning("no network for %ss; starting setup hotspot %s", cfg.wait_for_network_s, ssid)
            if not self.ctx.sim:
                await run("nmcli", "con", "delete", HOTSPOT_CON, timeout=10)
                rc, out = await run("nmcli", "dev", "wifi", "hotspot", "ifname", cfg.interface, "con-name", HOTSPOT_CON, "ssid", ssid, "password", cfg.password, timeout=30)
                if rc != 0:
                    log.error("hotspot failed: %s", out.strip())
                    return
            self.hotspot_active = True
            url = f"http://{HOTSPOT_IP}/"
            self.ctx.store.state.face.setup = SetupInfo(ssid=ssid, password=cfg.password, url=url, qr_payload=f"WIFI:T:WPA;S:{ssid};P:{cfg.password};;")
            self.ctx.db.log_event("hotspot_start", ssid=ssid)
        else:
            log.info("network is back; stopping setup hotspot")
            if not self.ctx.sim:
                await run("nmcli", "con", "down", HOTSPOT_CON, timeout=15)
            self.hotspot_active = False
            self.ctx.store.state.face.setup = None
            self.ctx.db.log_event("hotspot_stop")
        self.ctx.store.touch()

    # ---- wifi API --------------------------------------------------------
    async def wifi_networks(self) -> list[dict]:
        if self.ctx.sim:
            return [{"ssid": "HomeWiFi", "signal": 82, "security": "WPA2", "active": self.ctx.store.state.system.network.online}, {"ssid": "Neighbour", "signal": 40, "security": "WPA2", "active": False}, {"ssid": "CoffeeShop", "signal": 25, "security": "", "active": False}]
        rc, out = await run("nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "auto", timeout=25)
        if rc != 0:
            raise RuntimeError(out.strip() or "nmcli failed")
        return [n.__dict__ for n in parse_wifi_list(out)]

    async def wifi_connect(self, ssid: str, psk: str | None) -> str:
        if self.ctx.sim:
            await self._hub.post(f"{self.ctx.config.sim.hub_url}/set", json={"network_online": True})
            return f"(sim) connected to {ssid}"
        if self.hotspot_active:
            await self._hotspot(False)
        args = ["nmcli", "dev", "wifi", "connect", ssid]
        if psk:
            args += ["password", psk]
        rc, out = await run(*args, timeout=60)
        if rc != 0:
            raise RuntimeError(out.strip() or "connect failed")
        self.ctx.db.log_event("wifi_connect", ssid=ssid)
        return out.strip()


def _local_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            return None if ip.startswith("127.") else ip
    except OSError:
        return None


def _tcp_reachable(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
