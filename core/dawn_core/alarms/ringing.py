"""A ring session: one alarm or nap ringing, with ramp, fallback, snooze, max ring."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from ..audio.service import AudioService
from ..context import DawnContext
from ..state.ui import RingingInfo

log = logging.getLogger("dawn.ring")


@dataclass
class RingRequest:
    kind: str  # alarm | nap | test
    label: str
    source: str
    volume: int
    ramp_seconds: int = 60
    ramp_start_percent: int = 10
    snooze_minutes: int = 9
    fallback_after_s: int = 15
    max_ring_minutes: int = 30
    chime: str = "gentle_bell"
    alarm_id: int | None = None
    occurrence: str | None = None


class RingSession:
    def __init__(self, ctx: DawnContext, req: RingRequest, on_done: Callable[[RingSession, str], Awaitable[None] | None]):
        self.ctx = ctx
        self.req = req
        self.on_done = on_done
        self.audio: AudioService = ctx.svc(AudioService)
        self.started_at = ctx.store.now()
        self.source_started = time.monotonic()
        self.prev_volume = self.audio.volume
        self.prev_muted = self.audio.muted
        self.fallback = False
        self.restarted_dab = False
        self.snooze_count = 0
        self.snoozed_until = None
        self.ring_elapsed = 0.0
        self.had_audio = False
        self.active = False
        self._tasks: list[asyncio.Task[None]] = []
        self._snooze_task: asyncio.Task[None] | None = None
        self._max_task: asyncio.Task[None] | None = None
        self._stopping = False

    # ---- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        self.active = True
        self.ctx.db.log_event("ring_start", ring_kind=self.req.kind, label=self.req.label, source=self.req.source, alarm_id=self.req.alarm_id)
        self._publish()
        await self._start_source(self.req.source)
        self._max_task = asyncio.create_task(self._max_ring(), name="ring-max")

    async def _start_source(self, ref: str) -> None:
        self.source_started = time.monotonic()
        self.had_audio = False
        await self.audio.set_volume(self._ramp_start(), persist=False, overlay=False)
        if ref.startswith("dab:") and not self.ctx.store.state.dab.sdr_present:
            log.warning("alarm source %s but no SDR present; using the chime", ref)
            await self._fallback("no SDR")
            return
        try:
            slot = await self.audio.play(ref, level="alarm", remember=False)
            if slot.state == "error":
                raise RuntimeError(slot.source.error or "source failed")
        except Exception as e:  # noqa: BLE001
            log.warning("alarm source %s failed (%s); using the chime", ref, e)
            await self._fallback(str(e))
            return
        self._cancel_tasks()
        self._tasks = [asyncio.create_task(self._ramp(), name="ring-ramp"), asyncio.create_task(self._watchdog(), name="ring-watchdog")]

    def _ramp_start(self) -> int:
        if self.req.ramp_seconds <= 0:
            return self.req.volume
        return min(self.req.volume, max(1, self.req.ramp_start_percent))

    async def _ramp(self) -> None:
        start, target, secs = self._ramp_start(), self.req.volume, self.req.ramp_seconds
        if secs <= 0 or start >= target:
            await self.audio.set_volume(target, persist=False, overlay=False)
            return
        t0 = time.monotonic()
        while True:
            frac = min(1.0, (time.monotonic() - t0) / secs)
            v = round(start + (target - start) * frac)
            await self.audio.set_volume(v, persist=False, overlay=False)
            if frac >= 1.0:
                return
            await asyncio.sleep(0.5)

    async def _watchdog(self) -> None:
        """Fallback to the chime if no audio flows within fallback_after_s.
        For DAB: if welle-cli looks dead early on, restart it once (then keep waiting
        within the same window)."""
        while True:
            await asyncio.sleep(1.0)
            if self.fallback:
                return
            flowing = await self.audio.audio_flowing()
            elapsed = time.monotonic() - self.source_started
            if flowing:
                self.had_audio = True
                continue
            if self.req.source.startswith("dab:") and not self.restarted_dab and elapsed >= 3:
                try:
                    from ..dab.service import DabService

                    dab = self.ctx.svc(DabService)
                    if not await dab.healthy():
                        self.restarted_dab = True
                        log.warning("DAB not healthy during ring; restarting welle-cli once")
                        await dab.restart_welle()
                except Exception:  # noqa: BLE001
                    log.exception("dab restart attempt failed")
            if elapsed >= self.req.fallback_after_s:
                log.warning("no audio after %.0fs; falling back to the chime", elapsed)
                await self._fallback("no audio flowing")
                return

    async def _fallback(self, reason: str) -> None:
        if self.fallback:
            return
        self.fallback = True
        self.ctx.db.log_event("ring_fallback", ring_kind=self.req.kind, label=self.req.label, reason=reason, source=self.req.source)
        try:
            await self.audio.play(f"chime:{self.req.chime}", level="alarm", remember=False)
        except Exception:  # noqa: BLE001
            log.exception("chime fallback failed")
        self._cancel_tasks()
        self._tasks = [asyncio.create_task(self._ramp(), name="ring-ramp")]
        self._publish()

    async def _max_ring(self) -> None:
        limit = self.req.max_ring_minutes * 60
        while self.ring_elapsed < limit:
            await asyncio.sleep(1.0)
            if self.snoozed_until is None:
                self.ring_elapsed += 1.0
        log.warning("%s rang for %d min without response; stopping", self.req.label, self.req.max_ring_minutes)
        await self.stop("max_ring")

    # ---- snooze ----------------------------------------------------------
    async def snooze(self) -> bool:
        if not self.active or self.snoozed_until is not None:
            return False
        self.snooze_count += 1
        self._cancel_tasks()
        await self.audio.arbiter.pause_level("alarm")
        until = self.ctx.store.now() + timedelta(minutes=self.req.snooze_minutes)
        self.snoozed_until = until
        self.ctx.db.log_event("ring_snooze", ring_kind=self.req.kind, label=self.req.label, count=self.snooze_count, until=until.isoformat())
        self._snooze_task = asyncio.create_task(self._wake_from_snooze(self.req.snooze_minutes * 60), name="ring-snooze")
        self._publish()
        return True

    async def _wake_from_snooze(self, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        self.snoozed_until = None
        self.ctx.db.log_event("ring_resume", ring_kind=self.req.kind, label=self.req.label)
        ref = f"chime:{self.req.chime}" if self.fallback else self.req.source
        self.fallback = False
        await self._start_source(ref)
        self._publish()

    # ---- stop ------------------------------------------------------------
    async def stop(self, reason: str = "user") -> None:
        if self._stopping:
            return
        self._stopping = True
        self.active = False
        self._cancel_tasks()
        for t in (self._snooze_task, self._max_task):
            if t and not t.done() and t is not asyncio.current_task():
                t.cancel()
        try:
            await self.audio.stop_level("alarm")
        finally:
            await self.audio.set_volume(self.prev_volume, persist=False, overlay=False)
            if self.prev_muted:
                await self.audio.set_mute(True, overlay=False)
        self.ctx.db.log_event("ring_stop", ring_kind=self.req.kind, label=self.req.label, reason=reason, snoozes=self.snooze_count, fallback=self.fallback)
        self.ctx.store.state.alarms.ringing = None
        self.ctx.store.touch()
        r = self.on_done(self, reason)
        if asyncio.iscoroutine(r):
            await r

    def _cancel_tasks(self) -> None:
        for t in self._tasks:
            if not t.done():
                t.cancel()
        self._tasks = []

    # ---- state -----------------------------------------------------------
    def _publish(self) -> None:
        ends = self.started_at + timedelta(minutes=self.req.max_ring_minutes)
        self.ctx.store.state.alarms.ringing = RingingInfo(
            kind="nap" if self.req.kind == "nap" else "alarm",
            alarm_id=self.req.alarm_id,
            label=self.req.label,
            started_at=self.started_at.isoformat(timespec="seconds"),
            snoozed_until=self.snoozed_until.isoformat(timespec="seconds") if self.snoozed_until else None,
            snooze_count=self.snooze_count,
            source=f"chime:{self.req.chime}" if self.fallback else self.req.source,
            fallback=self.fallback,
            volume_target=self.req.volume,
            ends_at=ends.isoformat(timespec="seconds"),
        )
        self.ctx.store.touch()
