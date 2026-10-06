"""When the clock is asleep: a pure, event-driven planner (unit-tested like the brightness curve).

Going to sleep (each switch on its own, combined with OR):
  - the bedtime passes (`start`), whatever the light, or
  - the room goes dark (light sensor below `dark_lux` for `dark_after_s`).
Waking (whichever comes first):
  - the morning time passes (`end`),
  - `alarm_lead_minutes` before the next alarm or its light-wake,
  - the room gets bright (above `bright_lux` for `bright_after_s`),
  - anything rings (an alarm or a nap).
After a morning, alarm or ring wake the dark trigger waits until the room has been bright again, so a dark
winter morning does not put the face straight back to sleep. A light switched on in the night wakes it, and
while still inside the bedtime window it goes back to sleep when the room is dark again.

"Asleep" is what the planner wants; the face only shows the sleep clock in Standby (audio playing keeps the
player up until it stops, and ringing, light-wake, a nap countdown, setup and messages all take precedence).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal

Room = Literal["dark", "bright", "between"]


def parse_hhmm(v: str) -> time:
    h, m = v.strip().split(":")
    return time(int(h), int(m))


@dataclass
class SleepSettings:
    start_at_time: bool = True
    start: time = time(22, 30)
    start_when_dark: bool = True
    end_at_time: bool = True
    end: time = time(6, 30)
    end_before_alarm: bool = True
    alarm_lead_minutes: int = 10
    end_when_bright: bool = True
    dark_lux: float = 3.0
    dark_after_s: float = 60.0
    bright_lux: float = 30.0
    bright_after_s: float = 20.0


def local_at(day: datetime, t: time) -> datetime:
    """`t` on `day`'s date in `day`'s zone; a time in a DST gap lands an hour later (as alarms do)."""
    naive = datetime.combine(day.date(), t)
    return naive.replace(tzinfo=day.tzinfo).astimezone(UTC).astimezone(day.tzinfo)


def last_at(now: datetime, t: time) -> datetime:
    """The latest occurrence of local time `t` at or before `now` (compared as instants, so DST cannot fool it)."""
    for back in (0, 1, 2):
        occ = local_at(now - timedelta(days=back), t)
        if occ.timestamp() <= now.timestamp():
            return occ
    return local_at(now - timedelta(days=2), t)


def next_at(now: datetime, t: time) -> datetime:
    for ahead in (0, 1, 2):
        occ = local_at(now + timedelta(days=ahead), t)
        if occ.timestamp() > now.timestamp():
            return occ
    return local_at(now + timedelta(days=2), t)


def in_window(now: datetime, start: time, end: time) -> bool:
    """Inside [start, end): the last bedtime is more recent than the last morning (works across midnight)."""
    if start == end:
        return False
    return last_at(now, start).timestamp() > last_at(now, end).timestamp()


class SleepPlanner:
    """All internal timing is in Unix seconds: two aware datetimes in the same zone subtract as wall-clock
    times in Python, which would make every DST change look like a one-hour clock step."""

    def __init__(self) -> None:
        self.asleep = False
        self.reason: str | None = None  # schedule | dark | manual
        self.dark_armed = True
        self.room: Room | None = None
        self._since: dict[str, float] = {}  # light condition -> when it started to hold
        self._last: float | None = None
        self._bright_woken = False
        self._bedtime_pending = False  # the bedtime passed while something was ringing
        self._handled_alarm: float | None = None
        self.events: list[tuple[str, str]] = []  # (what, why) since the caller last drained them

    # ---- transitions -------------------------------------------------------
    def _sleep(self, why: str) -> None:
        self._bright_woken = False
        if not self.asleep:
            self.asleep, self.reason = True, why
            self.events.append(("sleep", why))

    def _wake(self, why: str, disarm: bool) -> None:
        if disarm:
            self.dark_armed = False
            self._bright_woken = False
        if self.asleep:
            self.asleep, self.reason = False, None
            self.events.append(("wake", why))

    def manual(self, on: bool) -> None:
        """Sleep now / wake now from the web UI. A manual wake holds off the dark trigger until it is light."""
        if on:
            self._sleep("manual")
        else:
            self._wake("manual", disarm=True)

    # ---- the room ----------------------------------------------------------
    def _held(self, key: str, cond: bool, t: float) -> float:
        """How long `cond` has held without a break (-1 when it does not hold now)."""
        if not cond:
            self._since.pop(key, None)
            return -1.0
        return t - self._since.setdefault(key, t)

    def _room(self, s: SleepSettings, t: float, lux: float | None) -> tuple[bool, bool]:
        """(became dark, became bright). Each state is entered and left only after its dwell time, so a hand over
        the sensor or a passing shadow changes nothing. Until the first reading has settled the room is unknown
        (None): settling to dark counts as becoming dark, settling to bright is only where we are."""
        if lux is None:
            self.room = None
            self._since.clear()
            return False, False
        bright_lux = max(s.bright_lux, s.dark_lux)
        dark_for = self._held("dark", lux < s.dark_lux, t)
        bright_for = self._held("bright", lux > bright_lux, t)
        undark_for = self._held("undark", lux >= s.dark_lux, t)
        unbright_for = self._held("unbright", lux <= bright_lux, t)
        before = self.room
        if dark_for >= s.dark_after_s:
            self.room = "dark"
        elif bright_for >= s.bright_after_s:
            self.room = "bright"
        elif before == "bright":
            if unbright_for >= s.bright_after_s:
                self.room = "between"
        elif before == "dark":
            if undark_for >= s.dark_after_s:
                self.room = "between"
        elif before is None and s.dark_lux <= lux <= bright_lux:
            self.room = "between"
        return self.room == "dark" and before != "dark", self.room == "bright" and before not in ("bright", None)

    # ---- one step ----------------------------------------------------------
    def update(self, s: SleepSettings, now: datetime, lux: float | None, next_wake: datetime | None, ringing: bool) -> bool:
        t = now.timestamp()
        became_dark, became_bright = self._room(s, t, lux)
        last, self._last = self._last, t
        # boot, or the clock stepped (chrony at boot): judge the schedule by where we are, not by what was crossed
        resync = last is None or t < last - 5 or t - last > 600
        crossed = (lambda tt: last_at(now, tt).timestamp() > last) if not resync and last is not None else (lambda tt: False)  # noqa: E731
        window = in_window(now, s.start, s.end)

        if self.room == "bright":
            self.dark_armed = True  # it has been light since the last morning: evenings may use the dark trigger again
        if ringing:
            if s.start_at_time and crossed(s.start):
                self._bedtime_pending = True  # an alarm or nap ringing at bedtime does not cancel the bedtime
            self._wake("ring", disarm=True)
            return self.asleep
        bedtime_pending, self._bedtime_pending = self._bedtime_pending, False
        lead = False
        if s.end_before_alarm and next_wake is not None:
            w = next_wake.timestamp()
            lead = w - s.alarm_lead_minutes * 60 <= t < w + 300
            if lead and w != self._handled_alarm:
                self._handled_alarm = w
                self._wake("alarm", disarm=True)
        if s.end_at_time and crossed(s.end):
            self._wake("morning", disarm=True)
        if s.end_when_bright and became_bright:
            self._wake("bright", disarm=False)
            self._bright_woken = s.start_at_time and window
        if resync and self.asleep and self.reason == "schedule" and not window:
            self._wake("resync", disarm=False)  # the clock moved out of the bedtime window
        if lead:
            return self.asleep  # an alarm is minutes away: nothing puts the face (back) to sleep now
        if s.start_at_time and (crossed(s.start) or ((resync or bedtime_pending) and window)):
            self.dark_armed = True
            self._sleep("schedule")
        if became_dark and (s.start_when_dark and self.dark_armed or self._bright_woken and window):
            self._sleep("dark" if s.start_when_dark and self.dark_armed else "schedule")
        return self.asleep

    def next_start(self, s: SleepSettings, now: datetime) -> datetime | None:
        return next_at(now, s.start) if s.start_at_time else None

    def next_end(self, s: SleepSettings, now: datetime, next_wake: datetime | None) -> datetime | None:
        cands = []
        if s.end_at_time:
            cands.append(next_at(now, s.end))
        if s.end_before_alarm and next_wake is not None:
            lead = next_wake - timedelta(minutes=s.alarm_lead_minutes)
            if lead.timestamp() > now.timestamp():  # inside the lead window it is awake already, not "due at" a past time
                cands.append(lead)
        return min(cands, key=lambda d: d.timestamp()) if cands else None
