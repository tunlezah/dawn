"""FaceService: derives the face mode from the rest of the state and owns the
face-only bits (menu, wake timer, messages, shutdown countdown, setup info)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from ..context import DawnContext
from ..services import Service
from ..state.ui import FaceMessage, UIState

log = logging.getLogger("dawn.face")


class FaceService(Service):
    name = "face"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self._task: asyncio.Task[None] | None = None
        self._menu_opened_at: datetime | None = None
        self._demo_mode: str | None = None

    async def start(self) -> None:
        self.ctx.store.subscribe(self._on_state)
        self._task = asyncio.create_task(self._tick(), name="face-tick")
        self.recompute()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    # ---- derived mode ----------------------------------------------------
    def _on_state(self, _s: UIState) -> None:
        self.recompute()

    def recompute(self) -> None:
        st = self.ctx.store.state
        f = st.face
        now = self.ctx.store.now()
        if self._demo_mode:
            mode = self._demo_mode
        elif st.alarms.ringing is not None:
            mode = "ringing"
        elif st.alarms.light_wake_active:
            mode = "lightwake"
        elif st.timers.nap is not None and st.audio.active_source == "none":
            mode = "countdown"
        elif st.audio.active_source != "none":
            mode = "playing"
        elif st.timers.nap is not None:
            mode = "countdown"
        elif st.system.network.hotspot_active and f.setup is not None:
            mode = "setup"
        elif f.message is not None and (f.message.until is None or datetime.fromisoformat(f.message.until) > now):
            mode = "message"
        else:
            mode = "standby"
        if mode != f.mode:
            f.mode = mode  # type: ignore[assignment]
            if mode == "ringing":
                f.menu_open = False
            self.ctx.store.touch()

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(1)
            try:
                self._expire()
            except Exception:  # noqa: BLE001
                log.exception("face tick")

    def _expire(self) -> None:
        st = self.ctx.store.state
        f = st.face
        now = self.ctx.store.now()
        changed = False
        if f.menu_open and self._menu_opened_at and (now - self._menu_opened_at).total_seconds() > self.ctx.config.inputs.touch_menu_timeout_s:
            f.menu_open, f.menu_page = False, None
            changed = True
        if f.wake_until and datetime.fromisoformat(f.wake_until) <= now:
            f.wake_until = None
            changed = True
        if f.message and f.message.until and datetime.fromisoformat(f.message.until) <= now:
            f.message = None
            changed = True
        if changed:
            self.ctx.store.touch()
        self.recompute()

    # ---- actions ---------------------------------------------------------
    def wake(self, seconds: int | None = None) -> None:
        secs = seconds or self.ctx.config.inputs.standby_wake_s
        until = self.ctx.store.now() + timedelta(seconds=secs)
        self.ctx.store.state.face.wake_until = until.isoformat(timespec="seconds")
        self.ctx.store.touch()

    def touch(self) -> None:
        """Touch while not ringing: open the minimal menu (and wake the display)."""
        f = self.ctx.store.state.face
        if f.mode == "message":
            self.dismiss_message()
            return
        self.wake()
        if f.menu_open:
            f.menu_open, f.menu_page = False, None
        else:
            f.menu_open, f.menu_page = True, None
            self._menu_opened_at = self.ctx.store.now()
        self.ctx.store.touch()

    def open_menu(self, page: str | None = None) -> None:
        f = self.ctx.store.state.face
        f.menu_open, f.menu_page = True, page or None
        self._menu_opened_at = self.ctx.store.now()
        self.wake()
        self.ctx.store.touch()

    def set_menu(self, open_: bool, page: str | None = None) -> None:
        f = self.ctx.store.state.face
        f.menu_open = open_
        f.menu_page = page or None
        if open_:
            self._menu_opened_at = self.ctx.store.now()
        self.ctx.store.touch()

    def menu_activity(self) -> bool:
        """Re-arm the menu auto-close timer: the face reports a finger on the sheet (a drag on the
        volume slider, a tap on a tile) so the menu never closes under someone's hand."""
        f = self.ctx.store.state.face
        if not f.menu_open:
            return False
        self._menu_opened_at = self.ctx.store.now()
        self.wake()
        return True

    def toast(self, title: str, body: str = "", level: str = "info", seconds: int | None = 8) -> None:
        until = (self.ctx.store.now() + timedelta(seconds=seconds)).isoformat(timespec="seconds") if seconds else None
        self.ctx.store.state.face.message = FaceMessage(title=title, body=body, level=level, until=until)  # type: ignore[arg-type]
        self.ctx.store.touch()
        self.recompute()

    def dismiss_message(self) -> None:
        self.ctx.store.state.face.message = None
        self.ctx.store.touch()
        self.recompute()

    def set_shutdown_countdown(self, seconds: int | None) -> None:
        self.ctx.store.state.face.shutdown_countdown = seconds
        self.ctx.store.touch()

    def set_demo(self, mode: str | None) -> None:
        """Force a face mode (screenshots/tests)."""
        self._demo_mode = mode
        self.recompute()
