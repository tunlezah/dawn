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
    """The latest occurrence of local time `t` at or before `now`."""
    for back in (0, 1, 2):
        occ = local_at(now - timedelta(days=back), t)
        if occ <= now:
            return occ
    return local_at(now - timedelta(days=2), t)


def next_at(now: datetime, t: time) -> datetime:
    for ahead in (0, 1, 2):
        occ = local_at(now + timedelta(days=ahead), t)
        if occ > now:
            return occ
    return local_at(now + timedelta(days=2), t)


def in_window(now: datetime, start: time, end: time) -> bool:
    """Inside [start, end): the last bedtime is more recent than the last morning (works across midnight)."""
    if start == end:
        return False
    return last_at(now, start) > last_at(now, end)


class SleepPlanner:
    def __init__(self) -> None:
        self.asleep = False
        self.reason: str | None = None  # schedule | dark | manual
        self.dark_armed = True
        self.room: Room | None = None
        self._dark_since: datetime | None = None
        self._bright_since: datetime | None = None
        self._last: datetime | None = None
        self._bright_woken = False
        self._handled_alarm: datetime | None = None
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
    def _room(self, s: SleepSettings, now: datetime, lux: float | None) -> tuple[bool, bool]:
        """(became dark, became bright) with dwell times. Until the first reading has settled the room is unknown
        (None): settling to dark counts as becoming dark, settling to bright is only where we are, not a change."""
        if lux is None:
            self.room = None
            self._dark_since = self._bright_since = None
            return False, False
        bright_lux = max(s.bright_lux, s.dark_lux)
        self._dark_since = (self._dark_since or now) if lux < s.dark_lux else None
        self._bright_since = (self._bright_since or now) if lux > bright_lux else None
        dark = self._dark_since is not None and (now - self._dark_since).total_seconds() >= s.dark_after_s
        bright = self._bright_since is not None and (now - self._bright_since).total_seconds() >= s.bright_after_s
        before = self.room
        if dark:
            self.room = "dark"
        elif bright:
            self.room = "bright"
        elif (before is None and s.dark_lux <= lux <= bright_lux) or (before == "dark" and lux >= s.dark_lux) or (before == "bright" and lux <= bright_lux):
            self.room = "between"
        return self.room == "dark" and before != "dark", self.room == "bright" and before not in ("bright", None)

    # ---- one step ----------------------------------------------------------
    def update(self, s: SleepSettings, now: datetime, lux: float | None, next_wake: datetime | None, ringing: bool) -> bool:
        became_dark, became_bright = self._room(s, now, lux)
        last, self._last = self._last, now
        # boot, or the clock stepped (chrony at boot): judge the schedule by where we are, not by what was crossed
        resync = last is None or now < last - timedelta(seconds=5) or now - last > timedelta(minutes=10)
        crossed = (lambda t: last_at(now, t) > last) if not resync and last is not None else (lambda t: False)  # noqa: E731
        window = in_window(now, s.start, s.end)

        if self.room == "bright":
            self.dark_armed = True  # it has been light since the last morning: evenings may use the dark trigger again
        if ringing:
            self._wake("ring", disarm=True)
            return self.asleep
        if s.end_before_alarm and next_wake is not None and next_wake != self._handled_alarm:
            if next_wake - timedelta(minutes=s.alarm_lead_minutes) <= now < next_wake + timedelta(minutes=5):
                self._handled_alarm = next_wake
                self._wake("alarm", disarm=True)
        if s.end_at_time and crossed(s.end):
            self._wake("morning", disarm=True)
        if s.end_when_bright and became_bright:
            self._wake("bright", disarm=False)
            self._bright_woken = s.start_at_time and window
        if resync and self.asleep and self.reason == "schedule" and not window:
            self._wake("resync", disarm=False)  # the clock moved out of the bedtime window
        if s.start_at_time and (crossed(s.start) or (resync and window)):
            self.dark_armed = True
            self._sleep("schedule")
        if became_dark and (s.start_when_dark and self.dark_armed or self._bright_woken and window):
            self._sleep("dark" if s.start_when_dark and not window else "schedule")
        return self.asleep

    def next_start(self, s: SleepSettings, now: datetime) -> datetime | None:
        return next_at(now, s.start) if s.start_at_time else None

    def next_end(self, s: SleepSettings, now: datetime, next_wake: datetime | None) -> datetime | None:
        cands = []
        if s.end_at_time:
            cands.append(next_at(now, s.end))
        if s.end_before_alarm and next_wake is not None:
            cands.append(next_wake - timedelta(minutes=s.alarm_lead_minutes))
        return min(cands) if cands else None
