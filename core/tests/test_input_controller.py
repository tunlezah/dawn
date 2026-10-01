from __future__ import annotations

import asyncio

from dawn_core.inputs.controller import Actions, InputController


class Rec(Actions):
    def __init__(self, ringing: bool = False, playing: bool = False):
        self.ringing = ringing
        self.playing = playing
        self.calls: list[str] = []
        self.countdowns: list[int | None] = []

    async def is_ringing(self) -> bool:
        return self.ringing

    async def is_playing(self) -> bool:
        return self.playing

    async def stop_ringing(self) -> None:
        self.calls.append("stop_ringing")

    async def snooze(self) -> None:
        self.calls.append("snooze")

    async def standby(self) -> None:
        self.calls.append("standby")

    async def wake_face(self) -> None:
        self.calls.append("wake")

    async def volume_step(self, direction: int) -> None:
        self.calls.append(f"vol{direction:+d}")

    async def next_preset(self) -> None:
        self.calls.append("preset")

    async def open_nap_picker(self) -> None:
        self.calls.append("nap")

    async def touch(self) -> None:
        self.calls.append("touch")

    async def shutdown_countdown(self, seconds_left: int | None) -> None:
        self.countdowns.append(seconds_left)

    async def shutdown(self) -> None:
        self.calls.append("shutdown")


async def test_big_button_short_press_semantics() -> None:
    r = Rec(ringing=True)
    await InputController(r).handle("button_short")
    assert r.calls == ["stop_ringing"]
    r = Rec(playing=True)
    await InputController(r).handle("button_short")
    assert r.calls == ["standby"]
    r = Rec()
    await InputController(r).handle("button_short")
    assert r.calls == ["wake"]


async def test_encoder_semantics() -> None:
    r = Rec(ringing=True)
    c = InputController(r)
    await c.handle("encoder_push")
    assert r.calls == ["snooze"]
    r = Rec()
    c = InputController(r)
    for ev in ("encoder_cw", "encoder_ccw", "encoder_push", "encoder_long"):
        await c.handle(ev)
    assert r.calls == ["vol+1", "vol-1", "preset", "nap"]


async def test_touch_semantics() -> None:
    r = Rec(ringing=True)
    await InputController(r).handle("touch")
    assert r.calls == ["snooze"]
    r = Rec(playing=True)
    await InputController(r).handle("touch")
    assert r.calls == ["touch"]


async def test_hold_shows_countdown_and_shuts_down() -> None:
    r = Rec()
    c = InputController(r, long_press_s=1.2)
    await c.handle("button_down")
    await asyncio.sleep(1.6)
    assert "shutdown" in r.calls
    assert r.countdowns and r.countdowns[-1] == 0
    await c.handle("button_up")
    assert "wake" not in r.calls  # release after hold is not a short press


async def test_release_during_countdown_cancels() -> None:
    r = Rec()
    c = InputController(r, long_press_s=3.0)
    await c.handle("button_down")
    await asyncio.sleep(0.8)
    await c.handle("button_up")
    assert "shutdown" not in r.calls
    assert "wake" not in r.calls  # countdown was visible: cancelled, nothing else
    assert r.countdowns[-1] is None


async def test_quick_tap_is_short_press() -> None:
    r = Rec()
    c = InputController(r, long_press_s=3.0)
    await c.handle("button_down")
    await asyncio.sleep(0.1)
    await c.handle("button_up")
    assert r.calls == ["wake"]
