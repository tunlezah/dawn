"""Input backends emit raw events to a callback:
  encoder_cw, encoder_ccw, encoder_push (short), encoder_long,
  button_down, button_up (the controller derives short/long + countdown),
  button_short, button_long (synthesized by the sim / API)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import httpx

from ..config.schema import InputsConfig

log = logging.getLogger("dawn.inputs.backend")

EventCb = Callable[[str], None]


class InputBackend:
    name = "none"

    def __init__(self, cfg: InputsConfig, emit: EventCb):
        self.cfg = cfg
        self.emit = emit

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class GpiozeroBackend(InputBackend):
    name = "gpiozero"

    def __init__(self, cfg: InputsConfig, emit: EventCb):
        super().__init__(cfg, emit)
        self._devices: list = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def _thread_emit(self, ev: str) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(self.emit, ev)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        from gpiozero import Button, Device, RotaryEncoder  # type: ignore

        factory = self.cfg.pin_factory
        try:
            if factory == "lgpio":
                from gpiozero.pins.lgpio import LGPIOFactory  # type: ignore

                Device.pin_factory = LGPIOFactory()
            elif factory == "rpigpio":
                from gpiozero.pins.rpigpio import RPiGPIOFactory  # type: ignore

                Device.pin_factory = RPiGPIOFactory()
            elif factory == "pigpio":
                from gpiozero.pins.pigpio import PiGPIOFactory  # type: ignore

                Device.pin_factory = PiGPIOFactory()
            elif factory == "mock":
                from gpiozero.pins.mock import MockFactory  # type: ignore

                Device.pin_factory = MockFactory()
        except Exception as e:  # noqa: BLE001
            log.warning("pin factory %s unavailable (%s); using gpiozero default", factory, e)

        enc = self.cfg.encoder
        if enc.enabled:
            try:
                # A bare encoder (Adafruit 377) has no pull-ups of its own, unlike a KY-040 board: the
                # contacts only ever pull to GND, so every pin needs the SoC's internal pull-up.
                # gpiozero's RotaryEncoder always enables it on A and B; the switch asks for it below.
                # One step is one full quadrature cycle, which is one detent on a 24-detent/24-PPR part.
                a, b = (enc.dt_pin, enc.clk_pin) if enc.invert else (enc.clk_pin, enc.dt_pin)
                r = RotaryEncoder(a, b, max_steps=0, bounce_time=enc.bounce_time_s or None, wrap=False)
                r.when_rotated_clockwise = lambda: self._thread_emit("encoder_cw")
                r.when_rotated_counter_clockwise = lambda: self._thread_emit("encoder_ccw")
                sw = Button(enc.sw_pin, pull_up=True, bounce_time=0.02, hold_time=enc.long_press_s)
                state = {"held": False}

                def on_held() -> None:
                    state["held"] = True
                    self._thread_emit("encoder_long")

                def on_released() -> None:
                    if not state["held"]:
                        self._thread_emit("encoder_push")
                    state["held"] = False

                sw.when_held = on_held
                sw.when_released = on_released
                self._devices += [r, sw]
                log.info("encoder on CLK=%s DT=%s SW=%s (internal pull-ups)", enc.clk_pin, enc.dt_pin, enc.sw_pin)
            except Exception as e:  # noqa: BLE001
                log.error("encoder unavailable: %s", e)
        bb = self.cfg.big_button
        # Not fitted on the reference build: leave the pin alone so a floating GPIO23 can never
        # produce phantom presses. When enabled it is pulled up like the encoder switch.
        if bb.enabled:
            try:
                btn = Button(bb.pin, pull_up=True, bounce_time=bb.bounce_time_s or None)
                btn.when_pressed = lambda: self._thread_emit("button_down")
                btn.when_released = lambda: self._thread_emit("button_up")
                self._devices.append(btn)
                log.info("big button on GPIO%s", bb.pin)
            except Exception as e:  # noqa: BLE001
                log.error("big button unavailable: %s", e)

    async def stop(self) -> None:
        for d in self._devices:
            try:
                d.close()
            except Exception:  # noqa: BLE001
                pass
        self._devices.clear()


class SimHubBackend(InputBackend):
    """Long-polls the sim hub for events produced by the keyboard or the panel."""

    name = "sim"

    def __init__(self, cfg: InputsConfig, emit: EventCb, hub_url: str):
        super().__init__(cfg, emit)
        self.hub = hub_url.rstrip("/")
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll(), name="sim-inputs")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _poll(self) -> None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=2.0)) as c:
            while True:
                try:
                    r = await c.get(f"{self.hub}/inputs/poll", params={"timeout": 20})
                    ev = r.json().get("event")
                    if ev:
                        self.emit(ev)
                except Exception:  # noqa: BLE001
                    await asyncio.sleep(2)


class NullBackend(InputBackend):
    name = "none"


def make_backend(cfg: InputsConfig, emit: EventCb, sim: bool, hub_url: str) -> InputBackend:
    if sim:
        return SimHubBackend(cfg, emit, hub_url)
    try:
        import gpiozero  # noqa: F401

        return GpiozeroBackend(cfg, emit)
    except ImportError:
        log.warning("gpiozero not installed; physical inputs disabled (API/touch still work)")
        return NullBackend(cfg, emit)
