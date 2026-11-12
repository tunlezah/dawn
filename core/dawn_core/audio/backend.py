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
from contextlib import suppress
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
    """(exit code, output). 124 = timed out (and killed: pw-dump runs every second during a ring, so a wedged
    PipeWire must not leave a hung process a second), 127 = could not run."""
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except OSError as e:
        return 127, f"{cmd[0]}: {e}"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        with suppress(ProcessLookupError):
            proc.kill()
        return 124, "timeout"
    except asyncio.CancelledError:
        with suppress(ProcessLookupError):
            proc.kill()  # the caller gave up (a ring's start has a time limit): the tool goes with it
        raise
    return proc.returncode or 0, out.decode(errors="ignore")


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
    # a stream that runs but is not connected to the speaker (PipeWire only): why, else None
    link_problem: str | None = None

    @property
    def eq_present(self) -> bool:
        """The EQ/protection filter chain is in the graph (PipeWire only)."""
        return self.supports_eq

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def list_sinks(self) -> list[Sink]:
        return []
    async def default_sink(self) -> str | None:
        return None
    async def set_default_sink(self, sink: Sink) -> None: ...
    async def set_volume(self, percent: int) -> None: ...
    async def set_mute(self, muted: bool) -> None: ...
    async def set_eq(self, bass_db: float, treble_db: float, highpass_hz: float | None = None) -> None: ...
    async def stream_running(self, client_name: str) -> bool | None:
        """True/False when known, None when the backend cannot tell."""
        return None

    async def set_stream_mute(self, match: str, muted: bool) -> None:
        """Mute/unmute output streams whose name contains `match` (external sources)."""


class PipeWireBackend(AudioBackend):
    name = "pipewire"
    supports_eq = True
    EQ_SINK = "dawn_eq"
    HIGHPASS_OFF_HZ = 10.0  # the biquad stays in the graph; this far down it changes nothing audible

    NULL_SINKS = ("auto_null",)  # pipewire-pulse's stand-in when there is no sound card: it plays into nothing

    def __init__(self) -> None:
        self._eq_present = False
        self._hw_sink: Sink | None = None
        self.link_problem = None

    @property
    def eq_present(self) -> bool:
        return self._eq_present

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
            if name == self.EQ_SINK or name in self.NULL_SINKS or props.get("factory.name") == "support.null-audio-sink":
                continue  # the tone control is not an output, and a null sink is not a speaker
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
        await self._wpctl("set-volume", "-l", "1.0", target, f"{max(0, min(100, percent)) / 100:.3f}")

    async def set_mute(self, muted: bool) -> None:
        target = self._hw_sink.id if self._hw_sink else "@DEFAULT_AUDIO_SINK@"
        await self._wpctl("set-mute", target, "1" if muted else "0")

    @staticmethod
    async def _wpctl(*args: str) -> None:
        """A wpctl call that must take effect. A sink id only lives as long as the PipeWire that gave it: after
        PipeWire or WirePlumber restarts, the old id names nothing (or another node), wpctl says so with its exit
        code, and the caller has to hear about it, or an alarm's volume and unmute go nowhere unnoticed."""
        rc, out = await run("wpctl", *args)
        if rc == 124:
            # PipeWire is there but answers nothing. Not the same as a refusal: choosing the sink again would only
            # hang as well, and whoever is waiting (an alarm about to sound) has no time for that.
            raise TimeoutError(f"wpctl {args[0]}: no answer from PipeWire")
        if rc != 0:
            raise RuntimeError(f"wpctl {args[0]}: {out.strip().splitlines()[-1] if out.strip() else f'exit {rc}'}")

    async def set_eq(self, bass_db: float, treble_db: float, highpass_hz: float | None = None) -> None:
        if not self._eq_present:
            return
        eq_id, _ = await self._eq_nodes()
        if eq_id:
            hp = highpass_hz if highpass_hz is not None else self.HIGHPASS_OFF_HZ
            params = f'{{ params = [ "highpass:Freq" {hp:.1f} "bass:Gain" {bass_db:.1f} "treble:Gain" {treble_db:.1f} ] }}'
            await run("pw-cli", "set-param", eq_id, "Props", params)

    async def stream_running(self, client_name: str) -> bool | None:
        """The stream of `client_name` runs *and* is connected through to the speaker. A stream runs just as
        happily into the tone control when nothing links that to the sound card (PipeWire came back and the link's
        target was lost), or into a null sink, and then "running" is silence."""
        dump = await self._dump()
        for n in dump:
            info = n.get("info") or {}
            props = info.get("props") or {}
            if props.get("media.class") != "Stream/Output/Audio":
                continue
            names = {str(props.get(k, "")) for k in ("application.name", "node.name", "media.name", "application.process.binary")}
            if any(client_name in x for x in names):
                if info.get("state") != "running":
                    return False
                return self.reaches_speaker(dump, str(n.get("id"))) is not False
        return False

    def reaches_speaker(self, dump: list[dict[str, Any]], stream_id: str) -> bool | None:
        """Whether the links in `dump` carry stream `stream_id` to the chosen sound card, directly or through the
        tone control. None when that cannot be told (no sink chosen yet, or a dump without readable links), which
        never counts against the stream: this check may only ever add a "no", not take a sound away."""
        if self._hw_sink is None:
            return None
        ids: dict[str, str] = {}
        links: set[tuple[str, str]] = set()
        for n in dump:
            info = n.get("info") or {}
            props = info.get("props") or {}
            if str(n.get("type", "")).endswith(":Link"):
                out = info.get("output-node-id", props.get("link.output.node"))
                inp = info.get("input-node-id", props.get("link.input.node"))
                if out is not None and inp is not None and info.get("state") != "error":
                    links.add((str(out), str(inp)))
            elif props.get("node.name"):
                ids[str(props["node.name"])] = str(n.get("id"))
        if not links:
            return None
        hw = ids.get(self._hw_sink.name)
        eq = ids.get(self.EQ_SINK)
        eq_out = ids.get(f"output.{self.EQ_SINK}") or ids.get(f"{self.EQ_SINK}.output")
        if hw is None:
            ok, why = False, f"the sound card {self._hw_sink.description or self._hw_sink.name} is not in PipeWire"
        elif (stream_id, hw) in links:
            ok, why = True, ""
        elif eq is not None and (stream_id, eq) in links:
            ok = eq_out is not None and (eq_out, hw) in links
            why = "" if ok else f"the tone control is not connected to {self._hw_sink.description or self._hw_sink.name}"
        else:
            ok, why = False, f"the stream is not connected to {self._hw_sink.description or self._hw_sink.name}"
        if ok:
            self.link_problem = None
        elif self.link_problem != why:
            self.link_problem = why
            log.error("audio is playing but cannot be heard: %s", why)
        return ok


    async def set_stream_mute(self, match: str, muted: bool) -> None:
        for n in await self._dump():
            info = n.get("info") or {}
            props = info.get("props") or {}
            if props.get("media.class") != "Stream/Output/Audio":
                continue
            names = " ".join(str(props.get(k, "")) for k in ("application.name", "node.name", "media.name", "application.process.binary")).lower()
            if match.lower() in names:
                await run("wpctl", "set-mute", str(n.get("id")), "1" if muted else "0")


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
        self.highpass_hz: float | None = None
        self._sinks = [
            Sink("41", "alsa_output.usb-Topping_D10-00.analog-stereo", "Topping D10 (USB DAC)", "usb"),
            Sink("42", "alsa_output.platform-soc_sound.stereo-fallback", "Audio Amp SHIM (hifiberry-dac)", "hifiberry"),
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

    async def set_eq(self, bass_db: float, treble_db: float, highpass_hz: float | None = None) -> None:
        self.eq = (bass_db, treble_db)
        self.highpass_hz = highpass_hz

    async def stream_running(self, client_name: str) -> bool | None:
        try:
            async with httpx.AsyncClient(timeout=1.5) as c:
                r = await c.get(f"{self.hub}/audio/flowing", params={"client": client_name})
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
