"""Background tasks that are kept and heard from.

`asyncio.create_task` keeps only a weak reference to the task, so one that nothing else holds can be garbage-collected
part way through, and an exception it raises is never logged (at best a "Task exception was never retrieved" at
exit). `spawn` keeps a strong reference until the task is done, logs any exception with the task's name, and counts
it for Diagnostics (`system.task_failures`). Use it for every fire-and-forget task; ruff's RUF006 flags new ones.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Coroutine
from typing import Any

log = logging.getLogger("dawn.tasks")

_RUNNING: set[asyncio.Task[Any]] = set()
_FAILURES: deque[dict[str, Any]] = deque(maxlen=20)
_COUNT = 0


def spawn(coro: Coroutine[Any, Any, Any], *, name: str, owner: str | None = None) -> asyncio.Task[Any]:
    """Start `coro` as a task that is held until it finishes and whose failure is logged and counted."""
    task = asyncio.create_task(coro, name=name)
    _RUNNING.add(task)
    task.add_done_callback(lambda t: _done(t, name, owner))
    return task


def _done(task: asyncio.Task[Any], name: str, owner: str | None) -> None:
    global _COUNT
    _RUNNING.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    _COUNT += 1
    log.error("background task %s%s failed: %r", name, f" ({owner})" if owner else "", exc, exc_info=exc)
    _FAILURES.append({"name": name, "owner": owner, "at": time.time(), "error": f"{type(exc).__name__}: {exc}"[:300]})


def failures() -> list[dict[str, Any]]:
    """The last 20 failures, oldest first: name, owner, wall time (epoch seconds) and the error."""
    return list(_FAILURES)


def failure_count() -> int:
    return _COUNT


def running() -> int:
    return len(_RUNNING)
