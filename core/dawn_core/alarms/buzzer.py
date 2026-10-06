"""The backup tone: the last rung of an alarm's ladder (its source, then the chime, then this).

It needs neither mpv nor the alarm's source. The beeps are generated here (no file to lose) and played by the
first program that works: pw-play (PipeWire), paplay (PipeWire's PulseAudio side), aplay on ALSA's default
device, and then aplay straight on each sound card, which only succeeds when PipeWire is not holding the card,
that is when PipeWire is what broke. An optional GPIO buzzer (`alarm_defaults.buzzer.gpio_pin`) beeps with it,
independent of the whole audio stack. In the simulator nothing is played: the hub's "Audio flowing" toggle says
whether the tone is heard, so the face's own beep can be tried on a laptop.
"""

from __future__ import annotations

import array
import asyncio
import io
import logging
import math
import re
import shutil
import sys
import tempfile
import time
import wave
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..audio.sources import AudioSource
from ..context import DawnContext
from ..state.ui import NowPlaying

log = logging.getLogger("dawn.buzzer")

RATE = 48_000
TONE_HZ = 2_000.0
BEEP_S, GAP_S, BEEPS, PAUSE_S = 0.12, 0.08, 4, 0.52  # one cycle: four beeps, then a pause (1.32 s)
CYCLES = 4  # per file; the player is simply started again for the next one
SIM_CLIENT = "dawn-backup-tone"


def beep_wav(rate: int = RATE, cycles: int = CYCLES) -> bytes:
    """`cycles` times four 120 ms beeps 80 ms apart and 520 ms of quiet, as 16-bit mono WAV. A 2 kHz tone with some
    third harmonic: far above the speaker's 110 Hz high-pass, hard to sleep through, and nothing like the chimes."""
    frames = array.array("h")
    n = int(BEEP_S * rate)
    edge = int(0.005 * rate)  # 5 ms ramps: no clicks
    beep = array.array("h")
    for i in range(n):
        t = i / rate
        env = min(1.0, i / edge, (n - 1 - i) / edge)
        v = (math.sin(2 * math.pi * TONE_HZ * t) + 0.3 * math.sin(6 * math.pi * TONE_HZ * t)) / 1.3
        beep.append(int(0.55 * 32767 * env * v))
    gap = array.array("h", [0]) * int(GAP_S * rate)
    pause = array.array("h", [0]) * int(PAUSE_S * rate)
    for _ in range(cycles):
        for _ in range(BEEPS):
            frames.extend(beep)
            frames.extend(gap)
        frames.extend(pause)
    if sys.byteorder == "big":
        frames.byteswap()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames.tobytes())
    return buf.getvalue()


def alsa_cards(text: str | None = None) -> list[str]:
    """ALSA card ids from /proc/asound/cards: the I2S amp and USB first, HDMI last (a TV without speakers is a
    poor last resort)."""
    if text is None:
        try:
            text = Path("/proc/asound/cards").read_text()
        except OSError:
            return []
    cards = re.findall(r"^\s*\d+\s+\[(\S+)\s*\]:\s*(.*)$", text, re.M)

    def rank(card: tuple[str, str]) -> int:
        blob = " ".join(card).lower()
        return 2 if "hdmi" in blob else 0 if "hifiberry" in blob or "usb" in blob else 1

    return [cid for cid, _ in sorted(cards, key=rank)]


def player_commands(wav: str, which: Callable[[str], str | None] = shutil.which, cards: list[str] | None = None) -> list[list[str]]:
    """Every way there is here to play `wav`, most likely to work first."""
    cmds: list[list[str]] = []
    if which("pw-play"):
        cmds.append(["pw-play", wav])
    if which("paplay"):
        cmds.append(["paplay", wav])
    if which("aplay"):
        cmds.append(["aplay", "-q", wav])
        for card in alsa_cards() if cards is None else cards:
            cmds.append(["aplay", "-q", "-D", f"plughw:CARD={card},DEV=0", wav])
    return cmds


class GpioBuzzer:
    """An active piezo buzzer on a GPIO pin, beeping the same pattern as the tone."""

    def __init__(self) -> None:
        self._dev: Any = None
        self._pin: tuple[int, bool] | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self, pin: int, active_high: bool) -> None:
        if self._task and not self._task.done():
            return
        try:
            if self._dev is None or self._pin != (pin, active_high):
                self.close()
                from gpiozero import Buzzer as _Buzzer  # type: ignore

                self._dev = _Buzzer(pin, active_high=active_high, initial_value=False)
                self._pin = (pin, active_high)
        except Exception as e:  # noqa: BLE001
            log.error("GPIO buzzer on GPIO%s unavailable: %s", pin, e)
            self._dev = None
            return
        self._task = asyncio.create_task(self._pattern(), name="gpio-buzzer")

    async def _pattern(self) -> None:
        dev = self._dev
        try:
            while True:
                for _ in range(BEEPS):
                    dev.on()
                    await asyncio.sleep(BEEP_S)
                    dev.off()
                    await asyncio.sleep(GAP_S)
                await asyncio.sleep(PAUSE_S)
        finally:
            with suppress(Exception):
                dev.off()

    @property
    def beeping(self) -> bool:
        return self._task is not None and not self._task.done()

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        if self._dev is not None:
            with suppress(Exception):
                self._dev.off()

    def close(self) -> None:
        self.stop()
        if self._dev is not None:
            with suppress(Exception):
                self._dev.close()
        self._dev = None
        self._pin = None


class Buzzer:
    """Plays the backup tone until stopped, moving on to the next player whenever one fails."""

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self.gpio = GpioBuzzer()
        self.sounding = False  # a player is playing the tone right now, as far as can be told
        self.player: str | None = None  # the command doing it
        self.problem: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._good: list[str] | None = None  # the command that worked last time
        self._wav: Path | None = None
        self._logged: str | None = None

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.active:
            return
        self._task = asyncio.create_task(self._run(), name="backup-tone")
        b = self.ctx.config.alarm_defaults.buzzer
        if b.gpio_pin is not None and not self.ctx.sim:
            self.gpio.start(b.gpio_pin, b.gpio_active_high)

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._kill()
        self.gpio.stop()
        self.sounding = False

    def close(self) -> None:
        self._kill()
        self.gpio.close()

    # ---- playing -----------------------------------------------------------
    def wav_path(self) -> Path:
        if self._wav and self._wav.exists():
            return self._wav
        data = beep_wav()
        for d in (self.ctx.runtime_dir, Path(tempfile.gettempdir())):
            try:
                p = d / "dawn-backup-tone.wav"
                p.write_bytes(data)
                self._wav = p
                return p
            except OSError:
                continue
        raise RuntimeError("nowhere to write the backup tone")

    async def _run(self) -> None:
        if self.ctx.sim:
            await self._run_sim()
            return
        while True:
            try:
                cmds = player_commands(str(self.wav_path()))
                problem = None if cmds else "none of pw-play, paplay or aplay is installed"
            except Exception as e:  # noqa: BLE001
                cmds, problem = [], f"backup tone file: {e}"
            if self._good in cmds:
                cmds.remove(self._good)  # type: ignore[arg-type]
                cmds.insert(0, self._good)  # type: ignore[arg-type]
            played = False
            for cmd in cmds:
                if await self._play(cmd):
                    self._good, played = cmd, True
                    break
            if played:
                continue
            self.sounding = False
            self.problem = problem or f"no program could play the backup tone (last: {self.problem})"
            if self._logged != self.problem:
                self._logged = self.problem
                log.error("backup tone: %s", self.problem)
            await asyncio.sleep(3)

    async def _play(self, cmd: list[str]) -> bool:
        """Play the file once with `cmd`: True when it played it through."""
        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        except OSError as e:
            self.problem = f"{cmd[0]}: {e}"
            return False
        self._proc = proc
        out = asyncio.ensure_future(proc.communicate())
        try:
            done, _ = await asyncio.wait({out}, timeout=0.4)
            if not done:
                self.sounding = True  # still going: it is playing
                name = " ".join(cmd[:-1])
                if self.player != name:
                    self.player = name
                    log.warning("backup tone playing with %s", name)
            _, err = await out
        finally:
            if proc.returncode is None:
                with suppress(ProcessLookupError):
                    proc.kill()
            if not out.done():
                out.cancel()
            self._proc = None
        if proc.returncode == 0 and time.monotonic() - t0 >= 1.0:
            self.problem = None
            return True
        self.sounding = False
        msg = (err or b"").decode(errors="ignore").strip().splitlines()
        self.problem = f"{cmd[0]} failed: {msg[0] if msg else f'exit {proc.returncode}'}"
        return False

    def _kill(self) -> None:
        p = self._proc
        if p is not None and p.returncode is None:
            with suppress(ProcessLookupError):
                p.kill()
        self._proc = None

    async def _run_sim(self) -> None:
        from ..audio.service import AudioService

        self.player = "sim"
        log.warning("sim: the backup tone is beeping")
        backend = self.ctx.svc(AudioService).backend
        while True:
            self.sounding = await backend.stream_running(SIM_CLIENT) is not False
            await asyncio.sleep(1.0)


class BuzzerSource(AudioSource):
    """The backup tone as an arbiter source at the alarm level, so everything below it stays paused while it sounds."""

    kind = "buzzer"  # type: ignore[assignment]
    label = "Backup tone"
    ref = "buzzer:"

    def __init__(self, buzzer: Buzzer):
        super().__init__()
        self.buzzer = buzzer
        self._on = False

    async def start(self) -> None:
        self._on = True
        await self.buzzer.start()

    async def stop(self) -> None:
        self._on = False
        await self.buzzer.stop()

    async def pause(self) -> None:
        self._on = False
        await self.buzzer.stop()

    async def resume(self) -> None:
        self._on = True
        await self.buzzer.start()

    @property
    def playing(self) -> bool:
        return self._on and self.buzzer.active

    @property
    def flowing(self) -> bool:
        return self.playing and self.buzzer.sounding

    def now_playing(self) -> NowPlaying:
        return NowPlaying(source="buzzer", title=self.label, station="Alarm")
