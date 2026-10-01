"""WMO weather interpretation codes -> bundled icon id and description."""

from __future__ import annotations

_TABLE: list[tuple[set[int], str, str]] = [
    ({0}, "clear", "Clear sky"),
    ({1}, "partly", "Mainly clear"),
    ({2}, "partly", "Partly cloudy"),
    ({3}, "cloudy", "Overcast"),
    ({45, 48}, "fog", "Fog"),
    ({51, 53, 55}, "drizzle", "Drizzle"),
    ({56, 57}, "drizzle", "Freezing drizzle"),
    ({61}, "rain", "Light rain"),
    ({63}, "rain", "Rain"),
    ({65}, "rain", "Heavy rain"),
    ({66, 67}, "rain", "Freezing rain"),
    ({71}, "snow", "Light snow"),
    ({73}, "snow", "Snow"),
    ({75}, "snow", "Heavy snow"),
    ({77}, "snow", "Snow grains"),
    ({80}, "rain", "Light showers"),
    ({81}, "rain", "Showers"),
    ({82}, "rain", "Violent showers"),
    ({85, 86}, "snow", "Snow showers"),
    ({95}, "thunder", "Thunderstorm"),
    ({96, 99}, "hail", "Thunderstorm with hail"),
]


def describe(code: int | None, is_day: bool = True) -> tuple[str, str]:
    """(icon_id, description). Icons with day/night variants get a suffix."""
    if code is None:
        return "cloudy", "Unknown"
    for codes, family, text in _TABLE:
        if code in codes:
            icon = f"{family}-{'day' if is_day else 'night'}" if family in ("clear", "partly") else family
            return icon, text
    return "cloudy", f"Code {code}"
