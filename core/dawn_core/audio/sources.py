"""Audio sources. Each one knows how to start/stop/pause itself and reports
status + now-playing metadata. Player-backed sources (chime, url, playlist,
DAB) share `PlayerSource`; AirPlay and Bluetooth are external (phase 8)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..state.ui import NowPlaying, SourceKind, SourceStatus
from .player import Player

log = logging.getLogger("dawn.audio.sources")

CHIME_NAMES = ("gentle_bell", "rising_synth", "birds")
BUNDLED_CHIMES = Path(__file__).resolve().parents[1] / "assets" / "chimes"


class AudioSource:
    kind: SourceKind = "none"
    label: str = ""
    ref: str = ""  # canonical reference, e.g. "dab:1002", "chime:birds"

    def __init__(self) -> None:
        self._on_update: Callable[[], None] | None = None
        self.error: str | None = None

    def set_update_callback(self, fn: Callable[[], None]) -> None:
        self._on_update = fn

    def _changed(self) -> None:
        if self._on_update:
            self._on_update()

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def pause(self) -> None: ...
    async def resume(self) -> None: ...
    async def set_gain(self, percent: float) -> None: ...

    @property
    def playing(self) -> bool:
        return False

    @property
    def flowing(self) -> bool:
        return self.playing

    def now_playing(self) -> NowPlaying:
        return NowPlaying(source=self.kind, title=self.label)

    def status(self, priority: int, state: str) -> SourceStatus:
        return SourceStatus(kind=self.kind, priority=priority, state=state, label=self.label, detail=self.error)  # type: ignore[arg-type]


class PlayerSource(AudioSource):
    """A source that plays a URL/file through a Player."""

    loop = False

    def __init__(self, player: Player, url: str):
        super().__init__()
        self.player = player
        self.url = url
        self._started = False
        player.on_change(self._changed)

    async def start(self) -> None:
        self._started = True
        self.error = None
        await self.player.load(self.url, loop=self.loop)

    async def stop(self) -> None:
        self._started = False
        await self.player.unload()

    async def pause(self) -> None:
        await self.player.pause(True)

    async def resume(self) -> None:
        if self._started and (self.player.loaded_url != self.url or self.player.idle):
            # the player was used for something else meanwhile, or the stream ended while paused: load it again
            await self.player.load(self.url, loop=self.loop)
        else:
            await self.player.pause(False)

    async def set_gain(self, percent: float) -> None:
        await self.player.set_gain(percent)

    @property
    def playing(self) -> bool:
        return self._started and self.player.playing

    @property
    def flowing(self) -> bool:
        return self._started and self.player.flowing


class ChimeSource(PlayerSource):
    kind = "chime"
    loop = True

    def __init__(self, player: Player, name: str, chime_dir: str | None = None):
        if name not in CHIME_NAMES:
            name = CHIME_NAMES[0]
        path = resolve_chime(name, chime_dir)
        super().__init__(player, str(path))
        self.name = name
        self.label = {"gentle_bell": "Gentle bell", "rising_synth": "Rising synth", "birds": "Birds"}[name]
        self.ref = f"chime:{name}"

    def now_playing(self) -> NowPlaying:
        return NowPlaying(source="chime", title=self.label, station="Chime")


def resolve_chime(name: str, chime_dir: str | None) -> Path:
    for d in ([Path(chime_dir)] if chime_dir else []) + [BUNDLED_CHIMES]:
        for ext in ("ogg", "wav", "mp3", "flac"):
            p = d / f"{name}.{ext}"
            if p.exists():
                return p
    return BUNDLED_CHIMES / f"{name}.ogg"


class UrlSource(PlayerSource):
    kind = "url"

    def __init__(self, player: Player, url: str, label: str | None = None):
        super().__init__(player, url)
        self.label = label or _label_from_url(url)
        self.ref = f"url:{url}"

    def now_playing(self) -> NowPlaying:
        meta = self.player.metadata or {}
        icy = meta.get("icy-title") or meta.get("title")
        title = icy or self.player.media_title
        return NowPlaying(source="url", title=title or self.label, station=self.label if title else None, url=self.url, artist=meta.get("artist"))


class PlaylistSource(PlayerSource):
    kind = "playlist"

    def __init__(self, player: Player, name: str, media_dir: str, runtime_dir: Path):
        self.name = name
        files = playlist_files(name, media_dir)
        if not files:
            raise FileNotFoundError(f"playlist {name!r} has no playable files under {media_dir}")
        m3u = runtime_dir / f"playlist-{_safe(name)}.m3u"
        m3u.write_text("\n".join(str(f) for f in files) + "\n")
        super().__init__(player, str(m3u))
        self.label = name
        self.ref = f"playlist:{name}"
        self.files = files

    def now_playing(self) -> NowPlaying:
        meta = self.player.metadata or {}
        return NowPlaying(source="playlist", title=meta.get("title") or self.player.media_title or self.label, artist=meta.get("artist"), album=meta.get("album"), station=self.label)


AUDIO_EXT = {".mp3", ".ogg", ".oga", ".opus", ".flac", ".wav", ".m4a", ".aac", ".wma"}


def playlist_files(name: str, media_dir: str) -> list[Path]:
    base = Path(media_dir)
    cand = base / name
    if cand.is_dir():
        return sorted(p for p in cand.iterdir() if p.suffix.lower() in AUDIO_EXT)
    for ext in (".m3u", ".m3u8"):
        f = base / f"{name}{ext}"
        if f.exists():
            out = []
            for line in f.read_text(errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                p = Path(line) if line.startswith("/") or "://" in line else base / line
                out.append(p)
            return out
    if cand.is_file() and cand.suffix.lower() in AUDIO_EXT:
        return [cand]
    return []


def list_playlists(media_dir: str) -> list[dict[str, Any]]:
    base = Path(media_dir)
    if not base.exists():
        return []
    out = []
    for p in sorted(base.iterdir()):
        if p.is_dir():
            n = len([f for f in p.iterdir() if f.suffix.lower() in AUDIO_EXT])
            if n:
                out.append({"name": p.name, "kind": "folder", "tracks": n})
        elif p.suffix.lower() in (".m3u", ".m3u8"):
            out.append({"name": p.stem, "kind": "m3u", "tracks": len(playlist_files(p.stem, media_dir))})
        elif p.suffix.lower() in AUDIO_EXT:
            out.append({"name": p.name, "kind": "file", "tracks": 1})
    return out


def _label_from_url(url: str) -> str:
    from urllib.parse import urlparse

    u = urlparse(url)
    return u.netloc or url


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)[:60]
