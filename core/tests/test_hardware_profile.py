"""The reference build: I2S mono amp (volume ceiling, high-pass, no bass boost, pinned sink),
bare encoder on internal pull-ups, no big button, and Pi throttling flags."""

from __future__ import annotations

import asyncio

import pytest

from dawn_core.app import create_app
from dawn_core.audio.backend import Sink
from dawn_core.audio.service import AudioService
from dawn_core.config import ConfigManager
from dawn_core.config.schema import InputsConfig
from dawn_core.system.hw import parse_throttled, throttle_flags


@pytest.fixture()
async def ctx(tmp_config):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        yield app.state.ctx


async def test_volume_100_lands_on_the_output_ceiling(ctx) -> None:
    a: AudioService = ctx.svc(AudioService)
    await ctx.cfg_mgr.update({"audio": {"output_ceiling_percent": 80}})
    await a.set_volume(100)
    assert a.volume == 100 and a.backend.volume == 80
    await a.set_volume(50)
    assert a.backend.volume == 40
    # recalibrating re-applies the current level without touching the user's volume
    await ctx.cfg_mgr.update({"audio": {"output_ceiling_percent": 60}})
    assert a.volume == 50 and a.backend.volume == 30


async def test_alarm_and_chime_respect_the_volume_limits(ctx) -> None:
    from dawn_core.alarms.service import AlarmService

    a: AudioService = ctx.svc(AudioService)
    await ctx.cfg_mgr.update({"audio": {"max_volume": 60, "output_ceiling_percent": 90}})
    await ctx.svc(AlarmService).test_ring(source="chime:birds", volume=100, ramp_seconds=0)
    await asyncio.sleep(0.1)
    assert a.volume == 60
    assert a.backend.volume == 54  # 60 % of the 90 % ceiling
    await ctx.svc(AlarmService).stop_ringing()


async def test_max_volume_lowered_below_current_volume_clamps(ctx) -> None:
    a: AudioService = ctx.svc(AudioService)
    await a.set_volume(80)
    await ctx.cfg_mgr.update({"audio": {"max_volume": 50}})
    assert a.volume == 50 and a.backend.volume == 50


async def test_eq_highpass_and_no_bass_boost_by_default(ctx) -> None:
    a: AudioService = ctx.svc(AudioService)
    assert a.backend.highpass_hz == 110.0
    await ctx.cfg_mgr.update({"audio": {"eq": {"bass_db": 6.0, "treble_db": -2.0}}})
    assert a.backend.eq == (0.0, -2.0)  # boost capped at bass_max_db = 0
    assert ctx.store.state.audio.eq.bass_db == 0.0
    await ctx.cfg_mgr.update({"audio": {"eq": {"bass_db": -4.0}}})
    assert a.backend.eq[0] == -4.0  # cuts are always allowed
    # the high-pass protects the driver even with the tone controls switched off
    await ctx.cfg_mgr.update({"audio": {"eq": {"enabled": False}}})
    assert a.backend.eq == (0.0, 0.0) and a.backend.highpass_hz == 110.0
    await ctx.cfg_mgr.update({"audio": {"eq": {"highpass_hz": None}}})
    assert a.backend.highpass_hz is None


async def test_pinned_sink_by_kind_beats_usb_priority(ctx) -> None:
    a: AudioService = ctx.svc(AudioService)
    sinks = await a.backend.list_sinks()
    assert a._choose_sink(sinks).kind == "usb"  # default priority: a USB device would win
    await ctx.cfg_mgr.update({"audio": {"pinned_sink": "hifiberry"}})
    assert a._choose_sink(sinks).kind == "hifiberry"
    assert ctx.store.state.audio.sink.kind == "hifiberry"
    # a missing pin falls back to priority instead of leaving the clock silent
    only_usb = [Sink("9", "alsa_output.usb-x", "USB", "usb")]
    assert a._choose_sink(only_usb).kind == "usb"


def test_throttled_parsing() -> None:
    assert parse_throttled("throttled=0x0\n") == 0
    assert parse_throttled("throttled=0x50005") == 0x50005
    assert parse_throttled("") is None
    assert throttle_flags(0) == []
    assert throttle_flags(0x10000) == ["under-voltage since boot"]
    assert "under-voltage" in throttle_flags(0x50005) and "throttled since boot" in throttle_flags(0x50005)


# --------------------------------------------------------------------------- #
# GPIO with gpiozero's mock pins
# --------------------------------------------------------------------------- #
pytest.importorskip("gpiozero")


@pytest.fixture()
async def gpio():
    from gpiozero import Device
    from gpiozero.pins.mock import MockFactory

    from dawn_core.inputs.backend import GpiozeroBackend

    Device.pin_factory = MockFactory()
    events: list[str] = []
    backends = []

    async def make(**encoder):
        cfg = InputsConfig.model_validate({"pin_factory": "native", "encoder": {"bounce_time_s": 0, **encoder}})
        b = GpiozeroBackend(cfg, events.append)
        await b.start()
        backends.append(b)
        return b

    yield make, events, Device.pin_factory
    for b in backends:
        await b.stop()
    Device.pin_factory.reset()


def _detent(factory, first: int, second: int) -> None:
    """One detent: first contact closes, then the second, then both open again."""
    a, b = factory.pin(first), factory.pin(second)
    a.drive_low()
    b.drive_low()
    a.drive_high()
    b.drive_high()


async def test_encoder_pins_use_internal_pull_ups(gpio) -> None:
    make, _, factory = gpio
    await make()
    for pin in (17, 27, 22):
        assert factory.pin(pin).pull == "up"
        assert factory.pin(pin).state == 1  # idle high with nothing driving it


async def test_one_step_per_detent_and_invert(gpio) -> None:
    make, events, factory = gpio
    b = await make()
    _detent(factory, 17, 27)
    await asyncio.sleep(0.05)
    _detent(factory, 27, 17)
    await asyncio.sleep(0.05)
    assert sorted(events) == ["encoder_ccw", "encoder_cw"] and len(events) == 2
    forward = events[0]
    await b.stop()
    events.clear()
    await make(invert=True)
    _detent(factory, 17, 27)
    await asyncio.sleep(0.05)
    assert len(events) == 1 and events[0] != forward


async def test_switch_press_on_gpio22(gpio) -> None:
    make, events, factory = gpio
    await make()
    factory.pin(22).drive_low()
    factory.pin(22).drive_high()
    await asyncio.sleep(0.05)
    assert events == ["encoder_push"]


async def test_big_button_absent_by_default(gpio) -> None:
    make, events, factory = gpio
    await make()
    # GPIO23 is left alone: a floating (here: toggling) pin produces no button events
    pin = factory.pin(23)
    pin.drive_low()
    pin.drive_high()
    await asyncio.sleep(0.05)
    assert events == []
