"""One shape for every diagnostic finding, from the RTL-SDR stick to the disk.

A check says what was looked at (title), what was found (detail), how it is (status) and, when it is not
fine, what to do about it (hint) and which one-tap fixes may help (actions, see actions.py).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Status = Literal["ok", "warn", "fail", "info", "off"]
AREAS = ["dab", "gps", "time", "network", "audio", "airplay", "bluetooth", "display", "inputs", "weather", "system"]
AREA_TITLES = {"dab": "DAB radio", "gps": "GPS", "time": "Time sync", "network": "Network", "audio": "Audio", "airplay": "AirPlay",
               "bluetooth": "Bluetooth", "display": "Display", "inputs": "Inputs", "weather": "Weather", "system": "System"}
RANK = {"fail": 0, "warn": 1, "info": 2, "ok": 3, "off": 4}


@dataclass
class Check:
    id: str
    area: str
    title: str
    status: Status
    detail: str
    hint: str | None = None
    actions: list[str] = field(default_factory=list)
    group: str | None = None  # a chain within the area, e.g. "GPS → chrony"

    def as_dict(self) -> dict:
        return asdict(self)


def summarise(checks: list[Check]) -> dict:
    counts = {k: sum(1 for c in checks if c.status == k) for k in RANK}
    problems = sorted((c for c in checks if c.status in ("fail", "warn")), key=lambda c: (RANK[c.status], AREAS.index(c.area) if c.area in AREAS else 99))
    return {"counts": counts, "problems": problems}


def ago(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    s = max(0, int(seconds))
    if s < 90:
        return f"{s} s ago"
    if s < 5400:
        return f"{s // 60} min ago"
    if s < 172800:
        return f"{s // 3600} h ago"
    return f"{s // 86400} days ago"
