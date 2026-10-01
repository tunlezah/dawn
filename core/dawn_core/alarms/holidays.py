"""Australian public holidays for alarm skipping, with regional-only awareness."""

from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache

import holidays as _holidays

log = logging.getLogger("dawn.holidays")

# Holidays the `holidays` package lists for a state that only apply in part of it.
# They count only when holiday_scope == "include_regional".
REGIONAL_ONLY: dict[str, tuple[str, ...]] = {
    "QLD": ("The Royal Queensland Show",),
    "NT": ("Alice Springs Show Day", "Tennant Creek Show Day", "Katherine Show Day", "Darwin Show Day", "Borroloola Show Day"),
    "TAS": (
        "Royal Hobart Regatta", "Recreation Day", "Launceston Cup", "King Island Show", "Devonport Cup",
        "Burnie Show", "Royal Launceston Show", "Royal Hobart Show", "Flinders Island Show", "Devonport Show", "Agfest",
    ),
}


@lru_cache(maxsize=64)
def _calendar(region: str, year: int) -> dict[date, str]:
    try:
        return dict(_holidays.AU(subdiv=region, years=year).items())
    except Exception as e:  # noqa: BLE001
        log.error("holiday calendar for %s/%s failed: %s", region, year, e)
        return {}


class HolidayCalendar:
    def __init__(self, region: str, scope: str = "statewide", extra_dates: list[str] | None = None, exclude_names: list[str] | None = None):
        self.region = region
        self.scope = scope
        self.extra = {date.fromisoformat(d) for d in (extra_dates or []) if d}
        self.exclude = [n.lower() for n in (exclude_names or []) if n]

    def holidays_in(self, year: int) -> list[tuple[date, str, bool]]:
        """(date, name, regional_only) for the year, honouring scope/exclusions."""
        out = []
        regional = tuple(n.lower() for n in REGIONAL_ONLY.get(self.region, ()))
        for d, name in sorted(_calendar(self.region, year).items()):
            lname = name.lower()
            if any(x in lname for x in self.exclude):
                continue
            is_regional = any(r in lname for r in regional)
            if is_regional and self.scope != "include_regional":
                continue
            out.append((d, name, is_regional))
        for d in sorted(self.extra):
            if d.year == year:
                out.append((d, "Custom holiday", False))
        return out

    def name(self, day: date) -> str | None:
        for d, name, _ in self.holidays_in(day.year):
            if d == day:
                return name
        return None

    def is_holiday(self, day: date) -> bool:
        return self.name(day) is not None
