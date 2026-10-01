"""Audio output backends: sink enumeration/selection, master volume, mute, EQ,
and stream-activity detection.

- PipeWireBackend: wpctl / pw-dump / pw-cli / pw-metadata (the Pi).
- AlsaBackend: amixer / aplay -l (no PipeWire; the "any ALSA sink" fallback).
- SimBackend: in-memory, asks the sim hub whether audio is "flowing".
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("dawn.audio.backend")

SinkKind = str  # usb | hifiberry | headphones | hdmi | other


@dataclass
class Sink:
    id: str
    name: str
    description: str
    kind: SinkKind = "other"
    serial: str | None = None


async def run(*cmd: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return proc.returncode or 0, out.decode(errors="ignore")
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except TimeoutError:
        return 124, "timeout"


def classify_sink(name: str, description: str, props: dict[str, Any] | None = None) -> SinkKind:
    props = props or {}
    blob = " ".join([name, description, str(props.get("device.bus", "")), str(props.get("alsa.card_name", "")), str(props.get("api.alsa.card.name", ""))]).lower()
    if "hdmi" in blob:
        return "hdmi"
    if "hifiberry" in blob or "snd_rpi_hifiberry" in blob or "sndrpihifiberry" in blob:
        return "hifiberry"
    if "headphone" in blob or "bcm2835" in blob or "analog" in blob and "usb" not in blob:
        return "headphones"
    if "usb" in blob:
        return "usb"
    return "other"


class AudioBackend:
    name = "none"
    supports_eq = False

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def list_sinks(self) -> list[Sink]:
        return []
    async def default_sink(self) -> str | None:
        return None
    async def set_default_sink(self, sink: Sink) -> None: ...
    async def set_volume(self, percent: int) -> None: ...
    async def set_mute(self, muted: bool) -> None: ...
    async def set_eq(self, bass_db: float, treble_db: float) -> None: ...
    async def stream_running(self, client_name: str) -> bool | None:
        """True/False when known, None when the backend cannot tell."""
        return None


class PipeWireBackend(AudioBackend):
    name = "pipewire"
    supports_eq = True
    EQ_SINK = "dawn_eq"

    def __init__(self) -> None:
        self._eq_present = False
        self._hw_sink: Sink | None = None

    async def start(self) -> None:
        rc, _ = await run("wpctl", "status")
        if rc != 0:
            raise RuntimeError("wpctl not available")
        dump = await self._dump()
        self._eq_present = any(n.get("info", {}).get("props", {}).get("node.name") == self.EQ_SINK for n in dump)
        log.info("pipewire backend ready (eq filter-chain %s)", "present" if self._eq_present else "absent")

    async def _dump(self) -> list[dict[str, Any]]:
        rc, out = await run("pw-dump", timeout=8)
        if rc != 0:
            return []
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            # pw-dump may emit several concatenated arrays
            data = []
            for m in re.finditer(r"\[.*?\n\]", out, re.S):
                try:
                    data += json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
        return [d for d in data if isinstance(d, dict)]

    async def list_sinks(self) -> list[Sink]:
        out = []
        for n in await self._dump():
            info = n.get("info") or {}
            props = info.get("props") or {}
            if props.get("media.class") != "Audio/Sink":
                continue
            name = props.get("node.name", "")
            if name == self.EQ_SINK:
                continue
            desc = props.get("node.description") or props.get("node.nick") or name
            out.append(Sink(id=str(n.get("id")), name=name, description=desc, kind=classify_sink(name, desc, props), serial=str(props.get("object.serial", ""))))
        return out

    async def default_sink(self) -> str | None:
        rc, out = await run("wpctl", "inspect", "@DEFAULT_AUDIO_SINK@")
        if rc != 0:
            return None
        m = re.search(r'node\.name = "([^"]+)"', out)
        return m.group(1) if m else None

    async def set_default_sink(self, sink: Sink) -> None:
        self._hw_sink = sink
        if self._eq_present:
            # everything plays into the EQ sink; its output stream targets the hardware sink
            eq_id, out_id = await self._eq_nodes()
            if eq_id:
                await run("wpctl", "set-default", eq_id)
            if out_id:
                await run("pw-metadata", "-n", "default", out_id, "target.object", sink.name)
        else:
            await run("wpctl", "set-default", sink.id)

    async def _eq_nodes(self) -> tuple[str | None, str | None]:
        eq_id = out_id = None
        for n in await self._dump():
            props = (n.get("info") or {}).get("props") or {}
            if props.get("node.name") == self.EQ_SINK:
                eq_id = str(n.get("id"))
            elif props.get("node.name") in (f"output.{self.EQ_SINK}", f"{self.EQ_SINK}.output") or (
                props.get("media.class") == "Stream/Output/Audio" and self.EQ_SINK in str(props.get("node.name", ""))
            ):
                out_id = str(n.get("id"))
        return eq_id, out_id

    async def set_volume(self, percent: int) -> None:
        target = self._hw_sink.id if self._hw_sink else "@DEFAULT_AUDIO_SINK@"
        await run("wpctl", "set-volume", "-l", "1.0", target, f"{max(0, min(100, percent)) / 100:.3f}")

    async def set_mute(self, muted: bool) -> None:
        target = self._hw_sink.id if self._hw_sink else "@DEFAULT_AUDIO_SINK@"
        await run("wpctl", "set-mute", target, "1" if muted else "0")

    async def set_eq(self, bass_db: float, treble_db: float) -> None:
        if not self._eq_present:
            return
        eq_id, _ = await self._eq_nodes()
        if eq_id:
            params = f'{{ params = [ "bass:Gain" {bass_db:.1f} "treble:Gain" {treble_db:.1f} ] }}'
            await run("pw-cli", "set-param", eq_id, "Props", params)

    async def stream_running(self, client_name: str) -> bool | None:
        for n in await self._dump():
            info = n.get("info") or {}
            props = info.get("props") or {}
            if props.get("media.class") != "Stream/Output/Audio":
                continue
            names = {str(props.get(k, "")) for k in ("application.name", "node.name", "media.name", "application.process.binary")}
            if any(client_name in x for x in names):
                return info.get("state") == "running"
        return False


class AlsaBackend(AudioBackend):
    name = "alsa"

    def __init__(self) -> None:
        self.card: str | None = None
        self.control = "Master"

    async def start(self) -> None:
        if not shutil.which("amixer"):
            raise RuntimeError("amixer not available")
        sinks = await self.list_sinks()
        if sinks:
            await self.set_default_sink(sinks[0])

    async def list_sinks(self) -> list[Sink]:
        rc, out = await run("aplay", "-l")
        sinks = []
        for m in re.finditer(r"card (\d+): (\S+) \[([^\]]+)\], device (\d+): ([^\[]+)\[", out):
            card, name, desc, _dev, _ = m.groups()
            sinks.append(Sink(id=card, name=name, description=desc.strip(), kind=classify_sink(name, desc)))
        return sinks

    async def set_default_sink(self, sink: Sink) -> None:
        self.card = sink.id
        rc, out = await run("amixer", "-c", sink.id, "scontrols")
        for cand in ("Master", "PCM", "Digital", "Headphone", "Speaker"):
            if f"'{cand}'" in out:
                self.control = cand
                break

    async def set_volume(self, percent: int) -> None:
        if self.card is None:
            return
        await run("amixer", "-q", "-c", self.card, "sset", self.control, f"{percent}%")

    async def set_mute(self, muted: bool) -> None:
        if self.card is None:
            return
        await run("amixer", "-q", "-c", self.card, "sset", self.control, "mute" if muted else "unmute")


class SimBackend(AudioBackend):
    name = "sim"
    supports_eq = True

    def __init__(self, hub_url: str):
        self.hub = hub_url.rstrip("/")
        self.volume = 0
        self.muted = False
        self.default = "sim_usb"
        self.eq = (0.0, 0.0)
        self._sinks = [
            Sink("41", "alsa_output.usb-Topping_D10-00.analog-stereo", "Topping D10 (USB DAC)", "usb"),
            Sink("42", "alsa_output.platform-soc_sound.stereo-fallback", "HiFiBerry MiniAmp", "hifiberry"),
            Sink("43", "alsa_output.platform-bcm2835_audio.stereo-fallback", "bcm2835 Headphones", "headphones"),
            Sink("44", "alsa_output.platform-fef00700.hdmi.hdmi-stereo", "vc4-hdmi", "hdmi"),
        ]

    async def list_sinks(self) -> list[Sink]:
        return list(self._sinks)

    async def default_sink(self) -> str | None:
        return self.default

    async def set_default_sink(self, sink: Sink) -> None:
        self.default = sink.name
        log.info("sim: default sink -> %s", sink.description)

    async def set_volume(self, percent: int) -> None:
        self.volume = percent

    async def set_mute(self, muted: bool) -> None:
        self.muted = muted

    async def set_eq(self, bass_db: float, treble_db: float) -> None:
        self.eq = (bass_db, treble_db)

    async def stream_running(self, client_name: str) -> bool | None:
        try:
            async with httpx.AsyncClient(timeout=1.5) as c:
                r = await c.get(f"{self.hub}/audio/flowing")
                return bool(r.json().get("flowing"))
        except Exception:  # noqa: BLE001
            return True


async def make_backend(kind: str, hub_url: str, sim: bool) -> AudioBackend:
    if sim or kind == "sim":
        return SimBackend(hub_url)
    order = {"auto": ["pipewire", "alsa"], "pipewire": ["pipewire", "alsa"], "alsa": ["alsa"]}[kind]
    for k in order:
        b: AudioBackend = PipeWireBackend() if k == "pipewire" else AlsaBackend()
        try:
            await b.start()
            return b
        except Exception as e:  # noqa: BLE001
            log.warning("audio backend %s unavailable: %s", k, e)
    log.error("no audio backend available; audio control disabled")
    return AudioBackend()
