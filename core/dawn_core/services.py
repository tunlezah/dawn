"""Service base class and registry.

A Service is anything with an async start()/stop(). The registry starts them in
order and stops in reverse; a failing start() is logged and the rest continue,
because Dawn must reach the face and ring alarms even when half the hardware is
missing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("dawn.services")


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

    async def start_all(self) -> None:
        for s in self._services:
            try:
                await asyncio.wait_for(s.start(), timeout=30)
                log.debug("started %s", s.name)
            except Exception as e:  # noqa: BLE001
                self.failed[s.name] = repr(e)
                log.exception("service %s failed to start (continuing)", s.name)

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
