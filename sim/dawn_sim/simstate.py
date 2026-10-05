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
    gps_signal: float = 38.0  # dBHz of the strongest satellite; the rest fall off from it
    dab_sync: bool = True  # decoder has sync on a tuned channel
    dab_snr: float = 14.5  # dB; below ~8 errors start, below ~3 the ensemble is lost
    sdr_present: bool = True  # fake welle up at all
    network_online: bool = True
    audio_flowing: bool = True  # fake PipeWire stream activity
    chrony_active: str = "GPS"  # which fake source chrony prefers
    channel: str = "9A"
    tuned_at: float = field(default_factory=time.time)
    input_queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    airplay_queue: asyncio.Queue[bytes] = field(default_factory=asyncio.Queue)
    airplay_session: bool = False
    airplay_playing: bool = False
    bt_discoverable: bool = False
    bt_alias: str = "Dawn"
    bt_devices: list[dict[str, Any]] = field(default_factory=lambda: [
        {"address": "AA:BB:CC:DD:EE:01", "name": "Pixel 8", "paired": True, "connected": False, "trusted": True, "icon": "phone", "rssi": None},
    ])
    bt_connected: str | None = None
    bt_player_status: str | None = None
    bt_track: dict[str, Any] = field(default_factory=dict)
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
            "dab_snr": self.dab_snr,
            "gps_signal": self.gps_signal,
            "sdr_present": self.sdr_present,
            "network_online": self.network_online,
            "audio_flowing": self.audio_flowing,
            "chrony_active": self.chrony_active,
            "channel": self.channel,
        }


STATE = SimState()
