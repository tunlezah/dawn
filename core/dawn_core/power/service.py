"""PowerService: cool mode's thin service around the pure planner (power/cool.py).

Every 5 s it gathers what the planner needs, runs it, and applies the plan as desired state, idempotently, on every
tick: the decoder stopped or running, the CPU cap written or lifted, the face's animations frozen or not, the polling
rates. A crash or a restart therefore converges by itself; at start the plan is "not cool" and the decoder is started
if it is not running. Nothing here touches the alarm engine, the ring, the face's backup alarm, the buzzer or the
watchdog: the alarms are kept safe by the planner wanting the radio back minutes ahead, by any activity (a ring among
them) ending cool mode, and by the alarm's own preparation treating "waking the radio" as a step under way.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import DawnConfig
from ..context import DawnContext
from ..services import Service
from ..tasks import spawn
from .cool import CoolInputs, CoolPlan, CoolPlanner

log = logging.getLogger("dawn.power")

TICK_S = 5.0
CPUFREQ = Path("/sys/devices/system/cpu/cpufreq/policy0")
DAB_PAGE_S = 60.0  # the DAB diagnostics page polled this recently counts as open


def cool_plan(ctx: DawnContext) -> CoolPlan | None:
    """The plan in force (None when there is no power service): other services read their polling rates from it."""
    try:
        return ctx.svc(PowerService).plan
    except KeyError:
        return None


class PowerService(Service):
    name = "power"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self.planner = CoolPlanner(ctx.config.power, ctx.config.alarm_defaults.prepare_minutes)
        self.plan = CoolPlan()  # not cool until the first tick says otherwise
        self._task: asyncio.Task[None] | None = None
        self._first = True
        self._started = time.monotonic()
        self._samples: int | None = None
        self._sample_at: float | None = None
        self._cap_written: int | None = None
        self._cap_error_logged = False
        self._cool_day: str | None = None
        self._cool_today_s = 0.0
        self._last_tick: float | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = spawn(self._loop(), name="power")
        self.ctx.liveness.register("power", max_age_s=6 * TICK_S + 30, restart=self.restart_loop)
        self.ctx.liveness.task("power", self._task)

    async def restart_loop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = spawn(self._loop(), name="power")
        self.ctx.liveness.task("power", self._task)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        self._write_cap(None)  # leave the CPU uncapped (the unit's ExecStopPost does it too)

    async def on_config(self, old: DawnConfig, new: DawnConfig) -> None:
        if old.power != new.power or old.alarm_defaults.prepare_minutes != new.alarm_defaults.prepare_minutes:
            hot = self.planner.hot
            self.planner = CoolPlanner(new.power, new.alarm_defaults.prepare_minutes)
            self.planner.hot = hot

    async def _loop(self) -> None:
        while True:
            self.ctx.liveness.beat("power")
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("power tick failed")
            await asyncio.sleep(TICK_S)

    # ---- one tick ----------------------------------------------------------------------------------------------------
    async def tick(self, now: float | None = None, wall: datetime | None = None) -> CoolPlan:
        now = time.monotonic() if now is None else now
        wall = self.ctx.store.now() if wall is None else wall
        inputs = self.inputs(now, wall)
        plan = self.planner.update(now, wall, inputs)
        changed = plan.cool != self.plan.cool
        self.plan = plan
        if changed:
            log.info("cool mode %s: %s", "on" if plan.cool else "off", plan.reason)
            self.ctx.db.log_event("cool_mode", on=plan.cool, reason=plan.reason)
        await self._apply(plan)
        self._account(now, wall, plan)
        self._publish(plan, inputs)
        self._first = False
        return plan

    def inputs(self, now: float, wall: datetime) -> CoolInputs:
        st = self.ctx.store.state
        lat, _lon, _src = self.ctx.position()
        dab = self._dab()
        hub = self.ctx.ws_hub
        last = max(self.ctx.last_activity, self._started)
        if hub is not None:
            # an open control UI (a phone, a laptop) is someone using the clock: its page pings every 15 s
            for info in hub.info.values():
                if info.get("role") != "face":
                    last = max(last, float(info.get("last_seen") or 0.0))
        nxt = st.alarms.next
        next_at = None
        if nxt is not None:
            try:
                next_at = min(datetime.fromisoformat(t) for t in (nxt.at, nxt.light_wake_at) if t)
            except ValueError:
                next_at = None
        next_dab_wake = None
        try:
            from ..alarms.service import AlarmService

            next_dab_wake = self.ctx.svc(AlarmService).next_dab_wake
        except (ImportError, KeyError):
            pass
        return CoolInputs(
            soc_temp_c=st.system.cpu_temp_c, month=wall.month, latitude=lat,
            forecast_t_max=st.weather.t_max if st.weather.available else None,
            last_activity=last, busy=self._busy(wall), next_alarm_at=next_at, next_dab_alarm_at=next_dab_wake,
            scan_running=bool(dab and dab._scan_task and not dab._scan_task.done()),
            dab_in_slot=self._dab_in_slot(), dab_page_open=bool(dab and dab.page_polled_at and now - dab.page_polled_at < DAB_PAGE_S),
            ntp_live=any(s.kind == "ntp" and s.live for s in st.time_sources.sources),
            gps_live=any(s.kind == "gps" and s.live for s in st.time_sources.sources),
            dab_last_sample=self._dab_sample(now),
        )

    def _busy(self, wall: datetime) -> str | None:
        st = self.ctx.store.state
        if st.alarms.ringing is not None:
            return "an alarm is ringing or snoozed"
        if st.alarms.light_wake_active:
            return "a light wake is on"
        if st.timers.nap is not None or st.timers.sleep is not None:
            return "a timer is running"
        if st.audio.active_source != "none" or st.airplay.playing or st.bluetooth.playing:
            return "something is playing"
        if st.face.menu_open:
            return "the menu is open"
        if st.face.wake_until:
            try:
                if datetime.fromisoformat(st.face.wake_until) > wall:
                    return "the face was woken"
            except ValueError:
                pass
        return None

    def _dab(self) -> Any:
        if not self.ctx.config.dab.enabled:
            return None
        try:
            from ..dab.service import DabService

            return self.ctx.svc(DabService)
        except (ImportError, KeyError):
            return None

    def _dab_in_slot(self) -> bool:
        try:
            from ..audio.service import AudioService

            arb = self.ctx.svc(AudioService).arbiter
            return any(s is not None and s.source.kind == "dab" for s in (arb.slot(lv) for lv in ("alarm", "sleep", "user")))
        except (ImportError, KeyError):
            return False

    def _dab_sample(self, now: float) -> float | None:
        """When dawn-timed last wrote a DAB time sample (its status file's counter moved), on our monotonic clock."""
        try:
            n = int(json.loads(Path(self.ctx.config.diagnostics.timed_status_file).read_text()).get("samples_written") or 0)
        except (OSError, ValueError, TypeError):
            return self._sample_at
        if self._samples is not None and n != self._samples:
            self._sample_at = now
        self._samples = n
        return self._sample_at

    # ---- applying the plan ------------------------------------------------------------------------------------------
    async def _apply(self, plan: CoolPlan) -> None:
        dab = self._dab()
        if dab is not None:
            try:
                if plan.dab_wanted:
                    if dab.paused or self._first:
                        # paused by us, or the first tick after a start (a crash while it was stopped): bring it back
                        dab.wake(force_start=self._first)
                elif not dab.paused:
                    await dab.pause("cool mode")
            except Exception:  # noqa: BLE001
                log.exception("cool mode: the decoder could not be paused or woken")
        self._write_cap(plan.cpu_cap_mhz)
        disp = self.ctx.store.state.display
        if disp.cool != plan.freeze:
            disp.cool = plan.freeze
            self.ctx.store.touch()

    def _write_cap(self, mhz: int | None) -> None:
        """scaling_max_freq as wanted (kHz): the cap, never below cpuinfo_min_freq, or cpuinfo_max_freq for none. Written
        only when it differs; dawn-core may write it thanks to a tmpfiles rule (deploy/tmpfiles.d/dawn-cpufreq.conf)."""
        if self.ctx.sim:
            return
        try:
            hi = int((CPUFREQ / "cpuinfo_max_freq").read_text())
            lo = int((CPUFREQ / "cpuinfo_min_freq").read_text())
            want = hi if mhz is None else max(lo, min(hi, mhz * 1000))
            cur = int((CPUFREQ / "scaling_max_freq").read_text())
            if cur != want:
                (CPUFREQ / "scaling_max_freq").write_text(str(want))
                log.info("CPU frequency cap %s", f"{want // 1000} MHz" if mhz else "lifted")
            self._cap_written = None if mhz is None else want // 1000
        except (OSError, ValueError) as e:
            if not self._cap_error_logged:
                self._cap_error_logged = True
                log.warning("cannot set the CPU frequency cap (%s); cool mode carries on without it", e)

    def _account(self, now: float, wall: datetime, plan: CoolPlan) -> None:
        day = wall.date().isoformat()
        if day != self._cool_day:
            self._cool_day, self._cool_today_s = day, 0.0
        if self._last_tick is not None and plan.cool:
            self._cool_today_s += min(now - self._last_tick, 60.0)
        self._last_tick = now

    def _publish(self, plan: CoolPlan, i: CoolInputs) -> None:
        p = self.ctx.store.state.power
        dab = self._dab()
        p.mode = self.ctx.config.power.cool_mode
        p.cool, p.reason, p.hot = plan.cool, plan.reason, plan.hot
        p.dab_paused = bool(dab and dab.paused)
        p.dab_reason = plan.dab_reason
        p.cpu_cap_mhz = self._cap_written
        p.cool_today_s = int(self._cool_today_s)
        self.ctx.store.touch()
