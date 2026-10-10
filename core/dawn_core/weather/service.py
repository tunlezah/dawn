"""WeatherService: Open-Meteo every 15 min (no API key), cached, stale after 2 h."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..config import DawnConfig
from ..context import DawnContext
from ..services import Service
from ..state.ui import WeatherHour, WeatherState
from .wmo import describe

log = logging.getLogger("dawn.weather")

# hourly fields the face's scene draws from (weather, cloud, rain, wind, haze)
HOURLY = "temperature_2m,weather_code,cloud_cover,precipitation_probability,precipitation,wind_speed_10m,wind_gusts_10m,visibility,is_day"
HOURS_AHEAD = 24


def upcoming_hours(hourly: dict[str, Any] | None, now_local: str, ahead: int = HOURS_AHEAD) -> list[WeatherHour]:
    """The forecast hours from the one now is in, `ahead` of them. `now_local` is local wall clock "YYYY-MM-DDTHH:MM"
    (Open-Meteo's hourly times are local in the requested time zone, so plain string order is time order)."""
    if not isinstance(hourly, dict):
        return []
    times = hourly.get("time") or []
    this_hour = now_local[:13] + ":00"

    def col(name: str, i: int) -> Any:
        v = hourly.get(name)
        return v[i] if isinstance(v, list) and i < len(v) else None

    out: list[WeatherHour] = []
    for i, t in enumerate(times):
        if not isinstance(t, str) or t < this_hour:
            continue
        code = col("weather_code", i)
        is_day = bool(col("is_day", i) if col("is_day", i) is not None else 1)
        out.append(WeatherHour(
            time=t, code=code, icon=describe(code, is_day)[0] if code is not None else None,
            temperature=col("temperature_2m", i), cloud_cover=col("cloud_cover", i),
            precip_probability=col("precipitation_probability", i), precipitation=col("precipitation", i),
            wind_kmh=col("wind_speed_10m", i), gusts_kmh=col("wind_gusts_10m", i), visibility_m=col("visibility", i),
            is_day=is_day,
        ))
        if len(out) >= ahead:
            break
    return out


class WeatherService(Service):
    name = "weather"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self.cache_path: Path = ctx.data_dir / "weather.json"
        self.cache: dict[str, Any] | None = None
        self._task: asyncio.Task[None] | None = None
        self._client = httpx.AsyncClient(timeout=10.0)
        self._last_pos: tuple[float, float] | None = None
        self.last_error: str | None = None  # last failed fetch, for Diagnostics
        self.last_attempt: str | None = None

    async def start(self) -> None:
        self._load_cache()
        self.publish()
        self._task = asyncio.create_task(self._loop(), name="weather")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self._client.aclose()

    async def on_config(self, old: DawnConfig, new: DawnConfig) -> None:
        if old.weather != new.weather or old.location != new.location:
            self._last_pos = None
            asyncio.create_task(self.refresh())

    def _load_cache(self) -> None:
        try:
            self.cache = json.loads(self.cache_path.read_text())
        except (OSError, ValueError):
            self.cache = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception:  # noqa: BLE001
                log.exception("weather refresh failed")
            await asyncio.sleep(self.ctx.config.weather.interval_minutes * 60)

    async def _online(self) -> bool:
        if self.ctx.sim:
            try:
                r = await self._client.get(f"{self.ctx.config.sim.hub_url}/network", timeout=2)
                return bool(r.json().get("online"))
            except Exception:  # noqa: BLE001
                return True
        return True  # a failed fetch is handled the same way

    async def refresh(self) -> bool:
        cfg = self.ctx.config.weather
        if not cfg.enabled or cfg.provider == "none":
            self.publish()
            return False
        lat, lon, _ = self.ctx.position()
        if not await self._online():
            log.debug("offline; keeping cached weather")
            self.publish()
            return False
        params = {
            "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
            "current": "temperature_2m,weather_code,is_day",
            "hourly": HOURLY,
            "daily": "temperature_2m_max,temperature_2m_min,sunrise,sunset,weather_code",
            # two days of hours, so the hours after midnight are there and a missed fetch or two costs nothing
            "timezone": self.ctx.config.general.timezone, "forecast_days": "2", "wind_speed_unit": "kmh",
            "temperature_unit": cfg.units,
        }
        self.last_attempt = self.ctx.store.iso()
        try:
            r = await self._client.get(cfg.base_url, params=params)
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001
            log.warning("weather fetch failed: %s", e)
            self.last_error = f"{type(e).__name__}: {e}"
            self.publish()
            return False
        self.last_error = None
        self.cache = {"fetched_at": self.ctx.store.iso(), "lat": lat, "lon": lon, "data": data}
        try:
            self.cache_path.write_text(json.dumps(self.cache))
        except OSError as e:
            log.warning("cannot write weather cache: %s", e)
        self._last_pos = (lat, lon)
        self.publish()
        return True

    def publish(self) -> None:
        cfg = self.ctx.config
        st = WeatherState(units=cfg.weather.units, location_label=cfg.location.city_label)
        if self.cache and isinstance(self.cache.get("data"), dict):
            data = self.cache["data"]
            cur = data.get("current") or {}
            daily = data.get("daily") or {}
            fetched = datetime.fromisoformat(self.cache["fetched_at"])
            is_day = bool(cur.get("is_day", 1))
            icon, text = describe(cur.get("weather_code"), is_day)
            st.available = True
            st.fetched_at = self.cache["fetched_at"]
            st.stale = (self.ctx.store.now() - fetched) > timedelta(hours=cfg.weather.stale_after_hours)
            st.temperature = cur.get("temperature_2m")
            st.code = cur.get("weather_code")
            st.icon = icon
            st.description = text
            st.is_day = is_day
            st.t_min = (daily.get("temperature_2m_min") or [None])[0]
            st.t_max = (daily.get("temperature_2m_max") or [None])[0]
            st.sunrise = (daily.get("sunrise") or [None])[0]
            st.sunset = (daily.get("sunset") or [None])[0]
            tz = ZoneInfo(cfg.general.timezone)
            st.hours = upcoming_hours(data.get("hourly"), self.ctx.store.now().astimezone(tz).strftime("%Y-%m-%dT%H:%M"))
        self.ctx.store.replace("weather", st)
