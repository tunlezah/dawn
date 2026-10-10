from __future__ import annotations

from dawn_core.weather.wmo import describe


def test_wmo_mapping() -> None:
    assert describe(0, True) == ("clear-day", "Clear sky")
    assert describe(0, False) == ("clear-night", "Clear sky")
    assert describe(2, False)[0] == "partly-night"
    assert describe(3)[0] == "cloudy"
    assert describe(45)[0] == "fog"
    assert describe(55)[0] == "drizzle"
    assert describe(65) == ("rain", "Heavy rain")
    assert describe(75)[0] == "snow"
    assert describe(81)[0] == "rain"
    assert describe(95)[0] == "thunder"
    assert describe(99)[0] == "hail"
    assert describe(None) == ("cloudy", "Unknown")
    assert describe(123)[0] == "cloudy"


def test_upcoming_hours_starts_at_this_hour() -> None:
    from dawn_core.weather.service import upcoming_hours

    times = [f"2026-10-10T{h:02d}:00" for h in range(24)] + [f"2026-10-11T{h:02d}:00" for h in range(24)]
    hourly = {
        "time": times, "weather_code": [95 if i == 15 else 0 for i in range(48)],
        "is_day": [1 if 6 <= i % 24 < 18 else 0 for i in range(48)], "wind_speed_10m": [40.0] * 48,
        "temperature_2m": [20.0] * 47,  # a short column must not break the rest
    }
    hours = upcoming_hours(hourly, "2026-10-10T14:37")
    assert hours[0].time == "2026-10-10T14:00"
    assert hours[1].code == 95 and hours[1].icon == "thunder" and hours[1].wind_kmh == 40.0
    assert hours[0].icon == "clear-day"
    assert len(hours) == 24 and hours[-1].time == "2026-10-11T13:00"
    assert hours[10].icon == "clear-night" and not hours[10].is_day
    assert upcoming_hours(hourly, "2026-10-11T23:10")[0].temperature is None
    assert upcoming_hours(None, "2026-10-10T14:37") == []
    assert upcoming_hours(hourly, "2026-10-12T00:10") == []
