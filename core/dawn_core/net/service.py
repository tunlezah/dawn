"""NetworkService: online check, IP/SSID, setup hotspot when there is no network.

The hotspot takes the Wi-Fi radio, so while it is up the clock cannot see its own network come back: a router
that boots slower than the Pi after a power cut, or one that was restarted, would leave the clock on its hotspot
(off the LAN, no NTP, no weather, the face on the setup screen) until someone noticed. So while the hotspot is up
and a Wi-Fi network has been saved before, the hotspot is taken down every `retry_wifi_s` for a look: a saved
network in range is joined, otherwise the hotspot comes straight back. Not while someone is connected to the
hotspot's page: they are setting the clock up."""

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
from .nmcli import _split_terse, parse_active, parse_wifi_list, run

log = logging.getLogger("dawn.net")

HOTSPOT_CON = "dawn-hotspot"
HOTSPOT_IP = "10.42.0.1"  # NetworkManager's default shared-connection gateway
HOTSPOT_NET = HOTSPOT_IP.rsplit(".", 1)[0] + "."
MAX_DEFERRALS = 6  # a page left open on the hotspot holds the look for Wi-Fi off this many times, then no longer


class NetworkService(Service):
    name = "network"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self._task: asyncio.Task[None] | None = None
        self._offline_since: float | None = None
        self._booted = time.monotonic()
        self.hotspot_active = False
        self._wifi_tried_at = 0.0  # monotonic: when the hotspot came up, or the saved networks were last tried
        self._deferred = 0
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
            # one retry: polling every 5 s races the hub's 5 s keep-alive, and a pooled connection closed under
            # the request made the simulated network flap offline for a poll
            for attempt in range(2):
                try:
                    r = await self._hub.get(f"{self.ctx.config.sim.hub_url}/network")
                    j = r.json()
                    return bool(j.get("online")), j.get("ip"), j.get("ssid"), "wlan0" if j.get("online") else None
                except Exception:  # noqa: BLE001
                    if attempt:
                        return False, None, None, None
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
            elif self.hotspot_active and await self._retry_wifi(now):
                self._offline_since = None
                online, ip, ssid, iface = await self._probe()
                has_net = online or (ip is not None and not ip.startswith(HOTSPOT_NET))
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

    # ---- back to Wi-Fi from the hotspot -----------------------------------
    def _hotspot_in_use(self) -> bool:
        """Someone has the control UI open over the hotspot."""
        hub = self.ctx.ws_hub
        if hub is None:
            return False
        return any(str(c.get("remote") or "").startswith(HOTSPOT_NET) and c.get("remote") != HOTSPOT_IP for c in hub.connections())

    async def _saved_wifi(self) -> list[tuple[str, str]]:
        """(connection name, SSID) of every Wi-Fi network saved on this box, the hotspot's own profile left out."""
        rc, out = await run("nmcli", "-t", "-f", "NAME,TYPE", "con", "show", timeout=10)
        if rc != 0:
            return []
        saved = []
        for line in out.splitlines():
            parts = _split_terse(line)
            if len(parts) < 2 or parts[1] not in ("802-11-wireless", "wifi") or parts[0] == HOTSPOT_CON:
                continue
            rc, detail = await run("nmcli", "-g", "802-11-wireless.ssid,802-11-wireless.mode", "con", "show", parts[0], timeout=10)
            fields = detail.splitlines() if rc == 0 else []
            if len(fields) >= 2 and fields[1].strip() == "ap":
                continue  # another access-point profile: bringing it up would not be a network
            saved.append((parts[0], fields[0].strip() if fields and fields[0].strip() else parts[0]))
        return saved

    async def _retry_wifi(self, now: float) -> bool:
        """The hotspot is up: when it is time, take it down and join a saved Wi-Fi network that is in range. True
        when one was joined; the hotspot is back up otherwise."""
        cfg = self.ctx.config.network.hotspot
        if self.ctx.sim or cfg.retry_wifi_s <= 0 or now - self._wifi_tried_at < cfg.retry_wifi_s:
            return False
        self._wifi_tried_at = now
        if self._hotspot_in_use() and self._deferred < MAX_DEFERRALS:
            self._deferred += 1
            log.info("someone is connected to the setup hotspot; not looking for Wi-Fi now")
            return False
        self._deferred = 0
        saved = await self._saved_wifi()
        if not saved:
            return False  # never been on a network: the hotspot is all there is until one is chosen
        log.info("setup hotspot down for a moment to look for %s", ", ".join(ssid for _, ssid in saved))
        await run("nmcli", "con", "down", HOTSPOT_CON, timeout=15)
        rc, out = await run("nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "ifname", cfg.interface, "--rescan", "yes", timeout=25)
        in_range = {n.ssid for n in parse_wifi_list(out)} if rc == 0 else None  # None: the scan failed, try them all
        for name, ssid in saved:
            if in_range is not None and ssid not in in_range:
                continue
            rc, out = await run("nmcli", "con", "up", name, "ifname", cfg.interface, timeout=45)
            if rc == 0:
                log.warning("Wi-Fi %s is back; the setup hotspot stays down", ssid)
                self.hotspot_active = False
                self.ctx.store.state.face.setup = None
                self.ctx.db.log_event("hotspot_stop", reason="wifi_back", ssid=ssid)
                self.ctx.store.touch()
                return True
            log.info("could not join %s: %s", ssid, out.strip().splitlines()[-1] if out.strip() else f"exit {rc}")
        await self._hotspot(True, again=True)
        return False

    async def _hotspot(self, on: bool, *, again: bool = False) -> None:
        cfg = self.ctx.config.network.hotspot
        ssid = self._hotspot_ssid()
        if on:
            if not again:
                log.warning("no network for %ss; starting setup hotspot %s", cfg.wait_for_network_s, ssid)
            if not self.ctx.sim:
                await run("nmcli", "con", "delete", HOTSPOT_CON, timeout=10)
                rc, out = await run("nmcli", "dev", "wifi", "hotspot", "ifname", cfg.interface, "con-name", HOTSPOT_CON, "ssid", ssid, "password", cfg.password, timeout=30)
                if rc != 0:
                    log.error("hotspot failed: %s", out.strip())
                    if again:  # it was up a moment ago: the next poll starts it afresh
                        self.hotspot_active = False
                        self.ctx.store.state.face.setup = None
                        self.ctx.store.touch()
                    return
            self.hotspot_active = True
            if again:
                return  # the same hotspot as before the look for Wi-Fi: nothing new to show or to log
            self._wifi_tried_at, self._deferred = time.monotonic(), 0
            port = self.ctx.config.web.port
            url = f"http://{HOTSPOT_IP}{'' if port == 80 else f':{port}'}/"  # core listens on web.port; nothing serves port 80
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
