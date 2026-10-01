"""The DawnContext ties config, state, DB and services together.

It is created once in the FastAPI lifespan and attached to `app.state.ctx`.
Services receive the context so they can read config, mutate state and reach
each other through the registry.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .config import ConfigManager, DawnConfig
from .db import Database
from .services import ServiceRegistry
from .state import StateStore

log = logging.getLogger("dawn.ctx")


def is_sim() -> bool:
    return os.environ.get("DAWN_SIM", "") not in ("", "0", "false", "no")


class DawnContext:
    def __init__(self, cfg_mgr: ConfigManager):
        self.cfg_mgr = cfg_mgr
        self.sim = is_sim()
        self.store = StateStore(tz=self.config.general.timezone)
        self.data_dir = self._data_dir()
        self.runtime_dir = self._runtime_dir()
        self.db = Database(self.data_dir / "dawn.db")
        self.registry = ServiceRegistry()
        self.store.state.system.sim = self.sim
        self.cfg_mgr.on_change(self._on_config)

    @property
    def config(self) -> DawnConfig:
        return self.cfg_mgr.config

    def _data_dir(self) -> Path:
        env = os.environ.get("DAWN_DATA_DIR")
        p = Path(env) if env else Path(self.config.general.data_dir)
        if self.sim and not env and not os.access(p.parent, os.W_OK):
            p = Path.cwd() / "var" / "dawn"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _runtime_dir(self) -> Path:
        env = os.environ.get("DAWN_RUNTIME_DIR")
        p = Path(env) if env else Path(self.config.general.runtime_dir)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            p = self.data_dir / "run"
            p.mkdir(parents=True, exist_ok=True)
        return p

    def svc(self, cls: type) -> Any:
        return self.registry.get(cls)

    async def _on_config(self, old: DawnConfig, new: DawnConfig) -> None:
        if old.general.timezone != new.general.timezone:
            self.store.set_timezone(new.general.timezone)
        self.store.state.system.config_error = None
        self.refresh_settings_summary()
        await self.registry.notify_config(old, new)

    def refresh_settings_summary(self) -> None:
        c = self.config
        st = self.store.state
        st.settings.timezone = c.general.timezone
        st.settings.holiday_region = c.holidays.region
        st.settings.holiday_scope = c.holidays.scope
        st.settings.name = c.general.name
        st.settings.clock_24h = c.display.clock_24h
        st.settings.theme = c.display.theme
        st.settings.auth_required = bool(c.web.auth.pin)
        st.display.layout = c.display.layout
        st.display.show_seconds = c.display.show_seconds
        st.timers.sleep_choices = list(c.timers.sleep_choices_min)
        st.timers.nap_choices = list(c.timers.nap_choices_min)
        lat, lon, src = self.position()
        st.settings.latitude, st.settings.longitude, st.settings.location_source = lat, lon, src
        self.store.touch()

    def position(self) -> tuple[float, float, str]:
        """Current (lat, lon, source) preferring a GPS fix when configured."""
        g = self.store.state.time_sources.gps
        if self.config.location.prefer_gps and g.fix >= 2 and g.lat is not None and g.lon is not None:
            return g.lat, g.lon, "gps"
        return self.config.location.latitude, self.config.location.longitude, "config"
