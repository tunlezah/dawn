"""Control semantics. Never ambiguous:

Big button  short: ringing -> stop; playing -> standby; standby -> wake face 20 s
            hold 3 s: safe shutdown with on-screen countdown
Encoder     rotate: volume; push while ringing: snooze; push otherwise: next preset
            hold: nap-timer picker on the face
Touch       ringing: snooze (whole screen); otherwise: face menu
Snooze and stop are never on the same control.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("dawn.inputs")


class Actions:
    """What the controller can do; implemented by InputService over the other services."""

    async def is_ringing(self) -> bool: ...
    async def is_playing(self) -> bool: ...
    async def stop_ringing(self) -> None: ...
    async def snooze(self) -> None: ...
    async def standby(self) -> None: ...
    async def wake_face(self) -> None: ...
    async def volume_step(self, direction: int) -> None: ...
    async def next_preset(self) -> None: ...
    async def open_nap_picker(self) -> None: ...
    async def touch(self) -> None: ...
    async def shutdown_countdown(self, seconds_left: int | None) -> None: ...
    async def shutdown(self) -> None: ...


class InputController:
    def __init__(self, actions: Actions, long_press_s: float = 3.0):
        self.a = actions
        self.long_press_s = long_press_s
        self._hold_task: asyncio.Task[None] | None = None
        self._held_fired = False
        self._countdown_shown = False

    async def handle(self, event: str) -> None:
        try:
            await self._handle(event)
        except Exception:  # noqa: BLE001
            log.exception("input %s failed", event)

    async def _handle(self, event: str) -> None:
        if event == "encoder_cw":
            await self.a.volume_step(+1)
        elif event == "encoder_ccw":
            await self.a.volume_step(-1)
        elif event == "encoder_push":
            if await self.a.is_ringing():
                await self.a.snooze()
            else:
                await self.a.next_preset()
        elif event == "encoder_long":
            await self.a.open_nap_picker()
        elif event == "button_down":
            self._held_fired = False
            self._countdown_shown = False
            self._cancel_hold()
            self._hold_task = asyncio.create_task(self._hold_countdown())
        elif event == "button_up":
            fired, shown = self._held_fired, self._countdown_shown
            self._cancel_hold()
            await self.a.shutdown_countdown(None)
            if not fired and not shown:
                await self.button_short()
            # released while the countdown was showing: cancelled, nothing else happens
        elif event == "button_short":
            await self.button_short()
        elif event == "button_long":
            await self.a.shutdown()
        elif event == "touch":
            if await self.a.is_ringing():
                await self.a.snooze()
            else:
                await self.a.touch()
        else:
            log.debug("ignoring unknown input %s", event)

    async def button_short(self) -> None:
        if await self.a.is_ringing():
            await self.a.stop_ringing()
        elif await self.a.is_playing():
            await self.a.standby()
        else:
            await self.a.wake_face()

    async def _hold_countdown(self) -> None:
        try:
            # show the countdown after half a second so a short press never flashes it
            await asyncio.sleep(0.5)
            self._countdown_shown = True
            remaining = self.long_press_s - 0.5
            while remaining > 0:
                await self.a.shutdown_countdown(int(remaining + 0.999))
                await asyncio.sleep(min(1.0, remaining))
                remaining -= 1.0
            self._held_fired = True
            await self.a.shutdown_countdown(0)
            await self.a.shutdown()
        except asyncio.CancelledError:
            pass

    def _cancel_hold(self) -> None:
        if self._hold_task and not self._hold_task.done():
            self._hold_task.cancel()
        self._hold_task = None
