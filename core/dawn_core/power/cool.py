"""Cool mode: a pure planner, walked minute by minute in the tests like the sleep planner.

On a hot day, while nothing much is happening, the Pi runs cooler: the DAB decoder is stopped (the stick streams
2 MS/s and welle-cli demodulates around the clock, playing or not), the CPU is capped, the face's animations freeze,
and the polling slows down. The alarms are never affected: the decoder is running again `dab_wake_lead_minutes`
before any alarm on DAB starts its preparation, the CPU cap comes off when any alarm's preparation starts, and any
activity (a touch, the controls, something playing, a ring, the web UI) ends cool mode at once.

`auto` cools when the box is idle and (it is summer, or a hot day is forecast, or the SoC is hot); `on` whenever idle;
`off` never. The planner holds no I/O: PowerService feeds it what it saw and applies the plan, every tick, as desired
state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..config.schema import PowerConfig


@dataclass(frozen=True)
class CoolInputs:
    soc_temp_c: float | None = None
    month: int = 1
    latitude: float = -35.0
    forecast_t_max: float | None = None
    last_activity: float = 0.0  # monotonic time of the last activity (an event: touch, controls, a write to the API)
    busy: str | None = None  # something going on right now (playing, ringing, a nap, the menu, a web client): why
    next_alarm_at: datetime | None = None  # the next alarm (its light wake if earlier), any source
    next_alarm_dab: bool = False  # ...and whether that alarm's sound is DAB (last-played resolved)
    next_dab_alarm_at: datetime | None = None  # the next alarm whose source is DAB (its light wake if earlier)
    scan_running: bool = False
    dab_in_slot: bool = False  # a DAB source in any audio arbiter slot
    dab_page_open: bool = False  # Diagnostics' DAB live page polled in the last minute
    ntp_live: bool = False
    gps_live: bool = False
    dab_last_sample: float | None = None  # monotonic time dawn-timed last wrote a time sample


@dataclass(frozen=True)
class CoolPlan:
    cool: bool = False
    reason: str = "starting"
    dab_wanted: bool = True
    dab_reason: str = "not cool"
    cpu_cap_mhz: int | None = None
    freeze: bool = False
    lux_hz: float | None = None  # None: the configured rate
    poll_scale: float = 1.0
    hot: bool = False


@dataclass
class CoolPlanner:
    cfg: PowerConfig
    prepare_minutes: int = 5
    hot: bool = False
    _hot_since: float | None = None
    _cool_since: float | None = None
    _sync_started: float | None = None  # a DAB time-sync window that is running
    _last_sync: float | None = None  # when the last window ended (monotonic)
    plan: CoolPlan = field(default_factory=CoolPlan)

    # ---- pieces -----------------------------------------------------------------------------------------------
    def _summer(self, i: CoolInputs) -> bool:
        months = self.cfg.summer
        if months == "auto":
            months = [12, 1, 2] if i.latitude < 0 else [6, 7, 8]
        return i.month in months

    def _update_hot(self, now: float, temp: float | None) -> None:
        """Hot after enter_temp_c held for hold_s; cool again after below exit_temp_c for exit_hold_s."""
        c = self.cfg
        if temp is None:
            self._hot_since = self._cool_since = None
            return
        if not self.hot:
            self._cool_since = None
            if temp >= c.enter_temp_c:
                self._hot_since = now if self._hot_since is None else self._hot_since
                if now - self._hot_since >= c.hold_s:
                    self.hot = True
            else:
                self._hot_since = None
        else:
            self._hot_since = None
            if temp < c.exit_temp_c:
                self._cool_since = now if self._cool_since is None else self._cool_since
                if now - self._cool_since >= c.exit_hold_s:
                    self.hot = False
            else:
                self._cool_since = None

    def _time_sync(self, now: float, i: CoolInputs) -> str | None:
        """With no live NTP or GPS time, the decoder runs every dab_time_sync_every_h for dab_time_sync_minutes (or until
        dawn-timed wrote a sample) so the clock still gets DAB time. Why it must run now, or None."""
        if i.ntp_live or i.gps_live:
            self._sync_started = None
            return None
        c = self.cfg
        if self._sync_started is not None:
            done = (i.dab_last_sample is not None and i.dab_last_sample >= self._sync_started + 5) or \
                now - self._sync_started >= c.dab_time_sync_minutes * 60
            if done:
                self._sync_started, self._last_sync = None, now
                return None
            return "DAB time sync (no NTP or GPS)"
        if self._last_sync is None or now - self._last_sync >= c.dab_time_sync_every_h * 3600:
            self._sync_started = now
            return "DAB time sync (no NTP or GPS)"
        return None

    # ---- the plan -------------------------------------------------------------------------------------------------
    def update(self, now: float, wall: datetime, i: CoolInputs) -> CoolPlan:
        c = self.cfg
        self._update_hot(now, i.soc_temp_c)
        idle_for = now - i.last_activity
        if c.cool_mode == "off":
            cool, reason = False, "cool mode is off"
        elif i.busy:
            cool, reason = False, i.busy
        elif idle_for < c.idle_after_s:
            cool, reason = False, f"in use {idle_for:.0f} s ago"
        elif c.cool_mode == "on":
            cool, reason = True, "cool mode is on"
        else:
            why = [w for w, yes in (("summer", self._summer(i)),
                                    (f"forecast {i.forecast_t_max:.0f} °C" if i.forecast_t_max is not None else "", i.forecast_t_max is not None and i.forecast_t_max >= c.hot_day_c),
                                    (f"SoC {i.soc_temp_c:.0f} °C" if i.soc_temp_c is not None else "", self.hot)) if yes]
            cool, reason = (True, ", ".join(why)) if why else (False, "not a hot day")
        prep = timedelta(minutes=self.prepare_minutes)
        in_prep = i.next_alarm_at is not None and i.next_alarm_at - wall <= prep + timedelta(minutes=1)
        # ---- the decoder ---------------------------------------------------------------------------------------
        dab_lead = prep + timedelta(minutes=c.dab_wake_lead_minutes + 1)
        sync_why = self._time_sync(now, i) if cool and c.pause_dab else None
        if not cool:
            dab_wanted, dab_reason = True, "not cool"
            self._sync_started = None
        elif not c.pause_dab:
            dab_wanted, dab_reason = True, "pausing the radio is off"
        elif i.next_dab_alarm_at is not None and i.next_dab_alarm_at - wall <= dab_lead:
            dab_wanted, dab_reason = True, "an alarm on the radio is coming"
        elif i.dab_in_slot:
            dab_wanted, dab_reason = True, "the radio is in use"
        elif i.scan_running:
            dab_wanted, dab_reason = True, "a scan is running"
        elif i.dab_page_open:
            dab_wanted, dab_reason = True, "the DAB diagnostics page is open"
        elif sync_why:
            dab_wanted, dab_reason = True, sync_why
        else:
            dab_wanted, dab_reason = False, "paused (cool mode)"
        # ---- the rest ------------------------------------------------------------------------------------------
        cap = c.cpu_max_mhz if cool and c.cpu_max_mhz > 0 and not in_prep else None
        self.plan = CoolPlan(
            cool=cool, reason=reason, dab_wanted=dab_wanted, dab_reason=dab_reason, cpu_cap_mhz=cap,
            freeze=cool and c.freeze_animations, lux_hz=c.lux_sample_hz if cool else None,
            poll_scale=2.0 if cool and not in_prep else 1.0, hot=self.hot,
        )
        return self.plan
