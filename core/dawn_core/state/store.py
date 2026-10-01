"""Central state store.

Services mutate `store.state.<section>` through `store.update(...)` (which
bumps the version and schedules a broadcast) or `store.patch(section, **kw)`.
WebSocket clients always receive the full state; broadcasts are coalesced so a
burst of updates produces one message.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .ui import UIState

log = logging.getLogger("dawn.state")

Subscriber = Callable[[UIState], Awaitable[None] | None]


class StateStore:
    def __init__(self, tz: str = "Australia/Sydney", coalesce_ms: int = 40):
        self.state = UIState(tz=tz)
        self.tz = ZoneInfo(tz)
        self.coalesce_s = coalesce_ms / 1000
        self._subs: list[Subscriber] = []
        self._dirty = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self.events: list[dict[str, Any]] = []  # recent one-shot events (for /api/events)

    # ---- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._pump(), name="state-pump")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def set_timezone(self, tz: str) -> None:
        self.tz = ZoneInfo(tz)
        self.state.tz = tz
        self.touch()

    # ---- helpers ---------------------------------------------------------
    def now(self) -> datetime:
        return datetime.now(self.tz)

    def iso(self, dt: datetime | None = None) -> str:
        return (dt or self.now()).isoformat(timespec="seconds")

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        self._subs.append(fn)

        def unsub() -> None:
            if fn in self._subs:
                self._subs.remove(fn)

        return unsub

    # ---- mutation --------------------------------------------------------
    def touch(self) -> None:
        """Mark the state changed; broadcast soon."""
        self.state.version += 1
        self._dirty.set()

    def update(self, fn: Callable[[UIState], None]) -> None:
        fn(self.state)
        self.touch()

    def patch(self, section: str, **fields: Any) -> None:
        obj = getattr(self.state, section)
        for k, v in fields.items():
            setattr(obj, k, v)
        self.touch()

    def replace(self, section: str, value: Any) -> None:
        setattr(self.state, section, value)
        self.touch()

    def emit(self, kind: str, **data: Any) -> None:
        """Record a one-shot event (alarm fired, scan done...)."""
        ev = {"kind": kind, "at": self.iso(), **data}
        self.events.append(ev)
        del self.events[:-200]
        log.info("event %s %s", kind, data)
        self.touch()

    def snapshot(self) -> dict[str, Any]:
        self.state.now = self.iso()
        return self.state.model_dump(mode="json")

    # ---- pump ------------------------------------------------------------
    async def _pump(self) -> None:
        while True:
            await self._dirty.wait()
            await asyncio.sleep(self.coalesce_s)
            self._dirty.clear()
            snap = self.state
            for fn in list(self._subs):
                try:
                    r = fn(snap)
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:  # noqa: BLE001
                    log.exception("state subscriber failed")
