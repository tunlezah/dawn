"""Safe shutdown / reboot."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from ..context import DawnContext

log = logging.getLogger("dawn.power")


async def _run(ctx: DawnContext, *args: str) -> int:
    cmd = [ctx.config.system.sudo_binary, "-n", *args]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode:
        log.error("%s failed: %s", " ".join(args), err.decode(errors="ignore").strip())
    return proc.returncode or 0


WAKEALARM = Path("/sys/class/rtc/rtc0/wakealarm")
WAKE_EARLY = timedelta(minutes=10)  # a Pi 5 switches itself on this long before the alarm


class ShutdownRefused(Exception):
    """Not now (an alarm rings), or not without a confirmation (an alarm is due): 409."""


def pending_alarm(ctx: DawnContext, within: timedelta = timedelta(hours=24)) -> tuple[str, datetime] | None:
    """The next alarm (or nap) due within `within`: what a shutdown now would silence."""
    st = ctx.store.state
    now = ctx.store.now()
    found: list[tuple[str, datetime]] = []
    if st.alarms.next is not None:
        try:
            found.append((st.alarms.next.label or "next", datetime.fromisoformat(st.alarms.next.at)))
        except ValueError:
            pass
    if st.timers.nap is not None:
        try:
            found.append(("nap", datetime.fromisoformat(st.timers.nap.ends_at)))
        except ValueError:
            pass
    found = [(label, at) for label, at in found if now <= at <= now + within]
    return min(found, key=lambda x: x[1]) if found else None


def can_wake(ctx: DawnContext) -> bool:
    """A Pi 5 with its RTC can switch itself on at a set time; a Pi 4 cannot."""
    try:
        from .sysinfo import SysInfoService

        return ctx.svc(SysInfoService).hw.pi_generation == 5 and WAKEALARM.exists()
    except (ImportError, KeyError):
        return False


def _set_wakealarm(at: datetime) -> bool:
    """Write the RTC wake alarm (writable by group dawn through a tmpfiles rule). 0 first: a set alarm must be cleared."""
    try:
        WAKEALARM.write_text("0")
        WAKEALARM.write_text(str(int(at.timestamp())))
        return True
    except OSError as e:
        log.error("cannot set the RTC wake alarm: %s", e)
        return False


async def shutdown(ctx: DawnContext, *, confirmed: bool = True, source: str = "button") -> None:
    """Power off. Never while an alarm rings; with an alarm or nap due within a day the API needs `confirmed` (the
    hold-to-shutdown on the face shows the warning in its countdown and counts as confirmed). On a Pi 5 the RTC is set to
    switch the clock on 10 minutes before that alarm."""
    from ..face.service import FaceService

    if ctx.store.state.alarms.ringing is not None:
        raise ShutdownRefused(f"{ctx.store.state.alarms.ringing.label} is ringing; stop it first")
    pending = pending_alarm(ctx)
    if pending and not confirmed:
        label, at = pending
        raise ShutdownRefused(f"the {label} alarm at {at.strftime('%H:%M')} would not ring while Dawn is off")
    ctx.db.log_event("shutdown", source=source, pending=pending[1].isoformat() if pending else None)
    if not ctx.config.system.allow_shutdown:
        log.warning("shutdown requested but system.allow_shutdown is false")
        ctx.svc(FaceService).toast("Shutdown disabled", "system.allow_shutdown is false", "warning", 6)
        return
    wake_note = ""
    if pending and can_wake(ctx) and not ctx.sim and _set_wakealarm(pending[1] - WAKE_EARLY):
        wake_note = f"Dawn will switch itself on at {(pending[1] - WAKE_EARLY).strftime('%H:%M')}."
        log.info("RTC wake alarm set for %s", (pending[1] - WAKE_EARLY).isoformat(timespec="minutes"))
    if ctx.sim:
        log.info("sim: would power off now")
        ctx.svc(FaceService).toast("Shutting down", "(simulated)", "info", 6)
        ctx.svc(FaceService).set_shutdown_countdown(None)
        return
    ctx.svc(FaceService).toast("Shutting down", ("It is safe to unplug when the screen goes dark. " + wake_note).strip(), "info", None)
    await _run(ctx, "systemctl", "poweroff")


async def reboot(ctx: DawnContext) -> None:
    from ..face.service import FaceService

    ctx.db.log_event("reboot", source="api")
    if ctx.sim:
        ctx.svc(FaceService).toast("Rebooting", "(simulated)", "info", 6)
        return
    ctx.svc(FaceService).toast("Rebooting", "", "info", None)
    await _run(ctx, "systemctl", "reboot")
