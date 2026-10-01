"""welle-cli web interface client and mux.json normalisation."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("dawn.dab.welle")


def norm_sid(v: Any) -> str:
    """Normalise a service id to lowercase hex without 0x (4 digits for audio services)."""
    if v is None:
        return ""
    if isinstance(v, int):
        return f"{v:04x}"
    s = str(v).strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    try:
        return f"{int(s, 16):04x}"
    except ValueError:
        return s


@dataclass
class ServiceInfo:
    sid: str
    label: str
    short_label: str = ""
    bitrate: int | None = None
    codec: str | None = None  # "DAB+" / "DAB"
    pty: str | None = None
    url_mp3: str | None = None
    dls: str | None = None
    dls_time: int | None = None
    mot_lastchange: int | None = None
    mot_name: str | None = None
    audio_level: float | None = None  # dB, max of L/R
    subchannel_id: int | None = None


@dataclass
class MuxInfo:
    ensemble_label: str = ""
    ensemble_id: str = ""
    channel: str | None = None
    snr: float | None = None
    sync: bool = False
    services: list[ServiceInfo] = field(default_factory=list)
    utc_time: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def parse_mux(j: dict[str, Any]) -> MuxInfo:
    ens = j.get("ensemble") or {}
    demod = j.get("demodulator") or {}
    info = MuxInfo(
        ensemble_label=str(ens.get("label") or "").strip(),
        ensemble_id=norm_sid(ens.get("id")) if ens.get("id") not in (None, "") else "",
        channel=j.get("channel"),
        snr=_f(demod.get("snr")),
        utc_time=j.get("utctime"),
        raw=j,
    )
    for s in j.get("services") or []:
        comps = s.get("components") or []
        sub = (comps[0].get("subchannel") if comps else None) or {}
        codec = s.get("mode") or (comps[0].get("ascty") if comps else None)
        al = s.get("audiolevel") or {}
        level = None
        if isinstance(al, dict) and al:
            vals = [v for v in (al.get("left"), al.get("right")) if isinstance(v, int | float)]
            level = max(vals) if vals else None
        mot = s.get("mot") or {}
        dls = s.get("dls") or {}
        info.services.append(
            ServiceInfo(
                sid=norm_sid(s.get("sid")),
                label=str(s.get("label") or "").strip(),
                short_label=str(s.get("shortlabel") or "").strip(),
                bitrate=_i(sub.get("bitrate")),
                codec=str(codec) if codec else None,
                pty=s.get("ptystring") or None,
                url_mp3=s.get("url_mp3"),
                dls=(str(dls.get("label")).strip() if isinstance(dls, dict) and dls.get("label") else (str(dls).strip() if isinstance(dls, str) and dls else None)),
                dls_time=_i(dls.get("time")) if isinstance(dls, dict) else None,
                mot_lastchange=_i(mot.get("lastchange") or mot.get("time")) if isinstance(mot, dict) else None,
                mot_name=mot.get("name") if isinstance(mot, dict) else None,
                audio_level=level,
                subchannel_id=_i(sub.get("id")),
            )
        )
    # Sync: explicit flag when welle exposes one, else "we decoded an ensemble".
    explicit = demod.get("sync")
    if isinstance(explicit, bool):
        info.sync = explicit and (bool(info.ensemble_label) or bool(info.services))
    else:
        info.sync = bool(info.ensemble_label) or bool(info.services)
    return info


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class WelleClient:
    def __init__(self, base_url: str, timeout: float = 4.0):
        self.base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def mux(self) -> MuxInfo | None:
        try:
            r = await self._client.get(f"{self.base}/mux.json")
            if r.status_code != 200:
                return None
            return parse_mux(r.json())
        except (httpx.HTTPError, ValueError):
            return None

    async def reachable(self) -> bool:
        try:
            r = await self._client.get(f"{self.base}/mux.json")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def channel(self) -> str | None:
        try:
            r = await self._client.get(f"{self.base}/channel")
            return r.text.strip().upper() if r.status_code == 200 else None
        except httpx.HTTPError:
            return None

    async def set_channel(self, channel: str) -> bool:
        try:
            r = await self._client.post(f"{self.base}/channel", content=channel.upper())
            return r.status_code in (200, 204)
        except httpx.HTTPError:
            return False

    async def slide(self, sid: str) -> tuple[bytes, str] | None:
        try:
            r = await self._client.get(f"{self.base}/slide/{sid}")
            if r.status_code != 200 or not r.content:
                return None
            return r.content, r.headers.get("content-type", "image/jpeg")
        except httpx.HTTPError:
            return None

    def stream_url(self, svc: ServiceInfo) -> str:
        path = svc.url_mp3 or f"/mp3/{svc.sid}"
        return f"{self.base}{path}"
