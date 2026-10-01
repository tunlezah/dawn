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
