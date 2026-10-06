"""AudioService: owns the backend, the players, the arbiter and the master volume."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from contextlib import suppress
from datetime import timedelta

from sqlmodel import select

from ..config import DawnConfig
from ..context import DawnContext
from ..db.models import PresetRow
from ..services import Service
from ..state.ui import EqState, NowPlaying, Preset, SinkInfo
from .arbiter import LEVELS, Arbiter, Slot
from .backend import AudioBackend, PipeWireBackend, Sink, make_backend
from .player import MpvPlayer, Player, SimPlayer
from .sources import CHIME_NAMES, AudioSource, ChimeSource, PlaylistSource, UrlSource, list_playlists

log = logging.getLogger("dawn.audio")

SourceFactory = Callable[[str, str], Awaitable[AudioSource]]  # (argument, level) -> source


class AudioService(Service):
    name = "audio"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self.backend: AudioBackend = AudioBackend()
        self.arbiter = Arbiter(ctx.config.audio.duck_percent, ctx.config.audio.duck_seconds, on_change=self.publish)
        self.players: dict[str, Player] = {}
        self.factories: dict[str, SourceFactory] = {}
        self.volume = 35
        self.muted = False
        self._volume_before_mute = 35
        self._sinks: list[Sink] = []
        self._sink: Sink | None = None
        self._task: asyncio.Task[None] | None = None
        self._overlay_task: asyncio.Task[None] | None = None
        self._last_published_level: str | None = None
        self._pinned_missing_logged: str | None = None
        self.register_factory("chime", self._make_chime)
        self.register_factory("url", self._make_url)
        self.register_factory("playlist", self._make_playlist)

    # ---- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        cfg = self.ctx.config
        self.backend = await make_backend(cfg.audio.backend, cfg.sim.hub_url, self.ctx.sim)
        self.ctx.store.state.audio.backend = self.backend.name
        self.volume = min(cfg.audio.max_volume, int(self.ctx.db.get("audio.volume", cfg.audio.default_volume)))
        self.muted = bool(self.ctx.db.get("audio.muted", False))
        await self.refresh_sinks(select_now=True)
        await self.backend.set_volume(self.hw_volume(self.volume))
        await self.backend.set_mute(self.muted)
        await self.apply_eq()
        self._task = asyncio.create_task(self._loop(), name="audio-loop")
        self.publish()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self.arbiter.release_all()
        for p in self.players.values():
            try:
                await p.stop()
            except Exception:  # noqa: BLE001
                pass
        await self.backend.stop()

    async def on_config(self, old: DawnConfig, new: DawnConfig) -> None:
        if old.audio.eq != new.audio.eq:
            await self.apply_eq()
        if old.audio.pinned_sink != new.audio.pinned_sink or old.audio.sink_priority != new.audio.sink_priority:
            await self.refresh_sinks(select_now=True)
        if new.audio.max_volume < self.volume:
            await self.set_volume(new.audio.max_volume, overlay=False)
        elif old.audio.output_ceiling_percent != new.audio.output_ceiling_percent:
            await self.backend.set_volume(self.hw_volume(self.volume))
        self.arbiter.duck_percent = new.audio.duck_percent
        self.arbiter.duck_seconds = new.audio.duck_seconds
        self.publish()

    async def _loop(self) -> None:
        n = 0
        while True:
            await asyncio.sleep(2)
            n += 1
            try:
                if n % 15 == 0:
                    await self._retry_pipewire()
                if n % 5 == 0:
                    await self.refresh_sinks()
                self.ctx.store.state.audio.audio_flowing = await self.audio_flowing()
                self.publish()
            except Exception:  # noqa: BLE001
                log.exception("audio loop")

    async def _retry_pipewire(self) -> None:
        """PipeWire not answering when core started means the ALSA fallback (or no backend) for the whole run, and
        with it no control of the volume an alarm rings at. Once PipeWire answers, switch to it."""
        if self.ctx.sim or self.ctx.config.audio.backend not in ("auto", "pipewire") or self.backend.name == "pipewire":
            return
        pw = PipeWireBackend()
        try:
            await pw.start()
        except Exception:  # noqa: BLE001
            return
        log.warning("PipeWire answers now; switching the audio backend from %s", self.backend.name)
        old, self.backend = self.backend, pw
        self.ctx.store.state.audio.backend = pw.name
        self._sink = None
        await self.refresh_sinks(select_now=True)  # default sink, volume and mute onto the new backend
        await self.apply_eq()
        try:
            await old.stop()
        except Exception:  # noqa: BLE001
            pass

    # ---- players / factories --------------------------------------------
    def register_factory(self, scheme: str, fn: SourceFactory) -> None:
        self.factories[scheme] = fn

    @staticmethod
    def player_name(family: str, level: str = "user") -> str:
        """The mpv instance for a source family (chime, media, dab) at a level. The alarm level has instances of
        its own: an alarm stream loaded into the player of the source it preempts is paused by that source's duck."""
        return f"dawn-alarm-{family}" if level == "alarm" else f"dawn-{family}"

    async def prepare_players(self, families: Iterable[str]) -> list[str]:
        """Start the alarm level's players ahead of an alarm (an mpv start can take seconds); returns what failed."""
        problems = []
        for family in families:
            try:
                await self.player(self.player_name(family, "alarm"))
            except Exception as e:  # noqa: BLE001
                problems.append(f"{family} player: {e}")
        return problems

    def has_output(self) -> bool:
        return self.backend.name != "none" and bool(self._sinks)

    async def player(self, client_name: str) -> Player:
        p = self.players.get(client_name)
        if p is not None:
            if isinstance(p, MpvPlayer) and not p.alive:
                log.warning("mpv %s died; restarting", client_name)
                try:
                    await p.stop()
                except Exception:  # noqa: BLE001
                    pass
                p = None
            else:
                return p
        cfg = self.ctx.config
        if self.ctx.sim or cfg.audio.backend == "sim":
            p = SimPlayer(client_name, flowing_probe=lambda: self._sim_flowing)
        else:
            mp = MpvPlayer(client_name, self.ctx.runtime_dir, cfg.audio.mpv_binary, cfg.audio.mpv_extra_args)
            try:
                await mp.start()
            except BaseException:
                if mp.proc and mp.proc.returncode is None:
                    with suppress(ProcessLookupError):
                        mp.proc.kill()  # a start that failed or was given up on must not leave an mpv behind
                raise
            p = mp
        self.players[client_name] = p
        return p

    _sim_flowing = True

    async def _make_chime(self, arg: str, level: str = "user") -> AudioSource:
        return ChimeSource(await self.player(self.player_name("chime", level)), arg or self.ctx.config.audio.default_chime, self.ctx.config.audio.chime_dir)

    async def _make_url(self, arg: str, level: str = "user") -> AudioSource:
        return UrlSource(await self.player(self.player_name("media", level)), arg)

    async def _make_playlist(self, arg: str, level: str = "user") -> AudioSource:
        return PlaylistSource(await self.player(self.player_name("media", level)), arg, self.ctx.config.audio.media_dir, self.ctx.runtime_dir)

    def resolve(self, ref: str) -> str:
        """`last-played` as the reference it stands for (anything else unchanged)."""
        if ref == "last-played":
            return self.ctx.db.get("audio.last_source") or f"chime:{self.ctx.config.audio.default_chime}"
        return ref

    async def make_source(self, ref: str, level: str = "user") -> AudioSource:
        """Build a source from a reference like chime:birds, dab:1002, url:http://..., playlist:Morning, last-played."""
        ref = self.resolve(ref)
        scheme, _, arg = ref.partition(":")
        fn = self.factories.get(scheme)
        if fn is None:
            raise ValueError(f"unknown source {ref!r}")
        return await fn(arg, level)

    # ---- playback API ----------------------------------------------------
    async def play(self, ref: str, level: str = "user", remember: bool = True) -> Slot:
        src = await self.make_source(ref, level)
        if remember and level == "user":
            self.ctx.db.try_set("audio.last_source", src.ref or ref)
        if self.muted and level in ("user", "alarm", "sleep"):
            await self.set_mute(False, overlay=False)
        slot = await self.arbiter.acquire(level, src)
        self.publish()
        return slot

    async def stop_level(self, level: str) -> None:
        await self.arbiter.release(level)
        self.publish()

    async def standby(self) -> None:
        """Stop user playback; pause external sources. The face goes to standby."""
        for level in ("user", "sleep"):
            await self.arbiter.release(level)
        for level in ("airplay", "bluetooth"):
            slot = self.arbiter.slot(level)
            if slot and slot.state in ("playing", "ducked", "starting"):
                await self.arbiter.pause_level(level)
        self.publish()

    async def audio_flowing(self) -> bool:
        slot = self.arbiter.active
        return slot is not None and await self._slot_flowing(slot)

    async def level_flowing(self, level: str) -> bool:
        """Audio from the source at `level` is reaching the output (a ring watches its own level, not whatever
        happens to be on top)."""
        slot = self.arbiter.slot(level)
        return slot is not None and await self._slot_flowing(slot)

    async def _slot_flowing(self, slot: Slot) -> bool:
        if slot.state not in ("playing", "ducked") or not slot.source.flowing:
            return False
        player = getattr(slot.source, "player", None)
        running = await self.backend.stream_running(player.client_name) if player is not None else None
        return running is not False

    # ---- volume ----------------------------------------------------------
    def hw_volume(self, volume: int) -> int:
        """Map the 0-100 user scale onto the sink: 100 lands on audio.output_ceiling_percent.

        The I2S amp has no hardware volume, so this is the only gain stage; every
        path (controls, alarm ramps, chimes, sleep fades) ends up here."""
        return round(max(0, min(100, volume)) * self.ctx.config.audio.output_ceiling_percent / 100)

    async def set_volume(self, volume: int, *, persist: bool = True, overlay: bool = True) -> int:
        volume = max(0, min(self.ctx.config.audio.max_volume, int(volume)))
        self.volume = volume
        if self.muted and volume > 0:
            self.muted = False
            await self.backend.set_mute(False)
        await self.backend.set_volume(self.hw_volume(volume))
        if persist:
            self.ctx.db.try_set("audio.volume", volume)
            self.ctx.db.try_set("audio.muted", self.muted)
        if overlay:
            self._show_overlay()
        self.publish()
        return volume

    async def step_volume(self, direction: int) -> int:
        step = self.ctx.config.inputs.encoder.volume_step
        return await self.set_volume(self.volume + direction * step)

    async def set_mute(self, muted: bool | None = None, *, overlay: bool = True) -> bool:
        self.muted = (not self.muted) if muted is None else muted
        await self.backend.set_mute(self.muted)
        self.ctx.db.try_set("audio.muted", self.muted)
        if overlay:
            self._show_overlay()
        self.publish()
        return self.muted

    async def ensure_unmuted(self) -> None:
        """Unmute for an alarm whatever the cached flag says: the sink may have been muted behind core's back
        (wpctl by hand, or a PipeWire restart restoring an old state)."""
        if self.muted:
            await self.set_mute(False, overlay=False)
        else:
            await self.backend.set_mute(False)

    def _show_overlay(self) -> None:
        until = self.ctx.store.now() + timedelta(seconds=self.ctx.config.audio.volume_overlay_s)
        self.ctx.store.state.audio.volume_overlay_until = until.isoformat(timespec="milliseconds")

    # ---- sinks -----------------------------------------------------------
    async def refresh_sinks(self, select_now: bool = False) -> None:
        try:
            sinks = await self.backend.list_sinks()
        except Exception:  # noqa: BLE001
            log.exception("list sinks failed")
            return
        changed = [s.name for s in sinks] != [s.name for s in self._sinks]
        self._sinks = sinks
        want = self._choose_sink(sinks)
        if want and (select_now or changed or self._sink is None or want.name != self._sink.name):
            if self._sink is None or want.name != self._sink.name or select_now:
                try:
                    await self.backend.set_default_sink(want)
                    await self.backend.set_volume(self.hw_volume(self.volume))
                    await self.backend.set_mute(self.muted)
                    log.info("audio sink -> %s (%s)", want.description, want.kind)
                except Exception:  # noqa: BLE001
                    log.exception("set default sink failed")
            self._sink = want
        if changed or select_now:
            self.publish()

    def _choose_sink(self, sinks: list[Sink]) -> Sink | None:
        if not sinks:
            return None
        pinned = self.ctx.config.audio.pinned_sink
        if pinned:
            # a node.name, id or description pins one device; a kind (hifiberry, usb...) pins
            # whichever sink of that kind is present, whatever PipeWire named it on this board
            for s in sinks:
                if s.name == pinned or s.id == pinned or s.description == pinned:
                    return s
            for s in sinks:
                if s.kind == pinned:
                    return s
            if self._pinned_missing_logged != pinned:
                log.warning("pinned sink %r not present; falling back to priority", pinned)
                self._pinned_missing_logged = pinned
        for kind in self.ctx.config.audio.sink_priority:
            for s in sinks:
                if s.kind == kind:
                    return s
        return sinks[0]

    async def pin_sink(self, name: str | None) -> None:
        await self.ctx.cfg_mgr.update({"audio": {"pinned_sink": name}})

    # ---- EQ --------------------------------------------------------------
    async def apply_eq(self) -> None:
        eq = self.ctx.config.audio.eq
        bass = min(eq.bass_db, eq.bass_max_db)
        try:
            # the high-pass protects the driver, so it stays in even with the tone controls off
            await self.backend.set_eq(bass if eq.enabled else 0.0, eq.treble_db if eq.enabled else 0.0, eq.highpass_hz)
        except Exception:  # noqa: BLE001
            log.exception("eq apply failed")
        self.ctx.store.state.audio.eq = EqState(
            enabled=eq.enabled, bass_db=bass, treble_db=eq.treble_db, bass_max_db=eq.bass_max_db, highpass_hz=eq.highpass_hz,
        )
        self.ctx.store.touch()

    # ---- presets ---------------------------------------------------------
    def presets(self) -> list[Preset]:
        with self.ctx.db.session() as s:
            rows = s.exec(select(PresetRow).order_by(PresetRow.position)).all()  # type: ignore[attr-defined]
        out = []
        for r in rows:
            logo = None
            if r.source.startswith("dab:"):
                logo = f"/api/dab/logo/{r.source[4:]}.svg"
            out.append(Preset(id=r.id or 0, label=r.label, source=r.source, logo_url=logo, position=r.position))
        return out

    async def play_next_preset(self, step: int = 1) -> Preset | None:
        """Cycle presets (step=+1 next, -1 previous). From standby, replays the last one (or the first)."""
        presets = self.presets()
        if not presets:
            return None
        current = self.ctx.db.get("audio.last_source")
        idx = next((i for i, p in enumerate(presets) if p.source == current), -1)
        active = self.arbiter.slot("user")
        if active is None or active.state not in ("playing", "ducked", "starting"):
            nxt = presets[idx if idx >= 0 else 0]
        else:
            nxt = presets[(idx + step) % len(presets)]
        await self.play(nxt.source)
        return nxt

    def chimes(self) -> list[dict[str, str]]:
        return [{"name": n, "label": {"gentle_bell": "Gentle bell", "rising_synth": "Rising synth", "birds": "Birds"}[n]} for n in CHIME_NAMES]

    def playlists(self) -> list[dict]:
        return list_playlists(self.ctx.config.audio.media_dir)

    # ---- state -----------------------------------------------------------
    def publish(self) -> None:
        st = self.ctx.store.state
        a = st.audio
        a.volume = self.volume
        a.muted = self.muted
        a.pinned_sink = self.ctx.config.audio.pinned_sink
        a.sinks = [SinkInfo(id=s.id, name=s.name, description=s.description, kind=s.kind, active=(self._sink is not None and s.name == self._sink.name)) for s in self._sinks]  # type: ignore[arg-type]
        a.sink = next((s for s in a.sinks if s.active), None)
        a.sources = [slot.source.status(slot.priority, slot.state) for slot in sorted(self.arbiter.slots.values(), key=lambda s: -s.priority)]
        active = self.arbiter.active
        a.active_source = active.source.kind if active else "none"
        st.now_playing = active.source.now_playing() if active else NowPlaying()
        if active and not st.now_playing.started_at:
            st.now_playing.started_at = self.ctx.store.iso()
        st.presets = self.presets()
        self.ctx.store.touch()

    @staticmethod
    def levels() -> dict[str, int]:
        return dict(LEVELS)
