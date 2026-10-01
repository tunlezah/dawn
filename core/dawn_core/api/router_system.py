from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..context import DawnContext
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
