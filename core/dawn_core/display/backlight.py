"""Backlight drivers: sysfs (DSI), HyperPixel PWM, software overlay (HDMI), sim, none."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from ..config.schema import BacklightConfig
from ..state import StateStore

log = logging.getLogger("dawn.backlight")


class Backlight:
    name = "none"

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def set(self, percent: int) -> None: ...


class SysfsBacklight(Backlight):
    name = "sysfs"

    def __init__(self, path: str, invert: bool = False):
        self.dir = Path(path)
        self.invert = invert
        self.max = 255

    async def start(self) -> None:
        self.max = int((self.dir / "max_brightness").read_text().strip() or 255)
        power = self.dir / "bl_power"
        if power.exists():
            try:
                power.write_text("0")
            except OSError:
                pass
        log.info("sysfs backlight %s (max %d)", self.dir, self.max)

    async def set(self, percent: int) -> None:
        pct = 100 - percent if self.invert else percent
        value = max(0, min(self.max, round(self.max * pct / 100)))
        try:
            await asyncio.to_thread((self.dir / "brightness").write_text, str(value))
        except OSError as e:
            log.warning("backlight write failed: %s", e)


class HyperPixelPwmBacklight(Backlight):
    name = "hyperpixel_pwm"

    def __init__(self, pin: int, frequency: int, invert: bool = False):
        self.pin, self.frequency, self.invert = pin, frequency, invert
        self._dev = None

    async def start(self) -> None:
        from gpiozero import PWMOutputDevice  # type: ignore

        self._dev = PWMOutputDevice(self.pin, frequency=self.frequency, initial_value=0.6)
        log.info("HyperPixel PWM backlight on GPIO%d", self.pin)

    async def stop(self) -> None:
        if self._dev:
            self._dev.close()

    async def set(self, percent: int) -> None:
        if self._dev:
            v = percent / 100
            self._dev.value = 1 - v if self.invert else v


class OverlayBacklight(Backlight):
    """HDMI panels without dimming: the face draws a black overlay with this opacity."""

    name = "overlay"

    def __init__(self, store: StateStore):
        self.store = store

    async def set(self, percent: int) -> None:
        dim = round(1 - percent / 100, 3)
        if self.store.state.display.overlay_dim != dim:
            self.store.state.display.overlay_dim = dim
            self.store.touch()


class SimBacklight(Backlight):
    name = "sim"

    def __init__(self, hub_url: str, store: StateStore):
        self.hub = hub_url.rstrip("/")
        self.store = store
        self._client = httpx.AsyncClient(timeout=1.0)
        self._last: int | None = None

    async def stop(self) -> None:
        await self._client.aclose()

    async def set(self, percent: int) -> None:
        if percent == self._last:
            return
        self._last = percent
        # the laptop has no backlight to drive: dim the face like the HDMI overlay does
        dim = round(1 - percent / 100, 3) * 0.6
        self.store.state.display.overlay_dim = dim
        self.store.touch()
        try:
            await self._client.post(f"{self.hub}/backlight", json={"percent": percent, "driver": "sim"})
        except Exception:  # noqa: BLE001
            pass


async def make_backlight(cfg: BacklightConfig, panel: str, sysfs_path: str | None, sim: bool, hub_url: str, store: StateStore) -> Backlight:
    if sim or cfg.driver == "sim":
        return SimBacklight(hub_url, store)
    driver = cfg.driver
    if driver == "auto":
        if sysfs_path:
            driver = "sysfs"
        elif panel == "hyperpixel4":
            driver = "hyperpixel_pwm"
        elif panel == "hdmi":
            driver = "overlay"
        else:
            driver = "none"
    try:
        bl: Backlight
        if driver == "sysfs" and sysfs_path:
            bl = SysfsBacklight(sysfs_path, cfg.invert)
        elif driver == "hyperpixel_pwm":
            bl = HyperPixelPwmBacklight(cfg.pwm_pin, cfg.pwm_frequency_hz, cfg.invert)
        elif driver == "overlay":
            bl = OverlayBacklight(store)
        else:
            bl = Backlight()
        await bl.start()
        return bl
    except Exception as e:  # noqa: BLE001
        log.warning("backlight driver %s failed (%s); using software overlay", driver, e)
        return OverlayBacklight(store)
