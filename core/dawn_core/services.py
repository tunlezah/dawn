"""Service base class and registry.

A Service is anything with an async start()/stop(). The registry starts them in
order and stops in reverse; a failing start() is logged and the rest continue,
because Dawn must reach the face and ring alarms even when half the hardware is
missing. A service whose start() failed is started again later (the supervisor
asks every check): a daemon or a device that was not ready at boot (PipeWire,
bluetoothd, a GPIO chip) comes right without a restart of dawn-core. start()
must therefore be safe to call again after it raised or was cut short.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

log = logging.getLogger("dawn.services")

START_TIMEOUT_S = 30.0
RETRY_MIN_S = 60.0  # a failed service is tried again after this long, then twice as long each time...
RETRY_MAX_S = 900.0  # ...up to this


class Service:
    name: str = "service"

    async def start(self) -> None:  # pragma: no cover - interface
        pass

    async def stop(self) -> None:  # pragma: no cover - interface
        pass

    async def on_config(self, old: Any, new: Any) -> None:
        """Called after a config hot reload."""


class ServiceRegistry:
    def __init__(self) -> None:
        self._services: list[Service] = []
        self.failed: dict[str, str] = {}
        self.retries: dict[str, int] = {}  # name -> failed starts so far (the first one included)
        self.recovered: dict[str, float] = {}  # name -> time.time() it started after failing
        self._next_try: dict[str, float] = {}  # name -> monotonic time of the next attempt

    def add(self, svc: Service) -> Service:
        self._services.append(svc)
        return svc

    def get(self, cls: type) -> Any:
        for s in self._services:
            if isinstance(s, cls):
                return s
        raise KeyError(cls.__name__)

    def all(self) -> list[Service]:
        return list(self._services)

    async def _start(self, s: Service) -> bool:
        try:
            await asyncio.wait_for(s.start(), timeout=START_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001
            n = self.retries[s.name] = self.retries.get(s.name, 0) + 1
            self.failed[s.name] = repr(e)
            self._next_try[s.name] = time.monotonic() + min(RETRY_MAX_S, RETRY_MIN_S * 2 ** (n - 1))
            log.exception("service %s failed to start (attempt %d; continuing, tried again later)", s.name, n)
            return False
        if s.name in self.failed:
            del self.failed[s.name]
            self.recovered[s.name] = time.time()
            log.warning("service %s started on attempt %d", s.name, self.retries.get(s.name, 0) + 1)
        else:
            log.debug("started %s", s.name)
        return True

    async def start_all(self) -> None:
        for s in self._services:
            await self._start(s)

    def retry_due(self) -> list[Service]:
        """The failed services whose next attempt is due."""
        now = time.monotonic()
        return [s for s in self._services if s.name in self.failed and now >= self._next_try.get(s.name, 0.0)]

    async def retry_failed(self) -> list[str]:
        """Start again the services whose start() failed, when their turn comes (1, 2, 4... minutes apart, 15 at
        most). Returns the names that came good."""
        out = []
        for s in self.retry_due():
            log.info("starting %s again (failed earlier: %s)", s.name, self.failed.get(s.name))
            if await self._start(s):
                out.append(s.name)
        return out

    async def stop_all(self) -> None:
        for s in reversed(self._services):
            try:
                await asyncio.wait_for(s.stop(), timeout=10)
            except Exception:  # noqa: BLE001
                log.exception("service %s failed to stop", s.name)

    async def notify_config(self, old: Any, new: Any) -> None:
        for s in self._services:
            try:
                await s.on_config(old, new)
            except Exception:  # noqa: BLE001
                log.exception("service %s failed on config reload", s.name)
