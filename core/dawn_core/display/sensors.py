"""Ambient light sensors: VEML6030 / VEML7700 (I2C, register compatible), BH1750, sim.

A missing sensor never breaks anything: make_sensor() returns None and the
display falls back to the sunrise/sunset schedule.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from ..config.schema import LuxSensorConfig

log = logging.getLogger("dawn.lux")


class LuxSensor:
    name = "none"
    address: int | None = None

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def read(self) -> float | None:  # lux
        return None


class VemlSensor(LuxSensor):
    """VEML6030 (PiicoDev: 0x10, 0x48 with the jumper cut) and VEML7700 (0x10).
    Gain 1/8, integration 100 ms: 0.4608 lx/count, range to ~30 klx, 100 ms refresh."""

    REG_CONF, REG_ALS, REG_ID = 0x00, 0x04, 0x07
    CONF = 0x1000  # ALS_GAIN=1/8 (bits 12:11 = 10), ALS_IT=100ms (bits 9:6 = 0000), power on
    RESOLUTION = 0.4608

    def __init__(self, bus: int, address: int, name: str = "veml6030"):
        self.bus_no = bus
        self.address = address
        self.name = name
        self._bus = None

    async def start(self) -> None:
        from smbus2 import SMBus  # type: ignore

        self._bus = SMBus(self.bus_no)
        await asyncio.to_thread(self._bus.write_word_data, self.address, self.REG_CONF, self.CONF)
        try:
            ident = await asyncio.to_thread(self._bus.read_word_data, self.address, self.REG_ID)
            if ident & 0xFF == 0x81:
                self.name = "veml7700"
        except OSError:
            pass  # VEML6030 has no ID register
        await asyncio.sleep(0.12)

    async def stop(self) -> None:
        if self._bus:
            try:
                await asyncio.to_thread(self._bus.write_word_data, self.address, self.REG_CONF, self.CONF | 0x01)  # shutdown
                self._bus.close()
            except OSError:
                pass

    async def read(self) -> float | None:
        if not self._bus:
            return None
        try:
            raw = await asyncio.to_thread(self._bus.read_word_data, self.address, self.REG_ALS)
        except OSError as e:
            log.debug("veml read failed: %s", e)
            return None
        lux = raw * self.RESOLUTION
        if lux > 1000:  # datasheet non-linearity correction for high illuminance
            lux = 6.0135e-13 * lux**4 - 9.3924e-9 * lux**3 + 8.1488e-5 * lux**2 + 1.0023 * lux
        return round(lux, 2)


class Bh1750Sensor(LuxSensor):
    name = "bh1750"
    POWER_ON, CONT_HIRES = 0x01, 0x10

    def __init__(self, bus: int, address: int):
        self.bus_no = bus
        self.address = address
        self._bus = None

    async def start(self) -> None:
        from smbus2 import SMBus  # type: ignore

        self._bus = SMBus(self.bus_no)
        await asyncio.to_thread(self._bus.write_byte, self.address, self.POWER_ON)
        await asyncio.to_thread(self._bus.write_byte, self.address, self.CONT_HIRES)
        await asyncio.sleep(0.2)

    async def stop(self) -> None:
        if self._bus:
            self._bus.close()

    async def read(self) -> float | None:
        if not self._bus:
            return None
        try:
            data = await asyncio.to_thread(self._bus.read_i2c_block_data, self.address, self.CONT_HIRES, 2)
        except OSError:
            return None
        return round(((data[0] << 8) | data[1]) / 1.2, 2)


class SimLuxSensor(LuxSensor):
    name = "sim"

    def __init__(self, hub_url: str):
        self.hub = hub_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=1.0)
        self._last: float | None = None

    async def stop(self) -> None:
        await self._client.aclose()

    async def read(self) -> float | None:
        try:
            r = await self._client.get(f"{self.hub}/lux")
            self._last = float(r.json()["lux"])
        except Exception:  # noqa: BLE001
            pass
        return self._last


async def _probe(bus: int, addr: int) -> bool:
    try:
        from smbus2 import SMBus  # type: ignore

        def _try() -> bool:
            with SMBus(bus) as b:
                b.read_byte(addr)
            return True

        return await asyncio.to_thread(_try)
    except Exception:  # noqa: BLE001
        return False


async def make_sensor(cfg: LuxSensorConfig, sim: bool, hub_url: str) -> LuxSensor | None:
    if sim or cfg.driver == "sim":
        return SimLuxSensor(hub_url)
    if cfg.driver == "none":
        return None
    try:
        import smbus2  # noqa: F401
    except ImportError:
        log.warning("smbus2 not installed; lux sensor disabled")
        return None
    candidates: list[tuple[str, int]] = []
    if cfg.driver in ("auto", "veml6030", "veml7700"):
        candidates += [("veml", a) for a in ([cfg.address] if cfg.address else [0x10, 0x48])]
    if cfg.driver in ("auto", "bh1750"):
        candidates += [("bh1750", a) for a in ([cfg.address] if cfg.address else [0x23, 0x5C])]
    for kind, addr in candidates:
        if not await _probe(cfg.i2c_bus, addr):
            continue
        sensor: LuxSensor = VemlSensor(cfg.i2c_bus, addr, "veml7700" if cfg.driver == "veml7700" else "veml6030") if kind == "veml" else Bh1750Sensor(cfg.i2c_bus, addr)
        try:
            await sensor.start()
            if await sensor.read() is None:
                raise OSError("no reading")
            log.info("lux sensor %s at 0x%02x on i2c-%d", sensor.name, addr, cfg.i2c_bus)
            return sensor
        except Exception as e:  # noqa: BLE001
            log.debug("sensor %s@0x%02x failed: %s", kind, addr, e)
    log.warning("no lux sensor found on i2c-%d (checked %s)", cfg.i2c_bus, ", ".join(f"{k}@0x{a:02x}" for k, a in candidates))
    return None
