"""Keyboard -> input events for the simulator (raw terminal mode)."""

from __future__ import annotations

import asyncio
import os
import sys
import termios
import tty

from .simstate import STATE

HELP = """keys:  +/- volume   Enter encoder push   n encoder hold   Space big button   S big button hold (shutdown)
       t touch face   1-9 lux presets   g GPS fix   d DAB sync   u SDR plug   w network   q quit"""

LUX_PRESETS = {"1": 0.5, "2": 3, "3": 10, "4": 30, "5": 100, "6": 300, "7": 1000, "8": 3000, "9": 10000}


async def keyboard_loop(stop: asyncio.Event) -> None:
    if not sys.stdin.isatty():
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    loop = asyncio.get_running_loop()
    print(HELP, flush=True)
    try:
        while not stop.is_set():
            ch = await loop.run_in_executor(None, _read1, fd)
            if ch is None:
                continue
            ev = None
            if ch in ("+", "="):
                ev = "encoder_cw"
            elif ch == "-":
                ev = "encoder_ccw"
            elif ch in ("\r", "\n"):
                ev = "encoder_push"
            elif ch == "n":
                ev = "encoder_long"
            elif ch == " ":
                ev = "button_short"
            elif ch == "S":
                ev = "button_long"
            elif ch == "t":
                ev = "touch"
            elif ch in LUX_PRESETS:
                STATE.lux = LUX_PRESETS[ch]
                STATE.note(f"lux -> {STATE.lux}")
            elif ch == "g":
                STATE.gps_fix = not STATE.gps_fix
                STATE.note(f"gps fix -> {STATE.gps_fix}")
            elif ch == "d":
                STATE.dab_sync = not STATE.dab_sync
                STATE.note(f"dab sync -> {STATE.dab_sync}")
            elif ch == "u":
                STATE.sdr_present = not STATE.sdr_present
                STATE.note(f"sdr present -> {STATE.sdr_present}")
            elif ch == "w":
                STATE.network_online = not STATE.network_online
                STATE.note(f"network -> {STATE.network_online}")
            elif ch == "q":
                stop.set()
            if ev:
                await STATE.input_queue.put({"event": ev, "value": None})
                STATE.note(f"key -> {ev}")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read1(fd: int) -> str | None:
    try:
        return os.read(fd, 1).decode(errors="ignore")
    except OSError:
        return None
