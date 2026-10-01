from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..context import DawnContext
from ..weather.service import WeatherService
from .deps import get_ctx

router = APIRouter(prefix="/api/weather", tags=["weather"])


@router.get("")
async def get_weather(ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    return ctx.store.state.weather.model_dump()


@router.post("/refresh")
async def refresh(ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    ok = await ctx.svc(WeatherService).refresh()
    return {"fetched": ok, "weather": ctx.store.state.weather.model_dump()}
