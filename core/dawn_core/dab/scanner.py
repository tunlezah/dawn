"""Band III scan: tune each channel, wait for sync, record ensemble + services."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from .welle import MuxInfo, WelleClient

log = logging.getLogger("dawn.dab.scan")

Progress = Callable[[int, int, str, int, int], Awaitable[None] | None]


def scan_order(all_channels: list[str], priority: list[str]) -> list[str]:
    seen: list[str] = []
    for c in [*priority, *all_channels]:
        c = c.upper()
        if c not in seen and c in [x.upper() for x in all_channels]:
            seen.append(c)
    return seen


async def scan(
    client: WelleClient,
    channels: list[str],
    dwell_s: float,
    on_progress: Progress | None = None,
    stop: asyncio.Event | None = None,
) -> dict[str, MuxInfo]:
    """Returns {channel: MuxInfo} for channels where an ensemble was found."""
    found: dict[str, MuxInfo] = {}
    n_services = 0
    for i, ch in enumerate(channels):
        if stop and stop.is_set():
            break
        if on_progress:
            r = on_progress(i, len(channels), ch, n_services, len(found))
            if asyncio.iscoroutine(r):
                await r
        ok = await client.set_channel(ch)
        if not ok:
            log.warning("retune to %s failed", ch)
            await asyncio.sleep(1)
            continue
        best: MuxInfo | None = None
        deadline = asyncio.get_running_loop().time() + dwell_s
        stable = 0
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.5)
            if stop and stop.is_set():
                break
            m = await client.mux()
            if m and m.sync and m.services:
                if best and len(m.services) == len(best.services):
                    stable += 1
                best = m
                if stable >= 2:  # service list stopped growing
                    break
        if best:
            best.channel = ch
            found[ch] = best
            n_services += len(best.services)
            log.info("scan %s: %s (%d services, snr %.1f)", ch, best.ensemble_label, len(best.services), best.snr or 0.0)
        else:
            log.debug("scan %s: nothing", ch)
    if on_progress:
        r = on_progress(len(channels), len(channels), "", n_services, len(found))
        if asyncio.iscoroutine(r):
            await r
    return found


def scanned_at() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
