from __future__ import annotations

import asyncio
import logging
import time

from ..audio.service import AudioService
from ..config import DawnConfig
from ..context import DawnContext
from ..services import Service
from .backend import InputBackend, make_backend
from .controller import Actions, InputController

log = logging.getLogger("dawn.inputs.service")


class _Actions(Actions):
    def __init__(self, ctx: DawnContext):
        self.ctx = ctx

    def _audio(self) -> AudioService:
        return self.ctx.svc(AudioService)

    def _alarms(self):
        try:
            from ..alarms.service import AlarmService

            return self.ctx.svc(AlarmService)
        except Exception:  # noqa: BLE001
            return None

    def _face(self):
        from ..face.service import FaceService

        return self.ctx.svc(FaceService)

    async def is_ringing(self) -> bool:
        # A snoozed alarm is still a ring session: the stop control must end it,
        # while snooze on an already-snoozed alarm is a no-op.
        return self.ctx.store.state.alarms.ringing is not None

    async def is_playing(self) -> bool:
        return self.ctx.store.state.audio.active_source != "none"

    async def stop_ringing(self) -> None:
        al = self._alarms()
        if al:
            await al.stop_ringing()

    async def snooze(self) -> None:
        al = self._alarms()
        if al:
            await al.snooze()

    async def standby(self) -> None:
        await self._audio().standby()

    async def wake_face(self) -> None:
        self._face().wake()

    async def volume_step(self, direction: int) -> None:
        await self._audio().step_volume(direction)

    async def next_preset(self) -> None:
        p = await self._audio().play_next_preset()
        if p is None:
            self._face().toast("No presets", "Add presets from the Radio page", level="info", seconds=4)

    async def open_nap_picker(self) -> None:
        self._face().open_menu("nap")

    async def touch(self) -> None:
        self._face().touch()

    async def shutdown_countdown(self, seconds_left: int | None) -> None:
        self._face().set_shutdown_countdown(seconds_left)

    async def shutdown(self) -> None:
        from ..system.power import shutdown

        await shutdown(self.ctx)


class InputService(Service):
    name = "inputs"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self.controller = InputController(_Actions(ctx), ctx.config.inputs.big_button.long_press_s)
        self.backend: InputBackend | None = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self.last_event: tuple[str, float, str] | None = None  # (event, time, "gpio" | "api") for Diagnostics

    async def start(self) -> None:
        self.backend = make_backend(self.ctx.config.inputs, self.emit, self.ctx.sim, self.ctx.config.sim.hub_url)
        await self.backend.start()
        self._task = asyncio.create_task(self._pump(), name="inputs")
        log.info("inputs backend: %s", self.backend.name)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.backend:
            await self.backend.stop()

    async def on_config(self, old: DawnConfig, new: DawnConfig) -> None:
        self.controller.long_press_s = new.inputs.big_button.long_press_s
        if old.inputs != new.inputs and not self.ctx.sim:
            log.info("inputs config changed; re-initialising GPIO")
            if self.backend:
                await self.backend.stop()
            self.backend = make_backend(new.inputs, self.emit, self.ctx.sim, new.sim.hub_url)
            await self.backend.start()

    def emit(self, event: str) -> None:
        """Thread-safe-ish entry: schedule handling on the loop."""
        self.last_event = (event, time.time(), "gpio")
        self._queue.put_nowait(event)

    async def inject(self, event: str) -> None:
        """From the API (face touch, sim, tests)."""
        self.last_event = (event, time.time(), "api")
        await self.controller.handle(event)

    async def _pump(self) -> None:
        while True:
            ev = await self._queue.get()
            await self.controller.handle(ev)
