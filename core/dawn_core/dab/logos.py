"""Station logos: the latest MOT slide per service, or a generated monogram."""

from __future__ import annotations

import colorsys
import hashlib
import html
import logging
import re
from pathlib import Path

log = logging.getLogger("dawn.dab.logos")

_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp", "image/gif": "gif"}


def service_colour(sid: str) -> str:
    h = hashlib.sha1(sid.lower().encode()).digest()
    hue = h[0] / 255
    sat = 0.45 + (h[1] / 255) * 0.25
    val = 0.55 + (h[2] / 255) * 0.25
    r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def monogram_letters(label: str) -> str:
    words = [w for w in re.split(r"[\s\-_/.]+", label.strip()) if w]
    if not words:
        return "??"
    if len(words) == 1:
        w = words[0]
        return (w[:2]).upper() if len(w) >= 2 else (w + w).upper()
    return (words[0][0] + words[1][0]).upper()


def monogram_svg(sid: str, label: str, size: int = 256) -> str:
    colour = service_colour(sid)
    letters = html.escape(monogram_letters(label))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 256 256">'
        f'<rect width="256" height="256" rx="40" fill="{colour}"/>'
        f'<text x="128" y="128" dy="0.36em" text-anchor="middle" font-family="Inter, system-ui, sans-serif" '
        f'font-weight="700" font-size="124" fill="rgba(255,255,255,0.92)" letter-spacing="-4">{letters}</text>'
        f"</svg>"
    )


class LogoCache:
    def __init__(self, data_dir: Path):
        self.dir = data_dir / "logos"
        self.dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, sid: str) -> Path | None:
        for ext in ("png", "jpg", "webp", "gif"):
            p = self.dir / f"{sid}.{ext}"
            if p.exists():
                return p
        return None

    def store(self, sid: str, data: bytes, content_type: str) -> Path:
        ext = _EXT.get(content_type.split(";")[0].strip().lower(), "jpg")
        for old in self.dir.glob(f"{sid}.*"):
            if old.suffix != f".{ext}":
                old.unlink(missing_ok=True)
        p = self.dir / f"{sid}.{ext}"
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
        return p

    def version(self, sid: str) -> int:
        p = self.path_for(sid)
        return int(p.stat().st_mtime) if p else 0

    def url(self, sid: str) -> str:
        return f"/api/dab/logo/{sid}?v={self.version(sid)}"
