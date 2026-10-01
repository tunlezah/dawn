"""Load, validate, watch and write config.yaml."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .schema import DawnConfig

log = logging.getLogger("dawn.config")

DEFAULT_PATH = "/etc/dawn/config.yaml"


def config_path() -> Path:
    """Resolve the config file: $DAWN_CONFIG, then /etc/dawn/config.yaml."""
    env = os.environ.get("DAWN_CONFIG")
    if env:
        return Path(env).expanduser()
    return Path(DEFAULT_PATH)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data


def load_config(path: Path | None = None) -> DawnConfig:
    """Load and validate. A missing file yields the defaults."""
    p = path or config_path()
    data = _read_yaml(p)
    return DawnConfig.model_validate(data)


def dump_config(cfg: DawnConfig) -> str:
    return yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False, allow_unicode=True)


def write_config(cfg: DawnConfig, path: Path | None = None) -> Path:
    p = path or config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(dump_config(cfg), encoding="utf-8")
    os.replace(tmp, p)
    return p


Listener = Callable[[DawnConfig, DawnConfig], Awaitable[None] | None]


class ConfigManager:
    """Holds the live config and hot-reloads it when the file changes.

    Reload is by polling mtime (every `poll_s`), which needs no extra dependency
    and works on every filesystem. Listeners get (old, new) after a successful
    reload; a file that fails validation is logged and ignored, the previous
    config stays active.
    """

    def __init__(self, path: Path | None = None, poll_s: float = 2.0):
        self.path = path or config_path()
        self.poll_s = poll_s
        self.config: DawnConfig = load_config(self.path)
        self.last_error: str | None = None
        self._mtime = self._stat()
        self._listeners: list[Listener] = []
        self._task: asyncio.Task[None] | None = None

    def _stat(self) -> float:
        try:
            return self.path.stat().st_mtime
        except FileNotFoundError:
            return 0.0

    def on_change(self, fn: Listener) -> None:
        self._listeners.append(fn)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._watch(), name="config-watch")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(self.poll_s)
            m = self._stat()
            if m != self._mtime:
                self._mtime = m
                await self.reload()

    async def reload(self) -> bool:
        try:
            new = load_config(self.path)
        except (ValidationError, ValueError, yaml.YAMLError) as e:
            self.last_error = str(e)
            log.error("config reload failed, keeping previous config: %s", e)
            return False
        self.last_error = None
        old, self.config = self.config, new
        log.info("config reloaded from %s", self.path)
        await self._notify(old, new)
        return True

    async def _notify(self, old: DawnConfig, new: DawnConfig) -> None:
        for fn in list(self._listeners):
            try:
                r = fn(old, new)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:  # noqa: BLE001
                log.exception("config listener failed")

    async def update(self, patch: dict[str, Any]) -> DawnConfig:
        """Deep-merge `patch` into the current config, validate, write, notify."""
        merged = _deep_merge(self.config.model_dump(mode="json"), patch)
        new = DawnConfig.model_validate(merged)
        write_config(new, self.path)
        self._mtime = self._stat()
        old, self.config = self.config, new
        await self._notify(old, new)
        return new

    async def replace(self, data: dict[str, Any]) -> DawnConfig:
        new = DawnConfig.model_validate(data)
        write_config(new, self.path)
        self._mtime = self._stat()
        old, self.config = self.config, new
        await self._notify(old, new)
        return new


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
