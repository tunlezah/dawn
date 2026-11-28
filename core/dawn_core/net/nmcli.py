"""NetworkManager via nmcli (terse output parsing). Pure parsers are testable."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass


@dataclass
class WifiNetwork:
    ssid: str
    signal: int
    security: str
    active: bool


def parse_wifi_list(text: str) -> list[WifiNetwork]:
    """Parse `nmcli -t -f ACTIVE,SSID,SIGNAL,SECURITY dev wifi list` (colons in SSIDs are escaped as \\:)."""
    out: dict[str, WifiNetwork] = {}
    for line in text.splitlines():
        parts = _split_terse(line)
        if len(parts) < 4:
            continue
        active, ssid, signal, security = parts[0], parts[1], parts[2], parts[3]
        if not ssid:
            continue
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        net = WifiNetwork(ssid=ssid, signal=sig, security=security.strip() or "", active=(active == "yes"))
        prev = out.get(ssid)
        if prev is None or net.active or net.signal > prev.signal:
            out[ssid] = WifiNetwork(ssid, max(sig, prev.signal if prev else 0), net.security, net.active or (prev.active if prev else False))
    return sorted(out.values(), key=lambda n: (not n.active, -n.signal))


def _split_terse(line: str) -> list[str]:
    parts, cur, i = [], "", 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            cur += line[i + 1]
            i += 2
            continue
        if ch == ":":
            parts.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    parts.append(cur)
    return parts


WIFI_TYPES = ("802-11-wireless", "wifi")


def parse_wifi_profiles(text: str, exclude: str) -> list[str]:
    """Saved Wi-Fi connection names from `nmcli -t -f NAME,TYPE con show`, leaving out `exclude` (Dawn's own
    hotspot, which is a Wi-Fi profile too but not a network to join)."""
    names: list[str] = []
    for line in text.splitlines():
        parts = _split_terse(line)
        if len(parts) >= 2 and parts[1] in WIFI_TYPES and parts[0] and parts[0] != exclude and parts[0] not in names:
            names.append(parts[0])
    return names


def parse_ssids(text: str) -> set[str]:
    """SSIDs in view from `nmcli -t -f SSID dev wifi list` (hidden networks print an empty line)."""
    return {s for s in (_split_terse(line)[0] for line in text.splitlines()) if s}


def terse_value(text: str) -> str:
    """One value printed by `nmcli -t -g <field>` (colons and backslashes come escaped)."""
    lines = text.strip().splitlines()
    return _split_terse(lines[0])[0] if lines else ""


def parse_station_dump(text: str) -> int:
    """How many clients `iw dev <if> station dump` lists (one `Station <mac> (on <if>)` block each)."""
    return sum(1 for line in text.splitlines() if line.startswith("Station "))


def parse_active(text: str) -> tuple[str | None, str | None]:
    """From `nmcli -t -f NAME,TYPE,DEVICE con show --active`: (ssid/name, device) of the wifi/ethernet connection."""
    for line in text.splitlines():
        parts = _split_terse(line)
        if len(parts) >= 3 and parts[1] in ("802-11-wireless", "wifi", "802-3-ethernet", "ethernet"):
            return parts[0], parts[2]
    return None, None


async def run(*args: str, timeout: float = 15.0) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except FileNotFoundError:
        return 127, f"{args[0]} not found"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        with suppress(ProcessLookupError):
            proc.kill()  # not left behind: this runs every 30 s
        return 124, "timeout"
    return proc.returncode or 0, out.decode(errors="ignore")
