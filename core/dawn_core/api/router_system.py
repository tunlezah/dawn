from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from ..context import DawnContext
from ..logging_setup import RING
from ..system import backup, power, update
from .deps import get_ctx

router = APIRouter(prefix="/api/system", tags=["system"])
wifi_router = APIRouter(prefix="/api/wifi", tags=["wifi"])


@router.get("/backup")
async def get_backup(ctx: DawnContext = Depends(get_ctx)) -> JSONResponse:
    doc = backup.make_backup(ctx)
    name = f"dawn-backup-{ctx.store.now().strftime('%Y%m%d-%H%M')}.json"
    return JSONResponse(doc, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/restore")
async def post_restore(doc: dict[str, Any] = Body(...), ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        counts = await backup.restore_backup(ctx, doc)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, str(e)) from e
    return {"ok": True, "restored": counts}


@router.put("/hostname")
async def set_hostname(body: dict[str, str], ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    name = "".join(c for c in body.get("hostname", "") if c.isalnum() or c == "-")[:63]
    if not name:
        raise HTTPException(422, "invalid hostname")
    if not ctx.sim:
        import asyncio

        proc = await asyncio.create_subprocess_exec(ctx.config.system.sudo_binary, "-n", "/usr/local/bin/dawn-set-hostname", name)
        await proc.wait()
        if proc.returncode:
            raise HTTPException(500, "hostname change failed")
    await ctx.cfg_mgr.update({"general": {"hostname": name}})
    ctx.store.state.system.hostname = name
    ctx.store.touch()
    return {"hostname": name}


@router.post("/update")
async def start_update(ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    await update.run_update(ctx)
    return {"started": True}


@router.post("/update/check")
async def check_update(ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    return await update.check(ctx)


@wifi_router.get("/networks")
async def wifi_networks(ctx: DawnContext = Depends(get_ctx)) -> list[dict[str, Any]]:
    from ..net.service import NetworkService

    try:
        return await ctx.svc(NetworkService).wifi_networks()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e


@wifi_router.post("/connect")
async def wifi_connect(body: dict[str, str], ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    from ..net.service import NetworkService

    ssid = body.get("ssid", "")
    if not ssid:
        raise HTTPException(422, "ssid required")
    try:
        msg = await ctx.svc(NetworkService).wifi_connect(ssid, body.get("psk") or None)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)) from e
    return {"ok": True, "message": msg}


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
