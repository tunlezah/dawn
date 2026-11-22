"""Sleep timer (fade to standby) and nap timer (rings the chime).

Both are durations ("in 20 minutes"), so they run on the monotonic clock: a chrony step at boot or a hand change of
the time neither shortens nor stretches them. What is shown and saved (`ends_at`) is derived from the monotonic
deadline on every publish (wall now + time left). Alarms are wall-clock times and live in AlarmService.

`ClockWatch`, run from the timers' 1 s tick, notices the wall clock jumping against the monotonic one, logs a
`clock_step` event (which also explains "the alarm rang late" in the event log) and republishes the timers and the
ring so their countdowns are right again at once.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from ..audio.service import AudioService
from ..context import DawnContext
from ..services import Service
from ..state.ui import TimerInfo
from ..tasks import spawn

log = logging.getLogger("dawn.timers")

NAP_RING_RETRIES = 3  # after the first try fails
NAP_RETRY_S = 5.0
STEP_BUDGET_S = 10.0  # a sleep-timer step (a volume change, stopping the source) that takes longer is given up on
CLOCK_STEP_S = 2.0  # a wall-clock jump against the monotonic clock larger than this is a clock step


class ClockWatch:
    """Compares how far the wall clock and the monotonic clock moved since the last look."""

    def __init__(self, threshold_s: float = CLOCK_STEP_S):
        self.threshold_s = threshold_s
        self._last: tuple[float, float] | None = None

    def check(self, wall: float, mono: float) -> float | None:
        """The step in seconds (positive: the clock jumped forward) when it is over the threshold, else None."""
        prev, self._last = self._last, (wall, mono)
        if prev is None:
            return None
        step = (wall - prev[0]) - (mono - prev[1])
        return step if abs(step) > self.threshold_s else None


class TimerService(Service):
    name = "timers"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self._sleep_task: asyncio.Task[None] | None = None
        self._nap_task: asyncio.Task[None] | None = None
        self._sleep_deadline: float | None = None  # monotonic
        self._sleep_total = 0
        self._sleep_fading = False
        self._sleep_prev_volume: int | None = None
        self._nap_deadline: float | None = None  # monotonic
        self._nap_total = 0
        self._tick_task: asyncio.Task[None] | None = None
        self._clock = ClockWatch()
        # seams for the tests: the monotonic clock and the wait that runs on it
        self._mono: Callable[[], float] = time.monotonic
        self._sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep

    async def start(self) -> None:
        if self._tick_task is None or self._tick_task.done():
            self._tick_task = spawn(self._tick(), name="timers-tick")
        self._restore_nap()
        self.publish()

    def _restore_nap(self) -> None:
        """A nap counting down when dawn-core restarted carries on, or rings now if it ran out meanwhile. Across a
        restart the monotonic clock is gone, so this one step uses the wall-clock time that was saved."""
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
        self._nap_total = total
        self._nap_deadline = self._mono() + max(0.0, (end - now).total_seconds())
        self._nap_task = asyncio.create_task(self._run_nap(), name="nap-timer")
        log.warning("nap timer carried on after a restart (ends %s)", end.isoformat(timespec="seconds"))

    async def stop(self) -> None:
        for t in (self._sleep_task, self._nap_task, self._tick_task):
            if t:
                t.cancel()

    def _audio(self) -> AudioService:
        return self.ctx.svc(AudioService)

    def _left(self, deadline: float) -> float:
        return max(0.0, deadline - self._mono())

    def _ends_at(self, deadline: float) -> datetime:
        return self.ctx.store.now() + timedelta(seconds=deadline - self._mono())

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
        self._sleep_deadline = self._mono() + self._sleep_total
        self._sleep_fading = False
        self._sleep_task = asyncio.create_task(self._run_sleep(), name="sleep-timer")
        self.ctx.db.log_event("sleep_start", minutes=minutes)
        self.publish()

    async def _run_sleep(self) -> None:
        audio = self._audio()
        fade_s = self.ctx.config.timers.sleep_fade_s
        cancelled = False
        try:
            deadline = self._sleep_deadline if self._sleep_deadline is not None else self._mono() + self._sleep_total
            await self._sleep(max(0.0, deadline - fade_s - self._mono()))
            self._sleep_fading = True
            self.publish()
            self._sleep_prev_volume = audio.volume
            start = audio.volume
            t0 = time.monotonic()
            step_failed = False
            while fade_s > 0:
                if self.ctx.store.state.alarms.ringing is not None:
                    log.info("sleep timer: an alarm rings; the fade ends now")
                    break  # the alarm's ramp owns the volume from here
                frac = min(1.0, (time.monotonic() - t0) / fade_s)
                try:
                    await asyncio.wait_for(audio.set_volume(round(start * (1 - frac)), persist=False, overlay=False), STEP_BUDGET_S)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    # a hung or failing PipeWire: the fade carries on (and ends) rather than leaving the radio on all night
                    if not step_failed:
                        log.exception("sleep timer: a fade step failed; carrying on")
                        step_failed = True
                if frac >= 1.0:
                    break
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            cancelled = True  # cancel_sleep: it decides what happens to the source and the volume
            raise
        finally:
            if not cancelled:
                await self._end_sleep(audio, self._sleep_prev_volume)
            self._sleep_deadline = None
            self._sleep_fading = False
            self._sleep_prev_volume = None
            self.publish()

    async def _end_sleep(self, audio: AudioService, prev_volume: int | None) -> None:
        """The timer ran out: stop the sleep source and put the volume back, each step on its own and bounded, so a
        failure in one (or in the fade before them) cannot leave the radio playing all night."""
        try:
            await asyncio.wait_for(audio.stop_level("sleep"), STEP_BUDGET_S)
        except Exception:  # noqa: BLE001
            log.exception("sleep timer: stopping the sleep source failed")
        try:
            if prev_volume is not None and not self._hand_volume_to_ring(prev_volume):
                await asyncio.wait_for(audio.set_volume(prev_volume, persist=False, overlay=False), STEP_BUDGET_S)
        except Exception:  # noqa: BLE001
            log.exception("sleep timer: putting the volume back failed")
        try:
            self.ctx.db.log_event("sleep_end")
        except Exception:  # noqa: BLE001
            log.debug("could not log sleep_end", exc_info=True)

    def _hand_volume_to_ring(self, volume: int | None) -> bool:
        """A ring that started during the fade took the fade's half-way volume as the one to put back when it stops;
        give it the volume from before the fade instead, and leave the volume itself to the ring's ramp."""
        if volume is None:
            return False
        from .service import AlarmService

        try:
            ring = self.ctx.svc(AlarmService).ring
        except KeyError:
            return False
        if ring is None or not ring.active:
            return False
        ring.prev_volume = volume
        return True

    async def cancel_sleep(self, resume: bool = True) -> None:
        audio = self._audio()
        if self._sleep_task and not self._sleep_task.done():
            prev = self._sleep_prev_volume
            self._sleep_task.cancel()
            try:
                await self._sleep_task
            except asyncio.CancelledError:
                pass
            if prev is not None:
                await audio.set_volume(prev, persist=False, overlay=False)
            self.ctx.db.log_event("sleep_cancel")
        self._sleep_task = None
        self._sleep_deadline = None
        self._sleep_fading = False
        if resume and audio.arbiter.slot("sleep") is not None:
            await audio.arbiter.move("sleep", "user")  # keep playing, just no timer
        self.publish()

    # ---- nap -------------------------------------------------------------
    async def start_nap(self, minutes: int) -> None:
        await self.cancel_nap(log=False)
        self._nap_total = minutes * 60
        self._nap_deadline = self._mono() + self._nap_total
        self._nap_task = asyncio.create_task(self._run_nap(), name="nap-timer")
        self._save_nap()
        self.ctx.db.log_event("nap_start", minutes=minutes)
        self.publish()

    def _save_nap(self) -> None:
        if self._nap_deadline is not None:
            self.ctx.db.try_set("timers.nap", {"ends_at": self._ends_at(self._nap_deadline).isoformat(), "total_s": self._nap_total})

    async def _run_nap(self) -> None:
        """Wait out the nap, then ring. The saved nap is kept until a ring session exists, so a ring that cannot start
        (or a restart part way through) does not lose it; a ring that keeps failing gets the backup tone directly."""
        if self._nap_deadline is not None:
            await self._sleep(self._left(self._nap_deadline))
        from .service import AlarmService

        alarms = self.ctx.svc(AlarmService)
        for attempt in range(1 + NAP_RING_RETRIES):
            try:
                await alarms.ring_nap()
                break
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.error("nap timer: the ring would not start (attempt %d): %r", attempt + 1, e)
                try:
                    self.ctx.db.log_event("nap_ring_failed", attempt=attempt + 1, error=f"{type(e).__name__}: {e}"[:200])
                except Exception:  # noqa: BLE001
                    pass
                if attempt < NAP_RING_RETRIES:
                    await self._sleep(NAP_RETRY_S)
        else:
            await self._nap_last_resort(alarms)
        self._nap_deadline = None
        self.ctx.db.try_set("timers.nap", None)  # from here on the ring itself is saved
        self.publish()

    async def _nap_last_resort(self, alarms: Any) -> None:
        """No ring session could be made: the backup tone straight, and the face beeps along, as a ring does when even
        the audio arbiter fails it. Stop (button, hold, the web) ends it like any ring."""
        from ..state.ui import RingingInfo

        log.error("nap timer: no ring after %d tries; sounding the backup tone directly", 1 + NAP_RING_RETRIES)
        try:
            await alarms.buzzer.start()
        except Exception:  # noqa: BLE001
            log.exception("nap timer: the backup tone would not start either")
        now = self.ctx.store.now()
        max_min = self.ctx.config.alarm_defaults.max_ring_minutes
        self.ctx.store.state.alarms.ringing = RingingInfo(
            kind="nap", label="Nap", started_at=now.isoformat(timespec="seconds"), source="buzzer:", fallback=True, tier="buzzer",
            fallback_reason="the nap's ring would not start", face_beep=True,
            ends_at=(now + timedelta(minutes=max_min)).isoformat(timespec="seconds"),
        )
        self.ctx.store.touch()

        async def max_ring() -> None:
            await asyncio.sleep(max_min * 60)
            await alarms.stop_orphan_tone("max_ring")

        spawn(max_ring(), name="nap-backup-tone-max", owner="timers")

    async def cancel_nap(self, log: bool = True) -> None:
        if self._nap_task and not self._nap_task.done():
            self._nap_task.cancel()
            if log:
                self.ctx.db.log_event("nap_cancel")
        self._nap_task = None
        if self._nap_deadline is not None:
            self.ctx.db.try_set("timers.nap", None)
        self._nap_deadline = None
        self.publish()

    # ---- state -----------------------------------------------------------
    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            try:
                self.watch_clock()
            except Exception:  # noqa: BLE001
                log.exception("clock watch failed")
            if self._sleep_deadline is not None or self._nap_deadline is not None:
                self.publish()

    def watch_clock(self, wall: float | None = None, mono: float | None = None) -> float | None:
        """One ClockWatch look; on a step, log it and put the right countdowns on the face now."""
        step = self._clock.check(time.time() if wall is None else wall, self._mono() if mono is None else mono)
        if step is None:
            return None
        log.warning("the system clock stepped by %+.1f s", step)
        try:
            self.ctx.db.log_event("clock_step", step_s=round(step, 1))
        except Exception:  # noqa: BLE001
            pass
        self._save_nap()  # the saved end is a wall-clock time
        self.publish()
        from .service import AlarmService

        try:
            ring = self.ctx.svc(AlarmService).ring
        except KeyError:
            ring = None
        if ring is not None and ring.active:
            ring.on_clock_step()
        return step

    def publish(self) -> None:
        st = self.ctx.store.state.timers
        sd, nd = self._sleep_deadline, self._nap_deadline
        st.sleep = TimerInfo(kind="sleep", ends_at=self._ends_at(sd).isoformat(timespec="seconds"), total_s=self._sleep_total,
                             remaining_s=int(self._left(sd)), fading=self._sleep_fading) if sd is not None else None
        st.nap = TimerInfo(kind="nap", ends_at=self._ends_at(nd).isoformat(timespec="seconds"), total_s=self._nap_total,
                           remaining_s=int(self._left(nd))) if nd is not None else None
        st.sleep_choices = list(self.ctx.config.timers.sleep_choices_min)
        st.nap_choices = list(self.ctx.config.timers.nap_choices_min)
        self.ctx.store.touch()
