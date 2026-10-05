"""Signal and timing history for the Diagnostics graphs.

Sampled every 10 s from what the services already hold (no extra hardware access), folded into
one-minute buckets (average, minimum, maximum) and written as one SQLite row a minute, so a week
is ~10 000 small rows and an overnight problem can still be looked at after a reboot.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import delete, select

from ..db.engine import Database
from ..db.models import MetricRow

log = logging.getLogger("dawn.diag.history")

# key -> (label, unit). Keys ending in .sync/.synced/.online/.flowing are fractions shown as %.
METRICS: dict[str, tuple[str, str]] = {
    "dab.snr": ("DAB SNR", "dB"),
    "dab.sync": ("DAB ensemble decoded", "%"),
    "dab.fic": ("DAB data-channel errors", "/min"),
    "dab.err": ("DAB decode errors (station playing)", "/min"),
    "dab.freq": ("DAB frequency correction", "Hz"),
    "dab.gain": ("Tuner gain", "dB"),
    "gps.used": ("Satellites used", ""),
    "gps.seen": ("Satellites in view", ""),
    "gps.snr": ("GPS signal (mean of used)", "dBHz"),
    "gps.fix": ("GPS fix (2 = 2D, 3 = 3D)", ""),
    "gps.hdop": ("GPS HDOP", ""),
    "gps.toff": ("GPS time arrives after the second", "ms"),
    "time.system": ("System clock offset", "ms"),
    "time.gps": ("GPS source offset", "ms"),
    "time.dab": ("DAB source offset", "ms"),
    "time.ntp": ("NTP source offset", "ms"),
    "time.synced": ("Clock synchronised", "%"),
    "net.online": ("Online", "%"),
    "net.wifi": ("Wi-Fi signal", "dBm"),
    "sys.temp": ("CPU temperature", "°C"),
    "sys.load": ("Load", ""),
    "sys.mem": ("Memory used", "%"),
    "display.lux": ("Room light", "lx"),
    "display.backlight": ("Backlight", "%"),
    "audio.flowing": ("Audio flowing while playing", "%"),
}
FRACTIONS = {k for k in METRICS if k.rsplit(".", 1)[1] in ("sync", "synced", "online", "flowing")}


class History:
    def __init__(self, db: Database):
        self.db = db
        self._minute: int | None = None  # epoch minute of the open bucket
        self._acc: dict[str, list[float]] = {}  # key -> [sum, n, min, max]

    # ---- sampling ----------------------------------------------------------
    def add(self, sample: dict[str, float | None], now: float | None = None) -> bool:
        """Fold one sample in; returns True when a finished minute was written."""
        now = now if now is not None else time.time()
        minute = int(now // 60)
        wrote = False
        if self._minute is not None and minute != self._minute and self._acc:
            self._write(self._minute, self._acc)
            wrote = True
            self._acc = {}
        self._minute = minute
        for k, v in sample.items():
            if v is None or k not in METRICS:
                continue
            v = float(v) * (100.0 if k in FRACTIONS else 1.0)
            a = self._acc.get(k)
            if a is None:
                self._acc[k] = [v, 1, v, v]
            else:
                a[0] += v
                a[1] += 1
                a[2] = min(a[2], v)
                a[3] = max(a[3], v)
        return wrote

    @staticmethod
    def _fold(acc: dict[str, list[float]]) -> dict[str, list[float]]:
        return {k: [round(a[0] / a[1], 3), round(a[2], 3), round(a[3], 3)] for k, a in acc.items() if a[1]}

    def _write(self, minute: int, acc: dict[str, list[float]]) -> None:
        at = datetime.fromtimestamp(minute * 60, UTC)
        try:
            with self.db.session() as s:
                s.add(MetricRow(at=at, data=json.dumps(self._fold(acc), separators=(",", ":"))))
                s.commit()
        except Exception:  # noqa: BLE001
            log.exception("cannot write history row")

    def prune(self, days: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        with self.db.session() as s:
            n = s.exec(delete(MetricRow).where(MetricRow.at < cutoff)).rowcount  # type: ignore[arg-type,attr-defined]
            s.commit()
        return int(n or 0)

    # ---- queries -----------------------------------------------------------
    def series(self, keys: list[str], hours: float, points: int = 240, now: float | None = None) -> dict[str, Any]:
        """{key: {label, unit, points: [[iso, avg, min, max], ...]}} over the last `hours`, in at most `points` buckets.
        The minute in progress is included, so a fresh page has something to show."""
        now = now if now is not None else time.time()
        start = now - hours * 3600
        keys = [k for k in keys if k in METRICS]
        with self.db.session() as s:
            rows = s.exec(select(MetricRow).where(MetricRow.at >= datetime.fromtimestamp(start, UTC)).order_by(MetricRow.at)).all()  # type: ignore[arg-type]
        minutes: list[tuple[float, dict[str, list[float]]]] = []
        for r in rows:
            at = r.at if r.at.tzinfo else r.at.replace(tzinfo=UTC)
            try:
                minutes.append((at.timestamp(), json.loads(r.data)))
            except ValueError:
                continue
        if self._minute is not None and self._acc:
            minutes.append((self._minute * 60.0, self._fold(self._acc)))
        width = max(60.0, hours * 3600 / max(1, points))
        out: dict[str, Any] = {}
        for k in keys:
            buckets: dict[int, list[float]] = {}
            for t, data in minutes:
                v = data.get(k)
                if not v:
                    continue
                b = int((t - start) // width)
                cur = buckets.get(b)
                if cur is None:
                    buckets[b] = [v[0], 1, v[1], v[2]]
                else:
                    cur[0] += v[0]
                    cur[1] += 1
                    cur[2] = min(cur[2], v[1])
                    cur[3] = max(cur[3], v[2])
            pts = [[datetime.fromtimestamp(start + (b + 0.5) * width, UTC).isoformat(timespec="seconds"), round(c[0] / c[1], 3), c[2], c[3]]
                   for b, c in sorted(buckets.items())]
            label, unit = METRICS[k]
            out[k] = {"label": label, "unit": unit, "points": pts}
        return out
