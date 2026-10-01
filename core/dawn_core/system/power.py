"""Safe shutdown / reboot."""

from __future__ import annotations

import asyncio
import logging

from ..context import DawnContext

log = logging.getLogger("dawn.power")


async def _run(ctx: DawnContext, *args: str) -> int:
    cmd = [ctx.config.system.sudo_binary, "-n", *args]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode:
        log.error("%s failed: %s", " ".join(args), err.decode(errors="ignore").strip())
    return proc.returncode or 0


async def shutdown(ctx: DawnContext) -> None:
    from ..face.service import FaceService

    ctx.db.log_event("shutdown", source="button")
    if not ctx.config.system.allow_shutdown:
        log.warning("shutdown requested but system.allow_shutdown is false")
        ctx.svc(FaceService).toast("Shutdown disabled", "system.allow_shutdown is false", "warning", 6)
        return
    if ctx.sim:
        log.info("sim: would power off now")
        ctx.svc(FaceService).toast("Shutting down", "(simulated)", "info", 6)
        ctx.svc(FaceService).set_shutdown_countdown(None)
        return
    ctx.svc(FaceService).toast("Shutting down", "It is safe to unplug when the screen goes dark", "info", None)
    await _run(ctx, "systemctl", "poweroff")


async def reboot(ctx: DawnContext) -> None:
    from ..face.service import FaceService

    ctx.db.log_event("reboot", source="api")
    if ctx.sim:
        ctx.svc(FaceService).toast("Rebooting", "(simulated)", "info", 6)
        return
    ctx.svc(FaceService).toast("Rebooting", "", "info", None)
    await _run(ctx, "systemctl", "reboot")
