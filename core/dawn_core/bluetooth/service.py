"""BluetoothService: pairing/connection management, A2DP playback into the arbiter, AVRCP metadata."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from ..audio.service import AudioService
from ..audio.sources import AudioSource
from ..config import DawnConfig
from ..context import DawnContext
from ..services import Service
from ..state.ui import BtDevice, NowPlaying
from .backend import BluetoothBackend, make_backend

log = logging.getLogger("dawn.bt")


class BluetoothSource(AudioSource):
    kind = "bluetooth"

    def __init__(self, svc: BluetoothService):
        super().__init__()
        self.svc = svc
        self.label = "Bluetooth"
        self.ref = "bluetooth"

    async def start(self) -> None:
        await self.svc.stream_mute(False)

    async def stop(self) -> None:
        await self.svc.stream_mute(False)

    async def pause(self) -> None:
        await self.svc.stream_mute(True)
        await self.svc.backend.player("Pause")

    async def resume(self) -> None:
        await self.svc.backend.player("Play")
        await self.svc.stream_mute(False)

    async def set_gain(self, percent: float) -> None:
        await self.svc.stream_mute(percent < 50)

    @property
    def playing(self) -> bool:
        return self.svc.backend.snap.transport_active or self.svc.backend.snap.player_status == "playing"

    def now_playing(self) -> NowPlaying:
        s = self.svc.backend.snap
        dev = next((d for d in s.devices if d.address == s.connected), None)
        t = s.track or {}
        return NowPlaying(source="bluetooth", title=t.get("Title") or (dev.name if dev else "Bluetooth"), artist=t.get("Artist"), album=t.get("Album"), station=dev.name if dev else None)


class BluetoothService(Service):
    name = "bluetooth"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self.backend: BluetoothBackend = BluetoothBackend(lambda: None)
        self.source = BluetoothSource(self)
        self._task: asyncio.Task[None] | None = None
        self._disc_task: asyncio.Task[None] | None = None
        self._was_active = False

    async def start(self) -> None:
        if not self.ctx.config.bluetooth.enabled:
            self.publish()
            return
        self.backend = await make_backend(self._on_change, self.ctx.sim, self.ctx.config.sim.hub_url, self.ctx.config.bluetooth.adapter)
        await self.backend.start() if self.backend.name == "sim" else None
        try:
            if self.backend.snap.available:
                await self.backend.set_alias(self.ctx.config.bluetooth_name())
        except Exception as e:  # noqa: BLE001
            log.debug("set alias failed: %s", e)
        self._task = asyncio.create_task(self._loop(), name="bt-loop")
        self.publish()

    async def stop(self) -> None:
        for t in (self._task, self._disc_task):
            if t:
                t.cancel()
        await self.backend.stop()

    async def on_config(self, old: DawnConfig, new: DawnConfig) -> None:
        if old.bluetooth_name() != new.bluetooth_name() and self.backend.snap.available:
            try:
                await self.backend.set_alias(new.bluetooth_name())
            except Exception as e:  # noqa: BLE001
                log.debug("set alias failed: %s", e)
        self.publish()

    async def _loop(self) -> None:
        n = 0
        while True:
            await asyncio.sleep(5)
            n += 1
            try:
                if n % 6 == 0 and self.ctx.config.bluetooth.auto_reconnect and self.backend.snap.available and not self.backend.snap.connected:
                    for d in self.backend.snap.devices:
                        if d.paired and d.trusted and not d.connected:
                            log.debug("auto-reconnect attempt: %s", d.name)
                            try:
                                await self.backend.connect(d.address)
                                break
                            except Exception:  # noqa: BLE001
                                pass
                if hasattr(self.backend, "refresh") and n % 12 == 0:
                    await self.backend.refresh()  # type: ignore[attr-defined]
                    await self._on_change()
            except Exception:  # noqa: BLE001
                log.exception("bt loop")

    async def _on_change(self) -> None:
        audio: AudioService = self.ctx.svc(AudioService)
        s = self.backend.snap
        active = s.transport_active or s.player_status == "playing"
        slot = audio.arbiter.slot("bluetooth")
        if active and not self._was_active:
            self.ctx.db.log_event("bluetooth_play", device=s.connected)
            if slot is None:
                await audio.arbiter.acquire("bluetooth", self.source)
            elif slot.state == "paused":
                await audio.arbiter.resume_level("bluetooth")
        elif not active and self._was_active and slot is not None:
            if s.connected and s.player_status == "paused":
                await audio.arbiter.pause_level("bluetooth")
            else:
                await audio.arbiter.release("bluetooth")
        elif not s.connected and slot is not None:
            await audio.arbiter.release("bluetooth")
        self._was_active = active
        audio.publish()
        self.publish()

    async def stream_mute(self, muted: bool) -> None:
        fn = getattr(self.ctx.svc(AudioService).backend, "set_stream_mute", None)
        if fn:
            await fn("bluez", muted)

    # ---- API -------------------------------------------------------------
    async def discoverable(self, seconds: int | None = None) -> None:
        secs = seconds or self.ctx.config.bluetooth.discoverable_timeout_s
        await self.backend.set_discoverable(True, secs)
        until = self.ctx.store.now() + timedelta(seconds=secs)
        self.ctx.store.state.bluetooth.discoverable_until = until.isoformat(timespec="seconds")
        if self._disc_task:
            self._disc_task.cancel()
        self._disc_task = asyncio.create_task(self._end_discoverable(secs))
        self.publish()

    async def _end_discoverable(self, secs: int) -> None:
        await asyncio.sleep(secs)
        try:
            await self.backend.set_discoverable(False, 0)
        except Exception:  # noqa: BLE001
            pass
        self.ctx.store.state.bluetooth.discoverable_until = None
        self.publish()

    def publish(self) -> None:
        s = self.backend.snap
        st = self.ctx.store.state.bluetooth
        st.available = s.available
        st.powered = s.powered
        st.discoverable = s.discoverable
        st.scanning = s.scanning
        st.name = s.alias or self.ctx.config.bluetooth_name()
        st.devices = [BtDevice(address=d.address, name=d.name or d.address, paired=d.paired, connected=d.connected, trusted=d.trusted, icon=d.icon, rssi=d.rssi) for d in s.devices]
        st.connected = s.connected
        st.playing = s.transport_active or s.player_status == "playing"
        self.ctx.store.touch()
