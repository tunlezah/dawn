"""Sunrise/sunset via astral for the current position."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun


def sun_times(lat: float, lon: float, day: date, tz: ZoneInfo, elevation_m: float = 0.0) -> dict[str, datetime]:
    loc = LocationInfo(latitude=lat, longitude=lon)
    obs = loc.observer
    obs.elevation = elevation_m
    try:
        s = sun(obs, date=day, tzinfo=tz)
    except ValueError:  # polar day/night
        base = datetime.combine(day, datetime.min.time(), tzinfo=tz)
        return {"dawn": base + timedelta(hours=6), "sunrise": base + timedelta(hours=6), "sunset": base + timedelta(hours=18), "dusk": base + timedelta(hours=18)}
    return {"dawn": s["dawn"], "sunrise": s["sunrise"], "sunset": s["sunset"], "dusk": s["dusk"]}


def next_sunrise(lat: float, lon: float, now: datetime, tz: ZoneInfo, elevation_m: float = 0.0) -> datetime:
    today = sun_times(lat, lon, now.date(), tz, elevation_m)["sunrise"]
    if today > now:
        return today
    return sun_times(lat, lon, now.date() + timedelta(days=1), tz, elevation_m)["sunrise"]
