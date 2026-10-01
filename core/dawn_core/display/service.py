"""DisplayService: 10 Hz brightness loop, night palette, sunrise/sunset, manual override."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from ..config import DawnConfig
from ..context import DawnContext
from ..services import Service
from ..system.sysinfo import SysInfoService
from .backlight import Backlight, make_backlight
from .curve import BrightnessController, Hysteresis
from .sensors import LuxSensor, make_sensor
from .sun import next_sunrise, sun_times

log = logging.getLogger("dawn.display")


class DisplayService(Service):
    name = "display"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self.sensor: LuxSensor | None = None
        self.backlight: Backlight = Backlight()
        self.controller = BrightnessController([], 4, 2)
        self.night_hys = Hysteresis(5, 7)
        self.lux: float | None = None
        self.mode: str = "auto"
        self.manual_percent: int = 60
        self.manual_until: datetime | None = None
        self._sun_day = None
        self._sun: dict[str, datetime] = {}
        self._task: asyncio.Task[None] | None = None
        self._last_applied: int | None = None
        self._last_publish = 0.0

    # ---- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        cfg = self.ctx.config
        hw = self.ctx.svc(SysInfoService).hw
        self._configure(cfg)
        self.sensor = await make_sensor(cfg.display.lux_sensor, self.ctx.sim, cfg.sim.hub_url)
        self.backlight = await make_backlight(cfg.display.backlight, hw.panel, hw.backlight_sysfs, self.ctx.sim, cfg.sim.hub_url, self.ctx.store)
        self.mode = self.ctx.db.get("display.mode", cfg.display.brightness.mode)
        self.manual_percent = int(self.ctx.db.get("display.manual_percent", cfg.display.brightness.manual_percent))
        mu = self.ctx.db.get("display.manual_until")
        self.manual_until = datetime.fromisoformat(mu) if mu else None
        st = self.ctx.store.state.display
        st.sensor = self.sensor.name if self.sensor else None
        st.sensor_found = self.sensor is not None
        st.backlight_driver = self.backlight.name
        self.controller.jump(self.manual_percent)
        self._task = asyncio.create_task(self._loop(), name="display-loop")
        log.info("display: sensor=%s backlight=%s mode=%s", st.sensor, st.backlight_driver, self.mode)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.sensor:
            await self.sensor.stop()
        await self.backlight.stop()

    async def on_config(self, old: DawnConfig, new: DawnConfig) -> None:
        self._configure(new)
        if old.display.lux_sensor != new.display.lux_sensor or old.display.backlight != new.display.backlight:
            log.info("display hardware config changed; re-initialising")
            if self.sensor:
                await self.sensor.stop()
            await self.backlight.stop()
            hw = self.ctx.svc(SysInfoService).hw
            self.sensor = await make_sensor(new.display.lux_sensor, self.ctx.sim, new.sim.hub_url)
            self.backlight = await make_backlight(new.display.backlight, hw.panel, hw.backlight_sysfs, self.ctx.sim, new.sim.hub_url, self.ctx.store)
            st = self.ctx.store.state.display
            st.sensor = self.sensor.name if self.sensor else None
            st.sensor_found = self.sensor is not None
            st.backlight_driver = self.backlight.name
        self._sun_day = None
        self._last_applied = None

    def _configure(self, cfg: DawnConfig) -> None:
        b = cfg.display.brightness
        self.controller.curve = [(p.lux, p.brightness) for p in b.curve]
        self.controller.hysteresis = b.hysteresis_percent
        self.controller.slew_s = b.slew_s
        self.controller.lo = cfg.display.backlight.min_percent
        self.controller.hi = cfg.display.backlight.max_percent
        self.night_hys.low = b.night_lux_threshold
        self.night_hys.high = b.night_lux_threshold + b.night_hysteresis_lux

    # ---- sun -------------------------------------------------------------
    def _update_sun(self, now: datetime) -> None:
        lat, lon, _ = self.ctx.position()
        key = (now.date(), round(lat, 2), round(lon, 2))
        if self._sun_day != key:
            self._sun = sun_times(lat, lon, now.date(), self.ctx.store.tz, self.ctx.config.location.elevation_m)
            self._sun_day = key
            st = self.ctx.store.state.display
            st.sunrise = self._sun["sunrise"].isoformat(timespec="seconds")
            st.sunset = self._sun["sunset"].isoformat(timespec="seconds")

    def is_daytime(self, now: datetime) -> bool:
        return bool(self._sun) and self._sun["sunrise"] <= now < self._sun["sunset"]

    # ---- loop ------------------------------------------------------------
    async def _loop(self) -> None:
        period = 1.0 / self.ctx.config.display.lux_sensor.sample_hz
        while True:
            t0 = time.monotonic()
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("display tick failed")
            await asyncio.sleep(max(0.02, period - (time.monotonic() - t0)))

    async def tick(self) -> None:
        cfg = self.ctx.config
        b = cfg.display.brightness
        st = self.ctx.store.state
        now = self.ctx.store.now()
        self._update_sun(now)

        # 1. lux
        if self.sensor:
            raw = await self.sensor.read()
            if raw is not None:
                a = cfg.display.lux_sensor.smoothing
                self.lux = raw if self.lux is None else (self.lux + a * (raw - self.lux))

        # 2. night palette
        schedule_night = not self.is_daytime(now)
        if self.sensor and self.lux is not None:
            night = self.night_hys.update(self.lux)
        else:
            night = schedule_night and b.sunset_night_palette

        # 3. manual override expiry
        if self.mode == "manual" and self.manual_until and now >= self.manual_until:
            log.info("manual brightness override expired at sunrise")
            self._set_mode_internal("auto")

        # 4. target
        mono = time.monotonic()
        forced: float | None = None
        if st.alarms.light_wake_active:
            forced = 100
        elif st.face.wake_until and datetime.fromisoformat(st.face.wake_until) > now and st.face.mode == "standby":
            forced = b.standby_wake_percent
        if forced is not None:
            self.controller.set_target(forced, mono)
        elif self.mode == "manual":
            self.controller.set_target(self.manual_percent, mono)
        elif self.sensor and self.lux is not None:
            self.controller.target_from_lux(self.lux, mono)
            self._apply_sunset_cap(now, mono)
        else:
            # schedule fallback: config manual level by day, curve minimum at night
            fallback = b.manual_percent if not schedule_night else min(b.manual_percent, max(1, int(self.controller.curve[0][1])) if self.controller.curve else 10)
            self.controller.set_target(fallback, mono)
            self._apply_sunset_cap(now, mono)

        applied = round(self.controller.step(mono))
        if applied != self._last_applied:
            self._last_applied = applied
            await self.backlight.set(applied)

        # 5. publish (throttled to 2 Hz unless something notable changed)
        d = st.display
        changed = d.night != night or d.brightness != applied or d.mode != self.mode or d.schedule_night != schedule_night
        if changed or time.monotonic() - self._last_publish > 0.5:
            self._last_publish = time.monotonic()
            d.lux = round(self.lux, 1) if self.lux is not None else None
            d.night = night
            d.schedule_night = schedule_night
            d.palette = "night" if night else cfg.display.theme
            d.brightness = applied
            d.target = int(self.controller.target)
            d.mode = self.mode  # type: ignore[assignment]
            d.manual_until = self.manual_until.isoformat(timespec="seconds") if self.manual_until else None
            d.layout = cfg.display.layout
            self.ctx.store.touch()

    def _apply_sunset_cap(self, now: datetime, mono: float) -> None:
        b = self.ctx.config.display.brightness
        if not self._sun or b.post_sunset_cap_minutes <= 0:
            return
        sunset = self._sun["sunset"]
        if sunset <= now and (now - sunset).total_seconds() < b.post_sunset_cap_minutes * 60 and self.controller.target > b.post_sunset_cap_percent:
            self.controller.set_target(b.post_sunset_cap_percent, mono)

    # ---- API -------------------------------------------------------------
    def set_manual(self, percent: int) -> None:
        percent = max(1, min(100, int(percent)))
        self.manual_percent = percent
        self.ctx.db.set("display.manual_percent", percent)
        self._set_mode_internal("manual")

    def set_mode(self, mode: str) -> None:
        self._set_mode_internal(mode)

    def _set_mode_internal(self, mode: str) -> None:
        self.mode = mode
        if mode == "manual":
            if self.ctx.config.display.brightness.manual_override_until == "sunrise":
                lat, lon, _ = self.ctx.position()
                self.manual_until = next_sunrise(lat, lon, self.ctx.store.now(), self.ctx.store.tz, self.ctx.config.location.elevation_m)
            else:
                self.manual_until = None
        else:
            self.manual_until = None
        self.ctx.db.set("display.mode", mode)
        self.ctx.db.set("display.manual_until", self.manual_until.isoformat() if self.manual_until else None)
        st = self.ctx.store.state.display
        st.mode = mode  # type: ignore[assignment]
        st.manual_until = self.manual_until.isoformat(timespec="seconds") if self.manual_until else None
        self.ctx.store.touch()

    def sun(self) -> dict[str, str]:
        return {k: v.isoformat(timespec="seconds") for k, v in self._sun.items()}
