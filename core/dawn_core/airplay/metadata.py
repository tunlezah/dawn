"""Parser for the shairport-sync metadata pipe.

The pipe carries XML-ish items:
  <item><type>636f7265</type><code>6d696e6d</code><length>12</length>
  <data encoding="base64">...</data></item>
type/code are hex of 4-char tags: type 'core' (DMAP: minm title, asar artist, asal album, ...)
and 'ssnc' (shairport: pbeg/pend/prsm/pfls, PICT artwork, snam client name, pvol volume...).
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

_ITEM = re.compile(rb"<item><type>([0-9a-f]+)</type><code>([0-9a-f]+)</code><length>(\d+)</length>(?:\s*<data encoding=\"base64\">\s*(.*?)\s*</data>)?\s*</item>", re.S)


@dataclass
class MetaItem:
    type: str  # 'core' | 'ssnc'
    code: str  # e.g. 'minm'
    data: bytes

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", errors="replace")


def _tag(hexstr: bytes) -> str:
    try:
        return bytes.fromhex(hexstr.decode()).decode("ascii", errors="replace")
    except ValueError:
        return hexstr.decode(errors="replace")


class MetadataParser:
    """Feed raw bytes; get MetaItems out. Keeps a partial-item buffer between feeds."""

    def __init__(self) -> None:
        self.buf = b""

    def feed(self, chunk: bytes) -> list[MetaItem]:
        self.buf += chunk
        items: list[MetaItem] = []
        last_end = 0
        for m in _ITEM.finditer(self.buf):
            typ, code, _length, data = m.groups()
            raw = b""
            if data:
                try:
                    raw = base64.b64decode(re.sub(rb"\s+", b"", data))
                except ValueError:
                    raw = b""
            items.append(MetaItem(_tag(typ), _tag(code), raw))
            last_end = m.end()
        self.buf = self.buf[last_end:]
        if len(self.buf) > 4_000_000:  # runaway without closing tag
            self.buf = b""
        return items


@dataclass
class AirPlayTrack:
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    client: str | None = None
    artwork: bytes | None = None
    artwork_version: int = 0
    playing: bool = False
    session: bool = False
    volume_db: float | None = None
    extras: dict[str, str] = field(default_factory=dict)

    def apply(self, item: MetaItem) -> str | None:
        """Update from an item; return an event name when the play state changes."""
        if item.type == "core":
            if item.code == "minm":
                self.title = item.text or None
            elif item.code == "asar":
                self.artist = item.text or None
            elif item.code == "asal":
                self.album = item.text or None
            return None
        if item.type == "ssnc":
            if item.code == "pbeg":
                self.session = True
                self.playing = True
                return "begin"
            if item.code == "pend":
                self.session = False
                self.playing = False
                self.title = self.artist = self.album = None
                self.artwork = None
                return "end"
            if item.code == "prsm":
                self.playing = True
                return "resume"
            if item.code in ("pfls", "paus"):
                self.playing = False
                return "pause"
            if item.code == "PICT":
                self.artwork = item.data or None
                self.artwork_version += 1
                return "artwork"
            if item.code == "snam":
                self.client = item.text or None
            elif item.code == "pvol":
                try:
                    self.volume_db = float(item.text.split(",")[0])
                except ValueError:
                    pass
            elif item.code in ("mdst", "mden"):
                return None
            else:
                self.extras[item.code] = item.text[:80]
        return None


def artwork_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "application/octet-stream"
