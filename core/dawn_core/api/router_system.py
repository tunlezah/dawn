from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..context import DawnContext
from ..logging_setup import RING
from ..system import power
from .deps import get_ctx

router = APIRouter(prefix="/api/system", tags=["system"])


@router.post("/shutdown")
async def shutdown(ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    await power.shutdown(ctx)
    return {"ok": True}


@router.post("/reboot")
async def reboot(ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    await power.reboot(ctx)
    return {"ok": True}


@router.get("/logs")
async def logs(lines: int = 200, ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    """Recent log lines: journald on the Pi (dawn-core unit), the in-memory ring buffer otherwise."""
    lines = max(10, min(lines, ctx.config.system.log_tail_lines))
    if not ctx.sim:
        try:
            import asyncio

            proc = await asyncio.create_subprocess_exec("journalctl", "-u", "dawn-core", "-u", "dawn-dab", "-u", "dawn-timed", "-n", str(lines), "--no-pager", "-o", "short-iso", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), 5)
            if proc.returncode == 0 and out.strip():
                return {"source": "journald", "lines": out.decode(errors="ignore").splitlines()[-lines:]}
        except (TimeoutError, OSError):
            pass
    return {"source": "memory", "lines": [f"{r['ts']} {r['level']:<7} {r['logger']}: {r['msg']}" for r in RING.records[-lines:]]}
