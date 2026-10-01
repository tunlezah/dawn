"""Pure scheduling: when does an alarm next ring?

All computation is in the alarm's local time zone (zoneinfo). Occurrences are
built with `datetime.combine(date, time, tzinfo=tz)`:
- A time that does not exist on spring-forward day (e.g. 02:30 when 02:00 jumps
  to 03:00) is normalised by Python using the pre-transition offset, which puts
  it at 03:30 local, i.e. one hour of wall-clock later; the alarm still rings
  exactly once that day.
- An ambiguous time on fall-back day (02:30 happening twice) uses fold=0, the
  first occurrence; it rings once.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

HolidayFn = Callable[[date], bool]

WEEKDAYS = [0, 1, 2, 3, 4]
WEEKENDS = [5, 6]


@dataclass
class AlarmSpec:
    time: str  # "HH:MM"
    repeat: str = "weekdays"  # once|weekdays|weekends|daily|custom
    days: list[int] = field(default_factory=list)  # Mon=0..Sun=6 for custom
    enabled: bool = True
    skip_public_holidays: bool = False
    skip_next: bool = False
    last_fired_occurrence: str | None = None  # ISO local datetime handled last

    def hhmm(self) -> time:
        h, m = self.time.split(":")
        return time(int(h), int(m))

    def active_days(self) -> list[int]:
        if self.repeat == "weekdays":
            return WEEKDAYS
        if self.repeat == "weekends":
            return WEEKENDS
        if self.repeat in ("daily", "once"):
            return list(range(7))
        return sorted(set(self.days))


def occurrence_on(day: date, t: time, tz: ZoneInfo) -> datetime:
    """Local datetime for `t` on `day` (DST rules as in the module docstring)."""
    naive = datetime.combine(day, t)
    local = naive.replace(tzinfo=tz, fold=0)
    # Non-existent local time: normalise through UTC so it lands after the gap.
    utc = local.astimezone(ZoneInfo("UTC"))
    back = utc.astimezone(tz)
    return back


def next_occurrence(
    spec: AlarmSpec,
    after: datetime,
    tz: ZoneInfo,
    is_holiday: HolidayFn | None = None,
    leave_until: date | None = None,
    horizon_days: int = 400,
) -> datetime | None:
    """First occurrence strictly after `after` that is not skipped.

    - skip_next skips exactly one otherwise-valid occurrence.
    - holidays (when enabled on the alarm) skip that day's occurrence.
    - leave_until suppresses repeating alarms up to and including that date.
    - `once` alarms are not affected by leave or holidays; they ring the next
      time the clock shows HH:MM (today or tomorrow).
    """
    if not spec.enabled:
        return None
    after_local = after.astimezone(tz)
    t = spec.hhmm()
    days = spec.active_days()
    if not days:
        return None
    skip_pending = spec.skip_next
    day = after_local.date()
    for _ in range(horizon_days):
        if day.weekday() in days:
            occ = occurrence_on(day, t, tz)
            if occ > after_local:
                if spec.repeat != "once":
                    if leave_until and day <= leave_until:
                        day += timedelta(days=1)
                        continue
                    if spec.skip_public_holidays and is_holiday and is_holiday(day):
                        day += timedelta(days=1)
                        continue
                if skip_pending:
                    skip_pending = False
                    day += timedelta(days=1)
                    continue
                return occ
        day += timedelta(days=1)
    return None


def due_occurrence(
    spec: AlarmSpec,
    now: datetime,
    tz: ZoneInfo,
    is_holiday: HolidayFn | None = None,
    leave_until: date | None = None,
    lookback: timedelta = timedelta(hours=26),
) -> datetime | None:
    """The most recent occurrence at or before `now` within `lookback`, if it has not
    been handled yet (compared against spec.last_fired_occurrence). Used by the
    engine tick to decide whether to fire or to log a miss."""
    start = now - lookback
    occ = next_occurrence(spec, start, tz, is_holiday, leave_until, horizon_days=3)
    latest: datetime | None = None
    while occ is not None and occ <= now:
        latest = occ
        occ = next_occurrence(spec, occ, tz, is_holiday, leave_until, horizon_days=3)
    if latest is None:
        return None
    if spec.last_fired_occurrence and _parse(spec.last_fired_occurrence) >= latest:
        return None
    return latest


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s)
