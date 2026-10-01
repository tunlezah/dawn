"""Shared mutable state for all simulators (one process)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SimState:
    lux: float = 120.0
    backlight: int = 60
    gps_fix: bool = True
    gps_lat: float = -33.8688
    gps_lon: float = 151.2093
    gps_sats: int = 9
    dab_sync: bool = True  # decoder has sync on a tuned channel
    sdr_present: bool = True  # fake welle up at all
    network_online: bool = True
    audio_flowing: bool = True  # fake PipeWire stream activity
    chrony_active: str = "GPS"  # which fake source chrony prefers
    channel: str = "9A"
    tuned_at: float = field(default_factory=time.time)
    input_queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    log: list[str] = field(default_factory=list)

    def note(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        self.log.append(line)
        del self.log[:-200]
        print(f"[sim] {msg}", flush=True)

    def as_dict(self) -> dict[str, Any]:
        return {
            "lux": self.lux,
            "backlight": self.backlight,
            "gps_fix": self.gps_fix,
            "gps_lat": self.gps_lat,
            "gps_lon": self.gps_lon,
            "gps_sats": self.gps_sats,
            "dab_sync": self.dab_sync,
            "sdr_present": self.sdr_present,
            "network_online": self.network_online,
            "audio_flowing": self.audio_flowing,
            "chrony_active": self.chrony_active,
            "channel": self.channel,
        }


STATE = SimState()
