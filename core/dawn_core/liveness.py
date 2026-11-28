"""Liveness of the loops inside dawn-core.

systemd's watchdog used to see only the alarm engine. The other loops (the state pump that pushes everything to the
face, the display loop that drives the backlight, the face and timer ticks, the supervisor, the polls) could die or
stall unnoticed: an exception that escaped, a task cancelled by mistake, an await that never returned.

Every long-lived loop registers here and calls `beat()` once per pass; a loop that blocks on a queue registers its
task instead (dead = done). SysInfoService runs `check()` on every heartbeat and `repair()` restarts a dead or stale
loop through the service's `restart_loop()`, spaced 5 s, 30 s and 120 s apart. A loop that gates the watchdog and is
still dead after those restarts holds back `WATCHDOG=1`, exactly as the engine alone did before, so systemd restarts
dawn-core and the saved ring carries on. `alarms.engine_alive` is "every gating loop is alive", so the face's own
backup alarm trusts it too.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

log = logging.getLogger("dawn.liveness")

BACKOFF_S = (5.0, 30.0, 120.0)  # the gap after the 1st, 2nd and 3rd restart before the next one (or giving up)
HEAL_S = 600.0  # alive this long after a restart: its restarts are forgotten
RESTART_BUDGET_S = 10.0

Restart = Callable[[], Awaitable[None]]


@dataclass
class _Loop:
    name: str
    max_age_s: float | None  # None: judged by its task alone
    gates_watchdog: bool
    restart: Restart | None
    since: float  # registered or restarted: the grace before its first beat
    last_beat: float | None = None
    task: asyncio.Task | None = None
    attempts: int = 0
    next_try: float = 0.0
    alive_since: float | None = None


class Liveness:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._loops: dict[str, _Loop] = {}
        self.on_restart: Callable[[str, int, bool, str], None] | None = None  # (name, attempt, ok, why)

    # ---- the loops' side ---------------------------------------------------
    def register(self, name: str, *, max_age_s: float | None, gates_watchdog: bool = False, restart: Restart | None = None) -> None:
        """Watch `name`. Registering again (a restart_loop, a start() tried again) keeps its restart count."""
        now = self._clock()
        old = self._loops.get(name)
        loop = _Loop(name, max_age_s, gates_watchdog, restart, since=now)
        if old is not None:
            loop.attempts, loop.next_try, loop.task = old.attempts, old.next_try, old.task
        self._loops[name] = loop

    def beat(self, name: str) -> None:
        loop = self._loops.get(name)
        if loop is not None:
            loop.last_beat = self._clock()

    def task(self, name: str, task: asyncio.Task | None) -> None:
        loop = self._loops.get(name)
        if loop is not None:
            loop.task = task
            loop.since = self._clock()

    # ---- the watcher's side ------------------------------------------------
    def names(self) -> list[str]:
        return list(self._loops)

    def _why_dead(self, loop: _Loop, now: float) -> str | None:
        if loop.task is not None and loop.task.done():
            if loop.task.cancelled():
                return "task cancelled"
            exc = loop.task.exception()
            return f"task ended: {type(exc).__name__}: {exc}" if exc else "task ended"
        if loop.max_age_s is not None:
            age = now - (loop.last_beat if loop.last_beat is not None and loop.last_beat >= loop.since else loop.since)
            if age > loop.max_age_s:
                return f"no pass for {age:.0f} s"
        return None

    def check(self, now: float | None = None) -> list[str]:
        """The names of the loops that are dead (task done) or stale (no beat within max_age_s)."""
        now = self._clock() if now is None else now
        return [n for n, loop in self._loops.items() if self._why_dead(loop, now) is not None]

    def why(self, name: str, now: float | None = None) -> str | None:
        loop = self._loops.get(name)
        return self._why_dead(loop, self._clock() if now is None else now) if loop else None

    def gating_dead(self, now: float | None = None) -> list[str]:
        now = self._clock() if now is None else now
        return [n for n, loop in self._loops.items() if loop.gates_watchdog and self._why_dead(loop, now) is not None]

    def exhausted(self, now: float | None = None) -> list[str]:
        """Gating loops still dead after their restarts (or with nothing to restart them): hold back the watchdog."""
        now = self._clock() if now is None else now
        out = []
        for n, loop in self._loops.items():
            if not loop.gates_watchdog or self._why_dead(loop, now) is None:
                continue
            if loop.restart is None or (loop.attempts >= len(BACKOFF_S) and now >= loop.next_try):
                out.append(n)
        return out

    async def repair(self, now: float | None = None) -> list[str]:
        """Restart what is dead and due, heal what has been fine for a while. Returns the names restarted."""
        now = self._clock() if now is None else now
        restarted = []
        for name, loop in list(self._loops.items()):
            why = self._why_dead(loop, now)
            if why is None:
                if loop.alive_since is None:
                    loop.alive_since = now
                elif loop.attempts and now - loop.alive_since >= HEAL_S:
                    loop.attempts, loop.next_try = 0, 0.0
                continue
            loop.alive_since = None
            if loop.restart is None or now < loop.next_try:
                continue
            if loop.attempts >= len(BACKOFF_S) and loop.gates_watchdog:
                continue  # given up: the watchdog is held back and systemd restarts dawn-core
            loop.attempts += 1
            loop.next_try = now + BACKOFF_S[min(loop.attempts, len(BACKOFF_S)) - 1]
            ok = True
            try:
                await asyncio.wait_for(loop.restart(), RESTART_BUDGET_S)
            except Exception:  # noqa: BLE001
                ok = False
                log.exception("restarting loop %s failed", name)
            fresh = self._loops.get(name, loop)
            fresh.since, fresh.last_beat = self._clock(), None
            fresh.attempts, fresh.next_try = loop.attempts, loop.next_try
            log.warning("dawn-core loop %s was dead (%s); restarted it (attempt %d)%s", name, why, loop.attempts, "" if ok else ": the restart failed")
            if self.on_restart:
                try:
                    self.on_restart(name, loop.attempts, ok, why)
                except Exception:  # noqa: BLE001
                    log.debug("on_restart callback failed", exc_info=True)
            restarted.append(name)
        return restarted
