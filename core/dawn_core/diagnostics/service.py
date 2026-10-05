"""DiagnosticsService: runs the checks, keeps the history, and publishes a short problem summary.

Checks run in the background every `diagnostics.check_interval_s` (and on demand from the page);
the history is sampled every 10 s from what the other services already hold.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..config import DawnConfig
from ..context import DawnContext
from ..services import Service
from ..state.ui import DiagnosticsSummary, DiagProblem
from . import dab, gps, network, system, timepath
from .checks import AREAS, Check, summarise
from .history import History
from .host import Host, make_host
from .network import parse_wireless

log = logging.getLogger("dawn.diag")
SAMPLE_S = 10


def _broken(area: str, e: BaseException) -> Check:
    return Check(f"{area}.diagnostics", area, "Diagnostics", "warn", f"Could not run these checks: {type(e).__name__}: {e}",
                 "This is a bug in the checks themselves; the log has the details.")


class DiagnosticsService(Service):
    name = "diagnostics"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self.host: Host = make_host(ctx)
        self.history = History(ctx.db)
        self._tasks: list[asyncio.Task[None]] = []
        self._lock = asyncio.Lock()
        self.report: dict[str, Any] | None = None
        self._report_at = 0.0
        self._wifi_dbm: float | None = None
        self._problems: dict[str, Check] = {}  # last published, to log what appears and clears

    async def start(self) -> None:
        try:
            n = await asyncio.to_thread(self.history.prune, self.ctx.config.diagnostics.history_days)
            if n:
                log.info("history: pruned %d rows older than %d days", n, self.ctx.config.diagnostics.history_days)
        except Exception:  # noqa: BLE001
            log.exception("history prune failed")
        self._tasks = [asyncio.create_task(self._sample_loop(), name="diag-sample"), asyncio.create_task(self._check_loop(), name="diag-checks")]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await self.host.close()

    async def on_config(self, old: DawnConfig, new: DawnConfig) -> None:
        if old.diagnostics.history_days > new.diagnostics.history_days:
            await asyncio.to_thread(self.history.prune, new.diagnostics.history_days)

    # ---- checks ------------------------------------------------------------
    async def _check_loop(self) -> None:
        await asyncio.sleep(20)  # let the other services settle after boot
        while True:
            try:
                await self.run(max_age_s=0)
            except Exception:  # noqa: BLE001
                log.exception("diagnostics run failed")
            await asyncio.sleep(self.ctx.config.diagnostics.check_interval_s)

    async def collect(self) -> dict[str, Any]:
        ctx, host = self.ctx, self.host
        names = ["dab", "gps", "network", "system"]
        got = await asyncio.gather(dab.collect(ctx, host), gps.collect(ctx, host), network.collect(ctx, host), system.collect(ctx, host),
                                   return_exceptions=True)
        facts: dict[str, Any] = dict(zip(names, got, strict=True))
        try:
            g = facts["gps"] if not isinstance(facts["gps"], BaseException) else {"enabled": False, "detail": {}, "unit": None}
            d = facts["dab"] if not isinstance(facts["dab"], BaseException) else {"enabled": False, "mux": None}
            online = facts["network"]["online"] if not isinstance(facts["network"], BaseException) else ctx.store.state.system.network.online
            facts["time"] = await timepath.collect(ctx, host, g, d, online)
        except Exception as e:  # noqa: BLE001
            facts["time"] = e
        return facts

    @staticmethod
    def build_checks(facts: dict[str, Any]) -> list[Check]:
        out: list[Check] = []
        for area, mod in (("dab", dab), ("gps", gps), ("time", timepath), ("network", network), ("system", system)):
            f = facts.get(area)
            if isinstance(f, BaseException):
                log.error("diagnostics: collecting %s failed: %r", area, f)
                out.append(_broken(area, f))
                continue
            try:
                out += mod.checks(f)  # type: ignore[attr-defined]
            except Exception as e:  # noqa: BLE001
                log.exception("diagnostics: %s checks failed", area)
                out.append(_broken(area, e))
        return sorted(out, key=lambda c: AREAS.index(c.area) if c.area in AREAS else 99)

    async def run(self, max_age_s: float = 3.0) -> dict[str, Any]:
        async with self._lock:
            if self.report is not None and time.monotonic() - self._report_at < max_age_s:
                return self.report
            facts = await self.collect()
            checks = self.build_checks(facts)
            s = summarise(checks)
            self.report = {
                "generated_at": self.ctx.store.iso(), "counts": s["counts"], "checks": [c.as_dict() for c in checks],
                "facts": {k: v for k, v in facts.items() if not isinstance(v, BaseException)},
            }
            self._report_at = time.monotonic()
            self._publish(s["problems"])
            return self.report

    def invalidate(self) -> None:
        """After a fix action: the next report re-checks instead of serving the cached one."""
        self._report_at = 0.0

    async def live(self, area: str) -> dict[str, Any]:
        """One area right now, without the rest (the DAB antenna meter polls this every second)."""
        mod = {"dab": dab, "gps": gps}[area]
        f = await mod.collect(self.ctx, self.host)
        return {"generated_at": self.ctx.store.iso(), "facts": f, "checks": [c.as_dict() for c in mod.checks(f)]}

    def _publish(self, problems: list[Check]) -> None:
        # the journal gets a line when a problem appears or clears, so an overnight fault can be found afterwards
        now = {p.id: p for p in problems}
        for pid, p in now.items():
            if pid not in self._problems:
                log.warning("problem: %s (%s): %s", p.title, pid, p.detail)
        for pid in self._problems.keys() - now.keys():
            log.info("cleared: %s", pid)
        self._problems = now
        st = self.ctx.store.state
        st.diagnostics = DiagnosticsSummary(
            updated_at=self.ctx.store.iso(), fail=sum(1 for p in problems if p.status == "fail"), warn=sum(1 for p in problems if p.status == "warn"),
            problems=[DiagProblem(id=p.id, area=p.area, title=p.title, status=p.status, detail=p.detail) for p in problems[:8]],  # type: ignore[arg-type]
        )
        self.ctx.store.touch()

    # ---- history -----------------------------------------------------------
    async def _sample_loop(self) -> None:
        while True:
            try:
                sample = await self.sample()
                await asyncio.to_thread(self.history.add, sample)
            except Exception:  # noqa: BLE001
                log.exception("history sample failed")
            await asyncio.sleep(SAMPLE_S)

    async def sample(self) -> dict[str, float | None]:
        st = self.ctx.store.state
        out: dict[str, float | None] = {}
        now = time.time()
        try:
            from ..dab.service import DabService

            d = self.ctx.svc(DabService)
            m = d.last_mux if d.last_mux_at and now - d.last_mux_at < 20 else None
            if m is not None:
                out["dab.sync"] = 1.0 if m.sync else 0.0
                if m.sync:
                    out.update({"dab.snr": m.snr, "dab.freq": m.freq_correction_hz, "dab.fic": d.fic_errors_per_min})
                out["dab.gain"] = m.gain_db
            np = st.now_playing
            r = d.error_rates.get(np.station_sid or "") if np.source == "dab" else None
            if r and now - r["at"] < 120:
                out["dab.err"] = r["frame"] + r["rs"] + r["aac"]
        except KeyError:
            pass
        try:
            from ..timesync.service import TimeSourceService

            g = self.ctx.svc(TimeSourceService).gps
            if g is not None and g.connected:
                fx = g.fix
                out.update({"gps.used": fx.sats_used, "gps.seen": fx.sats_seen, "gps.snr": fx.snr_summary()[0], "gps.fix": fx.mode,
                            "gps.hdop": fx.hdop if fx.hdop is not None and fx.hdop < 50 else None})
                if fx.toff_ms is not None and fx.toff_at and now - fx.toff_at < 30:
                    out["gps.toff"] = -fx.toff_ms
        except KeyError:
            pass
        ts = st.time_sources
        if ts.chrony_available:
            out["time.synced"] = 1.0 if ts.synced else 0.0
            out["time.system"] = ts.system_offset_ms
            for kind in ("gps", "dab"):
                src = next((s for s in ts.sources if s.kind == kind and s.live), None)
                out[f"time.{kind}"] = src.offset_ms if src else None
            ntp = [s.offset_ms for s in ts.sources if s.kind == "ntp" and s.live and s.offset_ms is not None]
            out["time.ntp"] = min(ntp, key=abs) if ntp else None
        out["net.online"] = 1.0 if st.system.network.online else 0.0
        w = parse_wireless(await self.host.read("/proc/net/wireless") or "")
        out["net.wifi"] = next(iter(w.values()))["level_dbm"] if w else None
        out.update({"sys.temp": st.system.cpu_temp_c, "sys.load": st.system.load1, "sys.mem": st.system.mem_used_percent,
                    "display.lux": st.display.lux, "display.backlight": st.display.brightness})
        if st.audio.active_source != "none":
            out["audio.flowing"] = 1.0 if (st.audio.audio_flowing or st.airplay.playing or st.bluetooth.playing) else 0.0
        return out

