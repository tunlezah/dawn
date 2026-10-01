"""Parse `chronyc -c sources` / `chronyc -c tracking` (CSV output) into state."""

from __future__ import annotations

from dataclasses import dataclass

from ..state.ui import TimeSource


@dataclass
class ChronySource:
    mode: str  # ^ server, = peer, # refclock
    state: str  # * current, + combined, - not combined, ? unreachable, x falseticker, ~ variable
    name: str
    stratum: int
    poll: int
    reach: int
    last_rx_s: int
    offset_s: float
    adjusted_offset_s: float
    error_s: float


@dataclass
class ChronyTracking:
    refid: str
    refname: str
    stratum: int
    system_offset_s: float
    last_offset_s: float
    rms_s: float
    leap: str

    @property
    def synced(self) -> bool:
        return self.leap.lower().startswith("normal") and 0 < self.stratum < 16


def _f(v: str) -> float:
    try:
        return float(v)
    except ValueError:
        return 0.0


def _i(v: str) -> int:
    try:
        return int(v)
    except ValueError:
        return 0


def parse_sources(text: str) -> list[ChronySource]:
    out = []
    for line in text.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 10 or not parts[0]:
            continue
        out.append(
            ChronySource(
                mode=parts[0], state=parts[1], name=parts[2], stratum=_i(parts[3]), poll=_i(parts[4]), reach=_i(parts[5]),
                last_rx_s=_i(parts[6]), offset_s=_f(parts[7]), adjusted_offset_s=_f(parts[8]), error_s=_f(parts[9]),
            )
        )
    return out


def parse_tracking(text: str) -> ChronyTracking | None:
    parts = text.strip().split(",")
    if len(parts) < 7:
        return None
    return ChronyTracking(
        refid=parts[0], refname=parts[1], stratum=_i(parts[2]), system_offset_s=_f(parts[4]), last_offset_s=_f(parts[5]),
        rms_s=_f(parts[6]), leap=parts[-1] if len(parts) >= 14 else "",
    )


def classify(name: str) -> str:
    n = name.upper()
    if n in ("GPS", "PPS", "GPS0") or n.startswith("GPS"):
        return "gps"
    if n in ("DAB", "DAB+") or n.startswith("DAB"):
        return "dab"
    if n.startswith("127.127."):
        return "other"
    return "ntp"


def to_time_sources(sources: list[ChronySource], stale_after_s: int) -> list[TimeSource]:
    out = []
    for s in sources:
        live = s.reach != 0 and s.state not in ("?", "x") and (s.last_rx_s <= stale_after_s)
        out.append(
            TimeSource(
                name=s.name, kind=classify(s.name), state=s.state, selected=(s.state == "*"), reach=s.reach,  # type: ignore[arg-type]
                last_rx_s=s.last_rx_s, offset_ms=round(s.offset_s * 1000, 3), live=live,
            )
        )
    return out


def active_kind(sources: list[ChronySource], tracking: ChronyTracking | None) -> str:
    """GPS | DAB | NTP | none: the selected (*) source's kind, else what tracking reports."""
    for s in sources:
        if s.state == "*":
            return {"gps": "GPS", "dab": "DAB", "ntp": "NTP"}.get(classify(s.name), "none")
    if tracking and tracking.synced:
        k = classify(tracking.refname)
        return {"gps": "GPS", "dab": "DAB", "ntp": "NTP"}.get(k, "none")
    return "none"
