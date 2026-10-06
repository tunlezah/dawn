"""The audio arbiter: one output, strict source priority.

Levels (high to low): alarm/nap ringing > sleep-timer playback > user source >
AirPlay > Bluetooth. A higher-priority start ducks lower sources to
`duck_percent` for `duck_seconds`, then pauses them. When the higher source is
released, the highest remaining source that was playing before is resumed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .sources import AudioSource

log = logging.getLogger("dawn.audio.arbiter")

LEVELS: dict[str, int] = {"alarm": 100, "sleep": 80, "user": 60, "airplay": 40, "bluetooth": 20}


@dataclass
class Slot:
    level: str
    priority: int
    source: AudioSource
    state: str = "idle"  # idle|starting|playing|ducked|paused|error
    was_playing: bool = False
    duck_task: asyncio.Task[None] | None = field(default=None, repr=False)


class Arbiter:
    def __init__(self, duck_percent: int = 20, duck_seconds: float = 2.0, on_change: Callable[[], Awaitable[None] | None] | None = None):
        self.duck_percent = duck_percent
        self.duck_seconds = duck_seconds
        self.slots: dict[str, Slot] = {}
        self._lock = asyncio.Lock()
        self._on_change = on_change

    # ---- queries ---------------------------------------------------------
    @property
    def active(self) -> Slot | None:
        live = [s for s in self.slots.values() if s.state in ("playing", "starting", "ducked")]
        return max(live, key=lambda s: s.priority) if live else None

    def slot(self, level: str) -> Slot | None:
        return self.slots.get(level)

    def _top(self) -> Slot | None:
        return max(self.slots.values(), key=lambda s: s.priority) if self.slots else None

    # ---- mutation --------------------------------------------------------
    async def acquire(self, level: str, source: AudioSource) -> Slot:
        """Start `source` at `level`, preempting anything lower. Replaces a source already at that level."""
        async with self._lock:
            old = self.slots.get(level)
            if old is not None:
                await self._stop_slot(old)
            slot = Slot(level=level, priority=LEVELS[level], source=source, state="starting")
            self.slots[level] = slot
            source.set_update_callback(self._changed)
            top = self._top()
            if top is slot:
                for other in self.slots.values():
                    if other is not slot and other.state in ("playing", "starting", "ducked"):
                        await self._preempt(other)
                await self._start_slot(slot)
            else:
                # something higher is active: hold this one paused until it is released
                slot.was_playing = True
                slot.state = "paused"
                log.info("%s queued behind %s", level, top.level if top else "?")
        self._changed()
        return slot

    async def release(self, level: str) -> None:
        async with self._lock:
            slot = self.slots.pop(level, None)
            if slot is None:
                return
            was_top = self._top() is None or slot.priority > self._top().priority  # type: ignore[union-attr]
            await self._stop_slot(slot)
            if was_top:
                await self._resume_next()
        self._changed()

    async def pause_level(self, level: str) -> None:
        """Pause without releasing (used by snooze and by external sources)."""
        async with self._lock:
            slot = self.slots.get(level)
            if slot and slot.state in ("playing", "ducked", "starting"):
                await self._cancel_duck(slot)
                try:
                    await slot.source.pause()
                except Exception:  # noqa: BLE001
                    log.exception("pause failed")
                slot.state = "paused"
                slot.was_playing = False
                await self._resume_next()
        self._changed()

    async def resume_level(self, level: str) -> None:
        async with self._lock:
            slot = self.slots.get(level)
            if slot and slot.state == "paused":
                top = self._top()
                if top is slot:
                    for other in self.slots.values():
                        if other is not slot and other.state in ("playing", "starting", "ducked"):
                            await self._preempt(other)
                    await slot.source.set_gain(100)
                    await slot.source.resume()
                    slot.state = "playing"
                else:
                    slot.was_playing = True
        self._changed()

    async def move(self, src_level: str, dst_level: str) -> None:
        """Re-file a slot at another level without touching its source (sleep timer promotion)."""
        async with self._lock:
            slot = self.slots.pop(src_level, None)
            if slot is None:
                return
            old = self.slots.pop(dst_level, None)
            if old is not None:
                await self._stop_slot(old)
            slot.level, slot.priority = dst_level, LEVELS[dst_level]
            self.slots[dst_level] = slot
            top = self._top()
            if top is slot and slot.state in ("playing", "starting", "ducked"):
                for other in self.slots.values():
                    if other is not slot and other.state in ("playing", "starting", "ducked"):
                        await self._preempt(other)
        self._changed()

    async def release_all(self, below: int | None = None) -> None:
        for level in list(self.slots):
            if below is None or LEVELS[level] < below:
                await self.release(level)

    # ---- internals -------------------------------------------------------
    async def _start_slot(self, slot: Slot) -> None:
        try:
            await slot.source.set_gain(100)
            await slot.source.start()
            slot.state = "playing"
            log.info("%s playing: %s", slot.level, slot.source.ref or slot.source.label)
        except Exception as e:  # noqa: BLE001
            slot.state = "error"
            slot.source.error = str(e)
            log.exception("source %s failed to start", slot.source.ref)

    async def _stop_slot(self, slot: Slot) -> None:
        await self._cancel_duck(slot)
        try:
            await slot.source.stop()
        except Exception:  # noqa: BLE001
            log.exception("stop failed for %s", slot.source.ref)
        slot.state = "idle"

    async def _preempt(self, slot: Slot) -> None:
        slot.was_playing = True
        slot.state = "ducked"
        try:
            await slot.source.set_gain(self.duck_percent)
        except Exception:  # noqa: BLE001
            log.exception("duck failed")
        slot.duck_task = asyncio.create_task(self._finish_duck(slot), name=f"duck-{slot.level}")

    async def _finish_duck(self, slot: Slot) -> None:
        try:
            await asyncio.sleep(self.duck_seconds)
            try:
                await slot.source.pause()
            except Exception:  # noqa: BLE001
                log.exception("pause after duck failed")
            if slot.state == "ducked":
                slot.state = "paused"
            self._changed()
        except asyncio.CancelledError:
            pass

    async def _cancel_duck(self, slot: Slot) -> None:
        if slot.duck_task and not slot.duck_task.done():
            slot.duck_task.cancel()
            try:
                await slot.duck_task
            except asyncio.CancelledError:
                pass
        slot.duck_task = None

    async def _resume_next(self) -> None:
        cands = [s for s in self.slots.values() if s.state in ("paused", "ducked") and s.was_playing]
        if not cands:
            return
        nxt = max(cands, key=lambda s: s.priority)
        top = self._top()
        if top is not None and top is not nxt and top.state in ("playing", "starting"):
            return
        await self._cancel_duck(nxt)
        try:
            await nxt.source.set_gain(100)
            await nxt.source.resume()
            nxt.state = "playing"
            nxt.was_playing = False
            log.info("%s resumed: %s", nxt.level, nxt.source.ref)
        except Exception as e:  # noqa: BLE001
            nxt.state = "error"
            nxt.source.error = str(e)

    def _changed(self) -> None:
        """Tell the owner (it republishes the state). Its failure is logged, never raised: the source has already
        started or stopped by then, and the caller must not take it for that having failed."""
        if self._on_change:
            try:
                r = self._on_change()
            except Exception:  # noqa: BLE001
                log.exception("arbiter change callback failed")
                return
            if asyncio.iscoroutine(r):
                asyncio.ensure_future(r)
