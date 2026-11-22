"""NetworkService: online check, IP/SSID, setup hotspot when there is no network."""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from pathlib import Path

import httpx

from ..config import DawnConfig
from ..context import DawnContext
from ..services import Service
from ..state.ui import SetupInfo
from ..tasks import spawn
from .nmcli import (
    parse_active,
    parse_ssids,
    parse_station_dump,
    parse_wifi_list,
    parse_wifi_profiles,
    run,
    terse_value,
)

log = logging.getLogger("dawn.net")

HOTSPOT_CON = "dawn-hotspot"
HOTSPOT_IP = "10.42.0.1"  # NetworkManager's default shared-connection gateway
PROFILES_TTL_S = 120.0  # saved Wi-Fi profiles change rarely; nmcli is asked again after this long
SCAN_SETTLE_S = 10.0  # after `nmcli dev wifi rescan`, before the list is read


class NetworkService(Service):
    name = "network"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self._task: asyncio.Task[None] | None = None
        self._offline_since: float | None = None
        self._booted = time.monotonic()
        self.hotspot_active = False
        self._hub = httpx.AsyncClient(timeout=2.0)
        # ssid -> profile name of the saved Wi-Fi networks (None = not asked yet), and when it was read
        self._profiles: dict[str, str] | None = None
        self._profiles_at = 0.0
        self._last_retry: float | None = None
        self._lock = asyncio.Lock()  # a retry and a connect from the web must not take wlan0 down under each other
        self._sleep = asyncio.sleep  # tests replace it

    async def start(self) -> None:
        self._task = spawn(self._loop(), name="network")

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
                    if self.hotspot_active:
                        # as on the Pi: while wlan0 is the access point there is no upstream, whatever the router does
                        return False, None, None, None
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
        if not online and ip is None and _ethernet_carrier():
            # a cable is plugged in: that is a network, even if it has not handed out an address yet; the hotspot
            # would not help, and it would take wlan0 away from a Wi-Fi that may come back
            online = True
            iface = iface or "eth0"
        return online, ip, ssid, iface

    async def poll(self, now: float | None = None) -> None:
        cfg = self.ctx.config.network
        now = time.monotonic() if now is None else now
        online, ip, ssid, iface = await self._probe()
        st = self.ctx.store.state.system.network
        has_net = online or (ip is not None and not ip.startswith(HOTSPOT_IP.rsplit(".", 1)[0]))
        if has_net:
            self._offline_since = None
            if self.hotspot_active:
                await self._hotspot(False)
        else:
            if self._offline_since is None:
                self._offline_since = now
            waited = now - max(self._offline_since, self._booted)
            short = cfg.hotspot.wait_for_network_s if not self.ctx.sim else 10
            if cfg.hotspot.enabled and not self.hotspot_active and waited >= short:
                # A box that has never been set up wants the hotspot soon. One with a saved Wi-Fi network is most
                # likely watching its router reboot: going into setup then took wlan0 away for good (no upstream
                # while it is the access point, so the network never looked back) and put a white QR code on the
                # bedside face all night.
                configured = await self._wifi_configured(now)
                if waited >= (cfg.hotspot.configured_wait_s if configured else short):
                    await self._hotspot(True)
            elif self.hotspot_active and await self._wifi_configured(now):
                if self._last_retry is None:
                    self._last_retry = now  # the first look is one retry interval after the hotspot came up
                elif now - self._last_retry >= cfg.hotspot.retry_every_s:
                    self._last_retry = now
                    if await self._retry_known():
                        online, has_net = False, True  # joined; the next poll reads the new address
        st.online = online
        st.ip = ip if has_net else (HOTSPOT_IP if self.hotspot_active else None)
        st.ssid = ssid if has_net else None
        st.interface = iface
        st.hotspot_active = self.hotspot_active
        st.hotspot_ssid = self._hotspot_ssid() if self.hotspot_active else None
        st.wifi_configured = bool(self._profiles) if self._profiles is not None else st.wifi_configured
        st.mdns_name = f"{self.ctx.config.general.hostname}.local"
        self.ctx.store.touch()

    # ---- saved networks and the retry ----------------------------------------
    async def _wifi_profiles(self, now: float | None = None, *, refresh: bool = False) -> dict[str, str]:
        """The saved Wi-Fi networks as {ssid: profile name}, leaving out Dawn's own hotspot. A profile's name is not
        always its SSID (Raspberry Pi Imager calls its one "preconfigured"), so each SSID is read from the profile."""
        now = time.monotonic() if now is None else now
        if self._profiles is not None and not refresh and now - self._profiles_at < PROFILES_TTL_S:
            return self._profiles
        profiles: dict[str, str] = {}
        if self.ctx.sim:
            try:
                j = (await self._hub.get(f"{self.ctx.config.sim.hub_url}/network")).json()
                if j.get("wifi_configured", True):
                    profiles = {"HomeWiFi": "HomeWiFi"}
            except Exception:  # noqa: BLE001
                return self._profiles or {}
        else:
            rc, out = await run("nmcli", "-t", "-f", "NAME,TYPE", "con", "show", timeout=10)
            if rc != 0:
                log.warning("cannot list saved Wi-Fi networks: %s", out.strip()[:200])
                return self._profiles or {}  # keep what was known: an nmcli hiccup is not "never set up"
            for name in parse_wifi_profiles(out, HOTSPOT_CON):
                rc, ssid = await run("nmcli", "-t", "-g", "802-11-wireless.ssid", "con", "show", "id", name, timeout=10)
                ssid = terse_value(ssid) if rc == 0 else ""
                profiles[ssid or name] = name
        self._profiles, self._profiles_at = profiles, now
        return profiles

    async def _wifi_configured(self, now: float | None = None) -> bool:
        return bool(await self._wifi_profiles(now))

    async def _hotspot_clients(self) -> int:
        """Phones associated with the hotspot right now. If `iw` cannot say, none: a retry that briefly drops the
        hotspot under someone is better than a clock that never looks for its own network again."""
        if self.ctx.sim:
            try:
                return 1 if (await self._hub.get(f"{self.ctx.config.sim.hub_url}/wifi/station")).json().get("client") else 0
            except Exception:  # noqa: BLE001
                return 0
        rc, out = await run("iw", "dev", self.ctx.config.network.hotspot.interface, "station", "dump", timeout=5)
        if rc != 0:
            log.warning("cannot list hotspot clients (%s); assuming none", out.strip()[:200])
            return 0
        return parse_station_dump(out)

    async def _visible_ssids(self) -> set[str]:
        if self.ctx.sim:
            try:
                return set((await self._hub.get(f"{self.ctx.config.sim.hub_url}/wifi/scan")).json().get("ssids", []))
            except Exception:  # noqa: BLE001
                return set()
        await run("nmcli", "dev", "wifi", "rescan", timeout=15)
        await self._sleep(SCAN_SETTLE_S)
        rc, out = await run("nmcli", "-t", "-f", "SSID", "dev", "wifi", "list", timeout=20)
        return parse_ssids(out) if rc == 0 else set()

    async def _join(self, profile: str) -> tuple[bool, str]:
        if self.ctx.sim:
            try:
                j = (await self._hub.post(f"{self.ctx.config.sim.hub_url}/wifi/up", json={"profile": profile})).json()
                return bool(j.get("ok")), ""
            except Exception as e:  # noqa: BLE001
                return False, str(e)
        rc, out = await run("nmcli", "con", "up", "id", profile, timeout=45)  # `id`: a profile name is never read as an option
        return rc == 0, out.strip()

    async def _retry_known(self) -> bool:
        """The hotspot is up on a box with a saved Wi-Fi network: take it down for a moment, look for that network
        and join it if it is in view; otherwise put the hotspot back. Never while a phone is on the hotspot (someone
        is setting the clock up). True when a saved network was joined."""
        async with self._lock:
            if not self.hotspot_active:
                return False
            clients = await self._hotspot_clients()
            if clients:
                log.info("hotspot retry skipped: %d client(s) connected", clients)
                self.ctx.db.log_event("hotspot_retry", outcome="client connected")
                return False
            profiles = await self._wifi_profiles(refresh=True)
            if not profiles:
                return False
            if not self.ctx.sim:
                await run("nmcli", "con", "down", HOTSPOT_CON, timeout=15)
            visible = await self._visible_ssids()
            known = [ssid for ssid in profiles if ssid in visible]
            joined, why = False, ""
            for ssid in known:
                joined, why = await self._join(profiles[ssid])
                if joined:
                    log.warning("saved Wi-Fi network %s is back; joined it and stopped the setup hotspot", ssid)
                    self.ctx.db.log_event("hotspot_retry", outcome="joined", ssid=ssid)
                    self._set_hotspot_off()
                    return True
            outcome = "join failed" if known else "no saved network in view"
            log.info("hotspot retry: %s%s; hotspot back up", outcome, f" ({why[:200]})" if why else "")
            self.ctx.db.log_event("hotspot_retry", outcome=outcome)
            await self._hotspot(True, retry=True)
            return False

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

    async def _hotspot(self, on: bool, *, retry: bool = False) -> None:
        cfg = self.ctx.config.network.hotspot
        ssid = self._hotspot_ssid()
        if on:
            if not retry:
                log.warning("no network; starting setup hotspot %s", ssid)
            if not self.ctx.sim:
                await run("nmcli", "con", "delete", HOTSPOT_CON, timeout=10)
                rc, out = await run("nmcli", "dev", "wifi", "hotspot", "ifname", cfg.interface, "con-name", HOTSPOT_CON, "ssid", ssid, "password", cfg.password, timeout=30)
                if rc != 0:
                    log.error("hotspot failed: %s", out.strip())
                    if retry:
                        self._set_hotspot_off()  # wlan0 is a plain client again; the offline wait starts over
                        self._offline_since = None
                    return
            self.hotspot_active = True
            port = self.ctx.config.web.port
            url = f"http://{HOTSPOT_IP}{'' if port == 80 else f':{port}'}/"  # core listens on web.port; nothing serves port 80
            self.ctx.store.state.face.setup = SetupInfo(ssid=ssid, password=cfg.password, url=url, qr_payload=f"WIFI:T:WPA;S:{ssid};P:{cfg.password};;")
            if not retry:
                self._last_retry = None
                self.ctx.db.log_event("hotspot_start", ssid=ssid)
        else:
            log.info("network is back; stopping setup hotspot")
            if not self.ctx.sim:
                await run("nmcli", "con", "down", HOTSPOT_CON, timeout=15)
            self._set_hotspot_off()
        self.ctx.store.touch()

    def _set_hotspot_off(self) -> None:
        self.hotspot_active = False
        self._last_retry = None
        self.ctx.store.state.face.setup = None
        self.ctx.store.state.face.setup_open = False
        st = self.ctx.store.state.system.network
        st.hotspot_active, st.hotspot_ssid = False, None
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
        async with self._lock:
            self._profiles = None  # a new profile is about to exist
            if self.ctx.sim:
                await self._hub.post(f"{self.ctx.config.sim.hub_url}/set", json={"network_online": True, "wifi_configured": True})
                if self.hotspot_active:
                    await self._hotspot(False)
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


def _ethernet_carrier(root: Path = Path("/sys/class/net")) -> bool:
    """A wired interface with its cable in (carrier 1)."""
    try:
        for dev in root.iterdir():
            if dev.name.startswith(("eth", "en")):
                try:
                    if (dev / "carrier").read_text().strip() == "1":
                        return True
                except OSError:
                    continue  # reading carrier of an interface that is down raises EINVAL
    except OSError:
        pass
    return False


def _tcp_reachable(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
