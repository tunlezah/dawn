"""WeatherService: Open-Meteo every 15 min (no API key), cached, stale after 2 h."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ..config import DawnConfig
from ..context import DawnContext
from ..services import Service
from ..state.ui import WeatherState
from .wmo import describe

log = logging.getLogger("dawn.weather")


class WeatherService(Service):
    name = "weather"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self.cache_path: Path = ctx.data_dir / "weather.json"
        self.cache: dict[str, Any] | None = None
        self._task: asyncio.Task[None] | None = None
        self._client = httpx.AsyncClient(timeout=10.0)
        self._last_pos: tuple[float, float] | None = None

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
            "daily": "temperature_2m_max,temperature_2m_min,sunrise,sunset,weather_code",
            "timezone": self.ctx.config.general.timezone, "forecast_days": "1",
            "temperature_unit": cfg.units,
        }
        try:
            r = await self._client.get(cfg.base_url, params=params)
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001
            log.warning("weather fetch failed: %s", e)
            self.publish()
            return False
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
        self.ctx.store.replace("weather", st)
