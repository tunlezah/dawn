"""Media players: mpv over its JSON IPC socket, and a simulated player.

One mpv process per source kind (dab, chime, url) so PipeWire shows distinct
streams (`--audio-client-name`) and the arbiter can duck/pause them independently.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("dawn.audio.player")


class Player:
    """Interface."""

    client_name: str = "dawn"

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def load(self, url: str, loop: bool = False) -> None: ...
    async def unload(self) -> None: ...
    async def pause(self, paused: bool) -> None: ...
    async def set_gain(self, percent: float) -> None: ...
    @property
    def playing(self) -> bool:
        return False
    @property
    def flowing(self) -> bool:
        """Decoder producing audio right now (best effort)."""
        return False
    @property
    def media_title(self) -> str | None:
        return None
    @property
    def metadata(self) -> dict[str, Any]:
        return {}
    def on_change(self, fn: Callable[[], None]) -> None: ...


class MpvPlayer(Player):
    def __init__(self, client_name: str, runtime_dir: Path, binary: str = "mpv", extra_args: list[str] | None = None):
        self.client_name = client_name
        self.sock = runtime_dir / f"mpv-{client_name}.sock"
        self.binary = binary
        self.extra_args = extra_args or []
        self.proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._rx_task: asyncio.Task[None] | None = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self.props: dict[str, Any] = {"pause": False, "core-idle": True, "paused-for-cache": False, "idle-active": True, "media-title": None, "metadata": {}, "eof-reached": False}
        self._listeners: list[Callable[[], None]] = []
        self._loaded_url: str | None = None
        self._last_change = time.monotonic()

    def on_change(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    async def start(self) -> None:
        if not shutil.which(self.binary):
            raise RuntimeError(f"{self.binary} not found")
        if self.sock.exists():
            self.sock.unlink()
        args = [
            self.binary, "--idle=yes", "--no-video", "--no-terminal", "--really-quiet", "--keep-open=no",
            f"--input-ipc-server={self.sock}", f"--audio-client-name={self.client_name}",
            "--ao=pipewire,pulse,alsa", "--volume=100", "--volume-max=100", "--cache=yes", "--demuxer-readahead-secs=4",
            "--network-timeout=10", "--stream-lavf-o=reconnect=1,reconnect_streamed=1,reconnect_delay_max=5",
            *self.extra_args,
        ]
        self.proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        for _ in range(50):
            if self.sock.exists():
                try:
                    self._reader, self._writer = await asyncio.open_unix_connection(str(self.sock))
                    break
                except OSError:
                    pass
            await asyncio.sleep(0.1)
        if not self._writer:
            raise RuntimeError("mpv IPC socket did not come up")
        self._rx_task = asyncio.create_task(self._rx(), name=f"mpv-rx-{self.client_name}")
        for i, p in enumerate(["pause", "core-idle", "paused-for-cache", "idle-active", "media-title", "metadata", "eof-reached"]):
            await self._cmd("observe_property", i + 1, p)
        log.info("mpv started for %s", self.client_name)

    async def stop(self) -> None:
        if self._rx_task:
            self._rx_task.cancel()
        if self._writer:
            try:
                await self._cmd("quit")
            except Exception:  # noqa: BLE001
                pass
            self._writer.close()
        if self.proc and self.proc.returncode is None:
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except TimeoutError:
                self.proc.kill()
        self.proc = None
        self._writer = None

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def _rx(self) -> None:
        assert self._reader
        while True:
            line = await self._reader.readline()
            if not line:
                log.warning("mpv %s IPC closed", self.client_name)
                return
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "request_id" in msg and msg["request_id"] in self._pending:
                fut = self._pending.pop(msg["request_id"])
                if not fut.done():
                    fut.set_result(msg)
            elif msg.get("event") == "property-change":
                self.props[msg.get("name")] = msg.get("data")
                self._last_change = time.monotonic()
                self._notify()
            elif msg.get("event") in ("end-file", "start-file", "file-loaded"):
                self._notify()

    def _notify(self) -> None:
        for fn in self._listeners:
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("player listener failed")

    async def _cmd(self, *args: Any) -> Any:
        if not self._writer:
            raise RuntimeError("mpv not running")
        self._req_id += 1
        rid = self._req_id
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        self._writer.write((json.dumps({"command": list(args), "request_id": rid}) + "\n").encode())
        await self._writer.drain()
        try:
            return await asyncio.wait_for(fut, 5)
        finally:
            self._pending.pop(rid, None)

    async def load(self, url: str, loop: bool = False) -> None:
        self._loaded_url = url
        await self._cmd("set_property", "loop-file", "inf" if loop else "no")
        await self._cmd("set_property", "pause", False)
        await self._cmd("loadfile", url, "replace")

    async def unload(self) -> None:
        self._loaded_url = None
        try:
            await self._cmd("stop")
        except Exception:  # noqa: BLE001
            pass

    async def pause(self, paused: bool) -> None:
        await self._cmd("set_property", "pause", paused)

    async def set_gain(self, percent: float) -> None:
        await self._cmd("set_property", "volume", max(0.0, min(100.0, percent)))

    @property
    def playing(self) -> bool:
        return self._loaded_url is not None and not self.props.get("pause") and not self.props.get("idle-active", True)

    @property
    def flowing(self) -> bool:
        return self.playing and not self.props.get("core-idle", True) and not self.props.get("paused-for-cache", False)

    @property
    def media_title(self) -> str | None:
        t = self.props.get("media-title")
        return str(t) if t else None

    @property
    def metadata(self) -> dict[str, Any]:
        return self.props.get("metadata") or {}


class SimPlayer(Player):
    """No sound; tracks state and reports 'flowing' from an optional probe."""

    def __init__(self, client_name: str, flowing_probe: Callable[[], bool] | None = None):
        self.client_name = client_name
        self._url: str | None = None
        self._paused = False
        self._probe = flowing_probe
        self._listeners: list[Callable[[], None]] = []
        self.gain = 100.0

    def on_change(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    async def load(self, url: str, loop: bool = False) -> None:
        self._url = url
        self._paused = False
        log.info("sim player %s: play %s%s", self.client_name, url, " (loop)" if loop else "")
        for fn in self._listeners:
            fn()

    async def unload(self) -> None:
        if self._url:
            log.info("sim player %s: stop", self.client_name)
        self._url = None

    async def pause(self, paused: bool) -> None:
        self._paused = paused
        log.info("sim player %s: %s", self.client_name, "pause" if paused else "resume")

    async def set_gain(self, percent: float) -> None:
        self.gain = percent

    @property
    def playing(self) -> bool:
        return self._url is not None and not self._paused

    @property
    def flowing(self) -> bool:
        if not self.playing:
            return False
        return self._probe() if self._probe else True

    @property
    def media_title(self) -> str | None:
        return os.path.basename(self._url) if self._url and "://" not in self._url else None
