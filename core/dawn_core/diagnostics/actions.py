"""One-tap fixes offered next to the checks. Each is a narrow, idempotent operation; the privileged ones are
exactly the lines in deploy/sudoers/dawn (no shell, no arguments from the browser reach a command line)."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from ..context import DawnContext
from .host import Host

log = logging.getLogger("dawn.diag.actions")

Result = tuple[bool, str]
Fn = Callable[[DawnContext, Host, Any], Awaitable[Result]]


async def _unit(host: Host, unit: str) -> Result:
    rc, out = await host.sudo("systemctl", "restart", unit)
    return (True, f"Restarted {unit}.") if rc == 0 else (False, f"Could not restart {unit}: {out.strip() or f'exit {rc}'}")


async def dab_restart(ctx: DawnContext, host: Host, _v: Any) -> Result:
    from ..dab.service import DabService, TunerBusy

    try:
        ok = await ctx.svc(DabService).restart_welle(force=True, reason="manual")
    except TunerBusy as e:
        return False, str(e)
    return (True, "Restarting the DAB decoder; it takes a few seconds to sync again.") if ok else (False, "It was restarted moments ago; wait a few seconds.")


async def dab_scan(ctx: DawnContext, host: Host, _v: Any) -> Result:
    from ..dab.service import DabService

    try:
        started = await ctx.svc(DabService).start_scan()
    except RuntimeError as e:
        return False, str(e)
    return (True, "Scanning Band III; it takes about a minute.") if started else (False, "A scan is already running.")


async def dab_retune(ctx: DawnContext, host: Host, value: Any) -> Result:
    from ..dab.service import DabService
    from ..dab.welle import CHANNEL_MHZ

    ch = str(value or "").upper()
    if ch not in CHANNEL_MHZ:
        return False, f"Unknown channel {value!r}."
    try:
        await ctx.svc(DabService).user_tune(ch)
    except RuntimeError as e:
        return False, str(e)
    return True, f"Tuned to {ch} ({CHANNEL_MHZ[ch]:.3f} MHz)."


async def dab_gain(ctx: DawnContext, host: Host, value: Any) -> Result:
    from ..dab.service import DabService, TunerBusy
    from ..dab.welle import R82XX_GAINS_DB, gain_index

    gain = None if value in (None, "", "auto") else float(value)
    if gain is not None and not 0 <= gain <= 60:
        return False, "Gain must be between 0 and 60 dB, or auto."
    await ctx.cfg_mgr.update({"dab": {"gain": gain}})
    try:
        restarted = await ctx.svc(DabService).restart_welle(force=True, reason="manual")
    except TunerBusy:
        restarted = False  # an alarm on the radio is ringing: the new gain applies at the next restart
    what = "Tuner gain set to automatic (AGC)." if gain is None else f"Tuner gain fixed at {R82XX_GAINS_DB[gain_index(gain)]} dB."
    return True, what + (" The decoder restarts." if restarted else " Restart the decoder in a few seconds to apply it.")


async def gps_restart(ctx: DawnContext, host: Host, _v: Any) -> Result:
    return await _unit(host, "gpsd")


async def chrony_restart(ctx: DawnContext, host: Host, _v: Any) -> Result:
    return await _unit(host, "chrony")


async def timed_restart(ctx: DawnContext, host: Host, _v: Any) -> Result:
    return await _unit(host, "dawn-timed")


async def chrony_burst(ctx: DawnContext, host: Host, _v: Any) -> Result:
    rc, out = await host.sudo(ctx.config.time_sources.chronyc_binary, "burst", "4/4")
    return (True, "chrony is polling every source now (4 quick samples each).") if rc == 0 else (False, f"chronyc burst failed: {out.strip()}")


async def airplay_restart(ctx: DawnContext, host: Host, _v: Any) -> Result:
    return await _unit(host, "shairport-sync")


async def face_restart(ctx: DawnContext, host: Host, _v: Any) -> Result:
    return await _unit(host, "dawn-face")


async def test_tone(ctx: DawnContext, host: Host, _v: Any) -> Result:
    from ..audio.service import AudioService

    audio = ctx.svc(AudioService)
    if audio.arbiter.slot("user") or audio.arbiter.slot("alarm") or audio.arbiter.slot("sleep"):
        return False, "Something is playing; stop it first."
    ref = f"chime:{ctx.config.audio.default_chime}"
    await audio.play(ref, remember=False)

    async def stop_later() -> None:
        await asyncio.sleep(4)
        slot = audio.arbiter.slot("user")
        if slot and slot.source.ref == ref:
            await audio.stop_level("user")

    asyncio.create_task(stop_later(), name="test-tone")
    return True, f"Playing the {ctx.config.audio.default_chime.replace('_', ' ')} chime for 4 s at volume {audio.volume}."


ACTIONS: dict[str, tuple[str, Fn]] = {
    "dab.restart": ("Restart the DAB decoder", dab_restart),
    "dab.scan": ("Scan for stations", dab_scan),
    "dab.retune": ("Tune to a channel", dab_retune),
    "dab.gain": ("Set tuner gain", dab_gain),
    "gps.restart": ("Restart gpsd", gps_restart),
    "time.restart_chrony": ("Restart chrony", chrony_restart),
    "time.burst": ("Poll all time sources now", chrony_burst),
    "time.restart_timed": ("Restart dawn-timed", timed_restart),
    "audio.test_tone": ("Play a test tone", test_tone),
    "airplay.restart": ("Restart AirPlay (shairport-sync)", airplay_restart),
    "display.restart_face": ("Restart the face kiosk", face_restart),
}


async def run(ctx: DawnContext, host: Host, action: str, value: Any = None) -> Result:
    if action not in ACTIONS or not re.fullmatch(r"[a-z_.]+", action):
        return False, f"Unknown action {action!r}."
    try:
        ok, msg = await ACTIONS[action][1](ctx, host, value)
    except Exception as e:  # noqa: BLE001
        log.exception("diagnostic action %s failed", action)
        ok, msg = False, f"{type(e).__name__}: {e}"
    ctx.db.log_event("diag_action", action=action, ok=ok, message=msg)
    return ok, msg
