"""AirPlayService: shairport-sync metadata pipe -> arbiter source + face metadata."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

import httpx

from ..audio.service import AudioService
from ..audio.sources import AudioSource
from ..config import DawnConfig
from ..context import DawnContext
from ..services import Service
from ..state.ui import NowPlaying
from .metadata import AirPlayTrack, MetadataParser

log = logging.getLogger("dawn.airplay")


class AirPlaySource(AudioSource):
    """External source: audio arrives via PipeWire; we only pause/resume the sender and mute the stream."""

    kind = "airplay"

    def __init__(self, svc: AirPlayService):
        super().__init__()
        self.svc = svc
        self.label = "AirPlay"
        self.ref = "airplay"

    async def start(self) -> None:
        await self.svc.stream_mute(False)

    async def stop(self) -> None:
        await self.svc.stream_mute(False)

    async def pause(self) -> None:
        await self.svc.stream_mute(True)
        await self.svc.remote("Pause")

    async def resume(self) -> None:
        await self.svc.remote("Play")
        await self.svc.stream_mute(False)

    async def set_gain(self, percent: float) -> None:
        await self.svc.stream_mute(percent < 50)  # duck = mute for external streams

    @property
    def playing(self) -> bool:
        return self.svc.track.playing

    def now_playing(self) -> NowPlaying:
        t = self.svc.track
        return NowPlaying(
            source="airplay", title=t.title or "AirPlay", artist=t.artist, album=t.album, station=t.client,
            artwork_url=f"/api/airplay/artwork?v={t.artwork_version}" if t.artwork else None,
        )


class AirPlayService(Service):
    name = "airplay"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self.track = AirPlayTrack()
        self.parser = MetadataParser()
        self.source = AirPlaySource(self)
        self._task: asyncio.Task[None] | None = None
        self._available = False
        self._hub = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=2.0))

    async def start(self) -> None:
        if not self.ctx.config.airplay.enabled:
            self.publish()
            return
        self._task = asyncio.create_task(self._sim_loop() if self.ctx.sim else self._pipe_loop(), name="airplay-meta")
        self.publish()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self._hub.aclose()

    async def on_config(self, old: DawnConfig, new: DawnConfig) -> None:
        if old.airplay_name() != new.airplay_name():
            await self._apply_name(new.airplay_name())
        self.publish()

    async def _apply_name(self, name: str) -> None:
        if self.ctx.sim:
            log.info("sim: AirPlay name -> %s", name)
            return
        proc = await asyncio.create_subprocess_exec(self.ctx.config.system.sudo_binary, "-n", "/usr/local/bin/dawn-airplay-name", name)
        await proc.wait()
        if proc.returncode:
            log.warning("could not apply AirPlay name (dawn-airplay-name rc=%s)", proc.returncode)

    # ---- metadata sources ------------------------------------------------
    async def _pipe_loop(self) -> None:
        path = self.ctx.config.airplay.metadata_pipe
        loop = asyncio.get_running_loop()
        while True:
            if not os.path.exists(path):
                self._available = False
                self.publish()
                await asyncio.sleep(10)
                continue
            try:
                fd = await loop.run_in_executor(None, lambda: os.open(path, os.O_RDONLY))
                self._available = True
                self.publish()
                log.info("reading AirPlay metadata from %s", path)
                while True:
                    chunk = await loop.run_in_executor(None, os.read, fd, 65536)
                    if not chunk:
                        break  # writer closed; reopen
                    await self._feed(chunk)
                os.close(fd)
            except asyncio.CancelledError:
                raise
            except OSError as e:
                log.warning("metadata pipe error: %s", e)
                await asyncio.sleep(3)

    async def _sim_loop(self) -> None:
        self._available = True
        self.publish()
        while True:
            try:
                r = await self._hub.get(f"{self.ctx.config.sim.hub_url}/airplay/pipe", params={"timeout": 20})
                if r.status_code == 200 and r.content:
                    await self._feed(r.content)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                await asyncio.sleep(2)

    async def _feed(self, chunk: bytes) -> None:
        audio: AudioService = self.ctx.svc(AudioService)
        for item in self.parser.feed(chunk):
            ev = self.track.apply(item)
            if ev == "begin":
                self.ctx.db.log_event("airplay_begin", client=self.track.client)
                await audio.arbiter.acquire("airplay", self.source)
            elif ev == "end":
                self.ctx.db.log_event("airplay_end")
                await audio.arbiter.release("airplay")
            elif ev == "pause":
                slot = audio.arbiter.slot("airplay")
                if slot and slot.state in ("playing", "ducked", "starting"):
                    await audio.arbiter.pause_level("airplay")
            elif ev == "resume":
                slot = audio.arbiter.slot("airplay")
                if slot is None:
                    await audio.arbiter.acquire("airplay", self.source)
                elif slot.state == "paused":
                    await audio.arbiter.resume_level("airplay")
        audio.publish()
        self.publish()

    # ---- control ---------------------------------------------------------
    async def remote(self, command: str) -> None:
        """Send Pause/Play to the AirPlay sender via shairport-sync's D-Bus RemoteControl."""
        if self.ctx.sim:
            try:
                await self._hub.post(f"{self.ctx.config.sim.hub_url}/airplay/remote", json={"command": command}, timeout=2)
            except Exception:  # noqa: BLE001
                pass
            return
        if not self.ctx.config.airplay.dbus:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "busctl", "--system", "call", "org.gnome.ShairportSync", "/org/gnome/ShairportSync", "org.gnome.ShairportSync.RemoteControl", command,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), 5)
        except (TimeoutError, OSError) as e:
            log.debug("airplay remote %s failed: %s", command, e)

    async def stream_mute(self, muted: bool) -> None:
        backend = self.ctx.svc(AudioService).backend
        fn = getattr(backend, "set_stream_mute", None)
        if fn:
            await fn("shairport", muted)

    def artwork(self) -> bytes | None:
        return self.track.artwork

    def publish(self) -> None:
        st = self.ctx.store.state.airplay
        st.available = self._available
        st.name = self.ctx.config.airplay_name()
        st.active = self.track.session
        st.client = self.track.client
        st.playing = self.track.playing
        self.ctx.store.touch()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
