#!/usr/bin/env python3
"""Synthesize the bundled chime sounds (pure Python) and encode them to OGG
with ffmpeg. Outputs core/dawn_core/assets/chimes/*.ogg. Each file is a loopable
~8 s phrase; the chime source loops it while ringing."""

from __future__ import annotations

import math
import random
import struct
import subprocess
import sys
import wave
from pathlib import Path

SR = 22050
OUT = Path(__file__).resolve().parents[1] / "core" / "dawn_core" / "assets" / "chimes"


def write_wav(path: Path, samples: list[float]) -> None:
    peak = max(1e-6, max(abs(s) for s in samples))
    gain = 0.85 / peak
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s * gain)) * 32767)) for s in samples))


def env(t: float, a: float, d: float) -> float:
    if t < 0:
        return 0.0
    if t < a:
        return t / a
    return math.exp(-(t - a) / d)


def bell(duration: float = 8.0) -> list[float]:
    """Gentle bell: inharmonic partials, struck 4 times with soft decay."""
    out = [0.0] * int(SR * duration)
    strikes = [0.0, 2.0, 4.0, 6.0]
    partials = [(1.0, 1.0), (2.76, 0.5), (5.4, 0.25), (8.93, 0.12)]
    f0 = 523.25  # C5
    for s in strikes:
        for i in range(int(s * SR), len(out)):
            t = i / SR - s
            if t > 2.6:
                break
            v = 0.0
            for ratio, amp in partials:
                v += amp * math.sin(2 * math.pi * f0 * ratio * t) * math.exp(-t * (1.2 + ratio * 0.6))
            out[i] += v * env(t, 0.004, 1.0)
    return out


def rising(duration: float = 8.0) -> list[float]:
    """Rising synth: an arpeggio that climbs, soft saw + sine, gentle swell."""
    out = [0.0] * int(SR * duration)
    notes = [261.63, 329.63, 392.0, 523.25, 659.25, 783.99, 1046.5]
    step = duration / (len(notes) + 1)
    for n, f in enumerate(notes):
        start = n * step
        for i in range(int(start * SR), min(len(out), int((start + step * 1.8) * SR))):
            t = i / SR - start
            e = env(t, 0.08, 0.6)
            saw = sum(math.sin(2 * math.pi * f * k * t) / k for k in range(1, 6)) * 0.25
            v = (math.sin(2 * math.pi * f * t) * 0.6 + saw) * e
            swell = min(1.0, 0.35 + (i / len(out)))
            out[i] += v * swell
    return out


def birds(duration: float = 8.0, seed: int = 7) -> list[float]:
    """Birds: short frequency-swept chirps in small clusters, with a light pad."""
    rnd = random.Random(seed)
    out = [0.0] * int(SR * duration)
    t_pos = 0.3
    while t_pos < duration - 0.5:
        n = rnd.randint(2, 5)
        base = rnd.uniform(2200, 4200)
        for _ in range(n):
            length = rnd.uniform(0.06, 0.16)
            f1, f2 = base * rnd.uniform(0.9, 1.1), base * rnd.uniform(1.1, 1.5)
            start = int(t_pos * SR)
            for i in range(start, min(len(out), start + int(length * SR))):
                t = (i - start) / SR
                f = f1 + (f2 - f1) * (t / length)
                out[i] += math.sin(2 * math.pi * f * t) * env(t, 0.01, length / 2) * 0.5
            t_pos += length + rnd.uniform(0.03, 0.12)
        t_pos += rnd.uniform(0.6, 1.4)
    for i in range(len(out)):
        t = i / SR
        out[i] += 0.04 * math.sin(2 * math.pi * 196 * t) * (0.7 + 0.3 * math.sin(2 * math.pi * 0.2 * t))
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in (("gentle_bell", bell), ("rising_synth", rising), ("birds", birds)):
        wav = OUT / f"{name}.wav"
        write_wav(wav, fn())
        ogg = OUT / f"{name}.ogg"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "libvorbis", "-q:a", "3", str(ogg)], check=True)
        wav.unlink()
        print(f"{ogg} {ogg.stat().st_size // 1024} KB")


if __name__ == "__main__":
    sys.exit(main())
