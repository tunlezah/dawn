"""Sleep timer (fade to standby) and nap timer (rings the chime)."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

from ..audio.service import AudioService
from ..context import DawnContext
from ..services import Service
from ..state.ui import TimerInfo

log = logging.getLogger("dawn.timers")


class TimerService(Service):
    name = "timers"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self._sleep_task: asyncio.Task[None] | None = None
        self._nap_task: asyncio.Task[None] | None = None
        self._sleep_end: datetime | None = None
        self._sleep_total = 0
        self._sleep_fading = False
        self._sleep_prev_volume: int | None = None
        self._nap_end: datetime | None = None
        self._nap_total = 0
        self._tick_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._tick_task = asyncio.create_task(self._tick(), name="timers-tick")
        self._restore_nap()
        self.publish()

    def _restore_nap(self) -> None:
        """A nap counting down when dawn-core restarted carries on, or rings now if it ran out meanwhile."""
        try:
            data = self.ctx.db.get("timers.nap")
        except Exception:  # noqa: BLE001
            return
        if not isinstance(data, dict):
            return
        try:
            end, total = datetime.fromisoformat(data["ends_at"]), int(data["total_s"])
        except (KeyError, TypeError, ValueError):
            self.ctx.db.try_set("timers.nap", None)
            return
        now = self.ctx.store.now()
        if end < now - timedelta(minutes=self.ctx.config.alarm_defaults.missed_grace_minutes) or end > now + timedelta(seconds=total + 60):
            self.ctx.db.try_set("timers.nap", None)
            return
        self._nap_total, self._nap_end = total, end
        self._nap_task = asyncio.create_task(self._run_nap(max(0.0, (end - now).total_seconds())), name="nap-timer")
        log.warning("nap timer carried on after a restart (ends %s)", end.isoformat(timespec="seconds"))

    async def stop(self) -> None:
        for t in (self._sleep_task, self._nap_task, self._tick_task):
            if t:
                t.cancel()

    def _audio(self) -> AudioService:
        return self.ctx.svc(AudioService)

    # ---- sleep -----------------------------------------------------------
    async def start_sleep(self, minutes: int) -> None:
        audio = self._audio()
        await self.cancel_sleep(resume=False)
        user = audio.arbiter.slot("user")
        if user and user.state in ("playing", "ducked", "starting", "paused"):
            await audio.arbiter.move("user", "sleep")
        elif audio.arbiter.slot("sleep") is None:
            await audio.play("last-played", level="sleep", remember=False)
        self._sleep_total = minutes * 60
        self._sleep_end = self.ctx.store.now() + timedelta(seconds=self._sleep_total)
        self._sleep_fading = False
        self._sleep_task = asyncio.create_task(self._run_sleep(), name="sleep-timer")
        self.ctx.db.log_event("sleep_start", minutes=minutes)
        self.publish()

    async def _run_sleep(self) -> None:
        audio = self._audio()
        fade_s = self.ctx.config.timers.sleep_fade_s
        try:
            await asyncio.sleep(max(0, self._sleep_total - fade_s))
            self._sleep_fading = True
            self.publish()
            self._sleep_prev_volume = audio.volume
            start = audio.volume
            t0 = time.monotonic()
            while fade_s > 0:
                frac = min(1.0, (time.monotonic() - t0) / fade_s)
                await audio.set_volume(round(start * (1 - frac)), persist=False, overlay=False)
                if frac >= 1.0:
                    break
                await asyncio.sleep(1.0)
            await audio.stop_level("sleep")
            await audio.set_volume(self._sleep_prev_volume, persist=False, overlay=False)
            self.ctx.db.log_event("sleep_end")
        except asyncio.CancelledError:
            raise
        finally:
            self._sleep_end = None
            self._sleep_fading = False
            self._sleep_prev_volume = None
            self.publish()

    async def cancel_sleep(self, resume: bool = True) -> None:
        audio = self._audio()
        if self._sleep_task and not self._sleep_task.done():
            self._sleep_task.cancel()
            try:
                await self._sleep_task
            except asyncio.CancelledError:
                pass
            if self._sleep_prev_volume is not None:
                await audio.set_volume(self._sleep_prev_volume, persist=False, overlay=False)
            self.ctx.db.log_event("sleep_cancel")
        self._sleep_task = None
        self._sleep_end = None
        self._sleep_fading = False
        if resume and audio.arbiter.slot("sleep") is not None:
            await audio.arbiter.move("sleep", "user")  # keep playing, just no timer
        self.publish()

    # ---- nap -------------------------------------------------------------
    async def start_nap(self, minutes: int) -> None:
        await self.cancel_nap(log=False)
        self._nap_total = minutes * 60
        self._nap_end = self.ctx.store.now() + timedelta(seconds=self._nap_total)
        self._nap_task = asyncio.create_task(self._run_nap(), name="nap-timer")
        self.ctx.db.try_set("timers.nap", {"ends_at": self._nap_end.isoformat(), "total_s": self._nap_total})
        self.ctx.db.log_event("nap_start", minutes=minutes)
        self.publish()

    async def _run_nap(self, seconds: float | None = None) -> None:
        await asyncio.sleep(self._nap_total if seconds is None else seconds)
        self._nap_end = None
        self.ctx.db.try_set("timers.nap", None)  # from here on the ring itself is saved
        self.publish()
        from .service import AlarmService

        await self.ctx.svc(AlarmService).ring_nap()

    async def cancel_nap(self, log: bool = True) -> None:
        if self._nap_task and not self._nap_task.done():
            self._nap_task.cancel()
            if log:
                self.ctx.db.log_event("nap_cancel")
        self._nap_task = None
        if self._nap_end is not None:
            self.ctx.db.try_set("timers.nap", None)
        self._nap_end = None
        self.publish()

    # ---- state -----------------------------------------------------------
    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self._sleep_end or self._nap_end:
                self.publish()

    def publish(self) -> None:
        now = self.ctx.store.now()
        st = self.ctx.store.state.timers
        st.sleep = TimerInfo(kind="sleep", ends_at=self._sleep_end.isoformat(timespec="seconds"), total_s=self._sleep_total, remaining_s=max(0, int((self._sleep_end - now).total_seconds())), fading=self._sleep_fading) if self._sleep_end else None
        st.nap = TimerInfo(kind="nap", ends_at=self._nap_end.isoformat(timespec="seconds"), total_s=self._nap_total, remaining_s=max(0, int((self._nap_end - now).total_seconds()))) if self._nap_end else None
        st.sleep_choices = list(self.ctx.config.timers.sleep_choices_min)
        st.nap_choices = list(self.ctx.config.timers.nap_choices_min)
        self.ctx.store.touch()
