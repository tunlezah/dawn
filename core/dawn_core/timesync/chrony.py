"""Parse `chronyc -c ...` (CSV output) into state and diagnostics.

In CSV mode every conversion in chronyc's print format is its own field (see
print_report in chronyc's client.c), so e.g. `selectdata` prints each option
letter as a separate column. `sources` prints reach in octal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

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
    ref_time: float | None = None  # unix time of the last update
    freq_ppm: float | None = None
    resid_freq_ppm: float | None = None
    skew_ppm: float | None = None
    root_delay_s: float | None = None
    root_dispersion_s: float | None = None
    update_interval_s: float | None = None

    @property
    def synced(self) -> bool:
        return self.leap.lower().startswith("normal") and 0 < self.stratum < 16


@dataclass
class ChronySourceStats:
    name: str
    samples: int
    runs: int
    span_s: int
    freq_ppm: float
    skew_ppm: float
    offset_s: float
    std_dev_s: float


@dataclass
class ChronySelect:
    state: str
    name: str
    authenticated: bool
    options: str  # configured selection options, e.g. "PT" (prefer, trust)
    effective: str
    last_sample_s: int
    score: float
    interval_lo_s: float
    interval_hi_s: float
    leap: str


@dataclass
class ChronyNtpData:
    remote: str
    stratum: int
    poll_s: float
    root_delay_s: float
    root_dispersion_s: float
    offset_s: float
    peer_delay_s: float
    peer_dispersion_s: float
    response_time_s: float
    tests: str  # NTP test bits, e.g. "111 111 1111"
    total_tx: int
    total_rx: int
    total_valid_rx: int


@dataclass
class ChronyActivity:
    online: int
    offline: int
    burst_online: int
    burst_offline: int
    unresolved: int


# selectdata states (chronyc.adoc): why a source is or is not used
SELECT_STATES: dict[str, tuple[str, str]] = {
    "N": ("ignored", "has the noselect option"),
    "M": ("not enough samples", "does not have enough measurements yet"),
    "s": ("not synchronised", "reports that it is not synchronised itself"),
    "d": ("too far", "its root distance is larger than maxdistance"),
    "~": ("too jittery", "its jitter is larger than maxjitter"),
    "w": ("waiting", "waits for other sources to collect enough measurements"),
    "S": ("stale", "its measurements are older than the other sources'"),
    "O": ("orphan", "its stratum is at or above the orphan stratum"),
    "T": ("disagrees with trusted", "does not fully agree with the sources marked trust (GPS)"),
    "x": ("falseticker", "does not agree with the other sources"),
    "W": ("waiting for others", "selectable, but waits for other sources (minsources or require)"),
    "P": ("not preferred", "selectable, but a source with the prefer option is used instead"),
    "U": ("waiting for update", "selectable, waits for a new measurement after a reselection"),
    "D": ("too far to combine", "selectable, but its root distance is too large to combine (combinelimit)"),
    "+": ("combined", "used: combined with the best source"),
    "*": ("selected", "used: the best source, it steers the clock"),
}


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
    full = len(parts) >= 14
    return ChronyTracking(
        refid=parts[0], refname=parts[1], stratum=_i(parts[2]), system_offset_s=_f(parts[4]), last_offset_s=_f(parts[5]),
        rms_s=_f(parts[6]), leap=parts[-1] if full else "",
        ref_time=_f(parts[3]) if full else None, freq_ppm=_f(parts[7]) if full else None, resid_freq_ppm=_f(parts[8]) if full else None,
        skew_ppm=_f(parts[9]) if full else None, root_delay_s=_f(parts[10]) if full else None,
        root_dispersion_s=_f(parts[11]) if full else None, update_interval_s=_f(parts[12]) if full else None,
    )


def _rows(text: str, n: int) -> list[list[str]]:
    return [p for p in (line.strip().split(",") for line in text.splitlines()) if len(p) >= n and p[0] != ""]


def parse_sourcestats(text: str) -> list[ChronySourceStats]:
    return [
        ChronySourceStats(name=p[0], samples=_i(p[1]), runs=_i(p[2]), span_s=_i(p[3]), freq_ppm=_f(p[4]), skew_ppm=_f(p[5]),
                          offset_s=_f(p[6]), std_dev_s=_f(p[7]))
        for p in _rows(text, 8)
    ]


def parse_selectdata(text: str) -> list[ChronySelect]:
    # S, name, auth, 5 configured option letters, 5 effective option letters, last, score, lo, hi, leap
    return [
        ChronySelect(state=p[0], name=p[1], authenticated=p[2] == "Y", options="".join(x for x in p[3:8] if x != "-"),
                     effective="".join(x for x in p[8:13] if x != "-"), last_sample_s=_i(p[13]), score=_f(p[14]),
                     interval_lo_s=_f(p[15]), interval_hi_s=_f(p[16]), leap=p[17])
        for p in _rows(text, 18)
    ]


def parse_ntpdata(text: str) -> list[ChronyNtpData]:
    # remote addr, refid, port, local addr, refid, leap, version, mode, stratum, poll, poll s, precision, precision s,
    # root delay, root dispersion, ref id, ref name, ref time, offset, peer delay, peer dispersion, response time,
    # jitter asymmetry, 3 test fields, interleaved, authenticated, tx ts, rx ts, total tx, rx, valid rx, ...
    return [
        ChronyNtpData(remote=p[0], stratum=_i(p[8]), poll_s=_f(p[10]), root_delay_s=_f(p[13]), root_dispersion_s=_f(p[14]),
                      offset_s=_f(p[18]), peer_delay_s=_f(p[19]), peer_dispersion_s=_f(p[20]), response_time_s=_f(p[21]),
                      tests=" ".join(p[23:26]), total_tx=_i(p[30]), total_rx=_i(p[31]), total_valid_rx=_i(p[32]))
        for p in _rows(text, 33)
    ]


def parse_activity(text: str) -> ChronyActivity | None:
    p = text.strip().split(",")
    if len(p) < 5:
        return None
    return ChronyActivity(*(_i(x) for x in p[:5]))


def reach_count(reach_octal: int) -> int:
    """`sources` prints the 8-bit reach register in octal (377 = the last 8 polls all answered)."""
    try:
        return bin(int(str(reach_octal), 8)).count("1")
    except ValueError:
        return 0


def as_dicts(items: list) -> list[dict]:
    return [asdict(x) for x in items]


@dataclass
class ShmSegment:
    key: int
    unit: int | None  # NTP refclock unit (key 0x4e545030 + unit)
    perms: str
    size: int
    nattch: int
    cpid: int
    lpid: int
    uid: int


NTP_SHM_KEY = 0x4E545030


def parse_sysvipc_shm(text: str) -> list[ShmSegment]:
    """/proc/sysvipc/shm (world-readable): which SysV segments exist and how many processes have them attached."""
    out = []
    for line in text.splitlines()[1:]:
        p = line.split()
        if len(p) < 8:
            continue
        try:
            key = int(p[0]) & 0xFFFFFFFF
            unit = key - NTP_SHM_KEY if 0 <= key - NTP_SHM_KEY < 16 else None
            out.append(ShmSegment(key=key, unit=unit, perms=p[2], size=int(p[3]), cpid=int(p[4]), lpid=int(p[5]), nattch=int(p[6]), uid=int(p[7])))
        except ValueError:
            continue
    return out


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
