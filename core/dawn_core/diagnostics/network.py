"""Network: address -> gateway -> internet -> DNS -> Wi-Fi signal, plus NetworkManager, mDNS and the web UI's address."""

from __future__ import annotations

import socket
import struct
from typing import Any
from urllib.parse import urlparse

from ..context import DawnContext
from .checks import Check
from .host import Host


def parse_route(text: str) -> dict[str, str] | None:
    """Default route from /proc/net/route (hex, little-endian addresses)."""
    for line in text.splitlines()[1:]:
        p = line.split()
        if len(p) >= 3 and p[1] == "00000000":
            try:
                return {"interface": p[0], "gateway": socket.inet_ntoa(struct.pack("<I", int(p[2], 16)))}
            except (ValueError, struct.error):
                return {"interface": p[0], "gateway": "?"}
    return None


def parse_wireless(text: str) -> dict[str, dict[str, float]]:
    """/proc/net/wireless: link quality (of 70) and signal level (dBm) per interface."""
    out = {}
    for line in text.splitlines()[2:]:
        if ":" not in line:
            continue
        iface, _, rest = line.partition(":")
        p = rest.split()
        try:
            link, level = float(p[1].rstrip(".")), float(p[2].rstrip("."))
        except (IndexError, ValueError):
            continue
        out[iface.strip()] = {"quality_percent": round(min(100.0, link / 70 * 100)), "level_dbm": level}
    return out


async def collect(ctx: DawnContext, host: Host) -> dict[str, Any]:
    n = ctx.store.state.system.network
    cfg = ctx.config
    route = parse_route(await host.read("/proc/net/route") or "")
    resolv = await host.read("/etc/resolv.conf") or ""
    nameservers = [ln.split()[1] for ln in resolv.splitlines() if ln.startswith("nameserver") and len(ln.split()) > 1]
    probe_host = urlparse(cfg.weather.base_url).hostname or ""
    if not probe_host or probe_host == "localhost" or probe_host.replace(".", "").isdigit():
        probe_host = "pool.ntp.org"  # an address literal proves nothing about DNS
    addrs, dns_err = await host.resolve(probe_host) if n.online else ([], "offline")
    wireless = parse_wireless(await host.read("/proc/net/wireless") or "")
    units = await host.units(["NetworkManager", "avahi-daemon"])
    return {
        "online": n.online, "ip": n.ip, "ssid": n.ssid, "interface": n.interface, "hotspot": n.hotspot_active, "hotspot_ssid": n.hotspot_ssid,
        "check_host": cfg.network.online_check_host, "route": route, "nameservers": nameservers, "dns_host": probe_host,
        "dns_addresses": addrs, "dns_error": dns_err, "wireless": wireless, "units": units,
        "hostname": cfg.general.hostname, "port": cfg.web.port, "sim": ctx.sim,
    }


def checks(f: dict[str, Any]) -> list[Check]:
    c: list[Check] = []

    def add(id_: str, title: str, status: str, detail: str, hint: str | None = None) -> None:
        c.append(Check(f"net.{id_}", "network", title, status, detail, hint))  # type: ignore[arg-type]

    if f["hotspot"]:
        add("hotspot", "Setup hotspot", "warn", f"No network, so the setup hotspot \"{f['hotspot_ssid']}\" is on.",
            f"Join it from a phone and open http://10.42.0.1:{f['port']}/ to choose a Wi-Fi network.")
    if f["ip"] and not f["hotspot"]:
        add("address", "Address", "ok", f"{f['ip']}" + (f" on {f['ssid']}" if f["ssid"] else "") + (f" ({f['interface']})" if f["interface"] else "") + ".")
    elif not f["hotspot"]:
        add("address", "Address", "fail", "No IP address.", "Choose a Wi-Fi network under Settings → Wi-Fi.")
    if not f["sim"]:
        if f["route"]:
            add("gateway", "Router", "ok", f"Default route via {f['route']['gateway']} on {f['route']['interface']}.")
        elif f["ip"]:
            add("gateway", "Router", "fail", "No default route.", "The network gave an address but no router: check the router, or reconnect Wi-Fi.")
    if f["online"]:
        add("internet", "Internet", "ok", f"{f['check_host']} reachable.")
    else:
        add("internet", "Internet", "fail", f"Cannot reach {f['check_host']}.",
            "Weather and NTP need the internet; alarms, DAB and GPS time do not.")
    if f["online"]:
        if f["dns_addresses"]:
            add("dns", "DNS", "ok", f"{f['dns_host']} resolves" + (f" (servers {', '.join(f['nameservers'])})" if f["nameservers"] else "") + ".")
        else:
            add("dns", "DNS", "fail", f"{f['dns_host']} does not resolve: {f['dns_error']}", "Check the DNS server your router hands out.")
    for iface, w in f["wireless"].items():
        lvl = w["level_dbm"]
        status, words = ("ok", "good") if lvl >= -67 else ("warn", "weak") if lvl >= -78 else ("fail", "very weak")
        add(f"wifi.{iface}", "Wi-Fi signal", status, f"{iface}: {lvl:.0f} dBm, link quality {w['quality_percent']}% ({words}).",
            None if status == "ok" else "Move the clock or the access point, or keep the Pi's antenna side clear of metal and the speaker.")
    nm = f["units"].get("NetworkManager")
    if nm not in ("active", "unknown"):
        add("nm", "NetworkManager", "fail", f"NetworkManager is {nm}.", "Wi-Fi changes and the setup hotspot need it.")
    av = f["units"].get("avahi-daemon")
    if av == "active":
        add("mdns", "Name on the network", "ok", f"Reachable as {f['hostname']}.local.")
    elif av != "unknown":
        add("mdns", "Name on the network", "warn", f"avahi-daemon is {av}, so {f['hostname']}.local does not resolve.", f"Use the IP address instead: {f['ip']}.")
    add("url", "Control UI address", "info", f"http://{f['hostname']}.local:{f['port']}/" + (f" or http://{f['ip']}:{f['port']}/" if f["ip"] else "")
        + f". It listens on port {f['port']}, not 80, so the :{f['port']} is needed.")
    return c
