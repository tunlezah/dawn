"""NetworkManager via nmcli (terse output parsing). Pure parsers are testable."""

from __future__ import annotations

import asyncio
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
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return proc.returncode or 0, out.decode(errors="ignore")
    except FileNotFoundError:
        return 127, "nmcli not found"
    except TimeoutError:
        return 124, "timeout"
