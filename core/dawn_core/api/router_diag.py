"""Diagnostics: checks, live readings, DAB plots, history and the one-tap fixes."""

from __future__ import annotations

import math
from datetime import timedelta
from statistics import median
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ..context import DawnContext
from ..dab.welle import CHANNEL_MHZ
from ..diagnostics import actions
from ..diagnostics.history import METRICS
from ..diagnostics.service import DiagnosticsService
from .deps import get_ctx

router = APIRouter(prefix="/api/diag", tags=["diagnostics"])
PLOTS = ("spectrum", "nullspectrum", "impulseresponse", "constellation")
# events worth marking on the history graphs
TIMELINE = ["dab_restart", "dab_scan", "ring_start", "ring_fallback", "ring_restored", "alarm_fire", "alarm_missed", "hotspot_start", "hotspot_stop",
            "diag_action", "light_wake", "sleep_mode", "reboot", "shutdown", "restore", "update_start", "wifi_connect"]


def diag(ctx: DawnContext = Depends(get_ctx)) -> DiagnosticsService:
    return ctx.svc(DiagnosticsService)


def downsample(values: list[float], bins: int, how: str = "max") -> list[float]:
    if len(values) <= bins:
        return [round(v, 2) for v in values]
    step = len(values) / bins
    out = []
    for i in range(bins):
        chunk = values[int(i * step) : max(int(i * step) + 1, int((i + 1) * step))]
        out.append(round(max(chunk) if how == "max" else sum(chunk) / len(chunk), 2))
    return out


def constellation_stats(phases: list[float]) -> dict[str, Any]:
    """DQPSK symbols sit at ±45° and ±135°: a histogram of the measured phases and the RMS distance from the nearest ideal point."""
    hist = [0] * 72
    err2 = 0.0
    for p in phases:
        p = (p + 180) % 360 - 180
        hist[int((p + 180) // 5) % 72] += 1
        err2 += ((p % 90) - 45) ** 2  # ideal points at 45° + k·90°
    n = len(phases)
    return {"histogram": hist, "phase_error_deg": round(math.sqrt(err2 / n), 1) if n else None, "points": [round(p, 1) for p in phases[:: max(1, n // 600)]]}


@router.get("")
async def report(d: DiagnosticsService = Depends(diag)) -> dict[str, Any]:
    return await d.run(max_age_s=3.0)


@router.post("/run")
async def run_now(d: DiagnosticsService = Depends(diag)) -> dict[str, Any]:
    return await d.run(max_age_s=0, fresh=True)


@router.get("/live/{area}")
async def live(area: str, d: DiagnosticsService = Depends(diag)) -> dict[str, Any]:
    if area not in ("dab", "gps"):
        raise HTTPException(404, "live readings exist for dab and gps")
    return await d.live(area)


@router.get("/dab/plot/{kind}")
async def dab_plot(kind: str, ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    from ..dab.service import DabService

    if kind not in PLOTS:
        raise HTTPException(404, f"plots: {', '.join(PLOTS)}")
    dab = ctx.svc(DabService)
    vals = await dab.client.plot(kind)
    if not vals:
        return {"kind": kind, "available": False}
    out: dict[str, Any] = {"kind": kind, "available": True, "n": len(vals)}
    if kind in ("spectrum", "nullspectrum"):
        db = [20 * math.log10(max(v, 1e-9)) for v in vals]
        mhz = CHANNEL_MHZ.get((dab.channel or "").upper())
        out.update(bins=downsample(db, 512), floor_db=round(median(db[: len(db) // 8] + db[-len(db) // 8 :]), 1),
                   center_mhz=mhz, span_mhz=2.048)  # welle samples at 2.048 MS/s, FFT-shifted around the channel centre
    elif kind == "impulseresponse":
        peak = max(range(len(vals)), key=lambda i: vals[i])
        out.update(bins=downsample(vals, 512), peak_index=peak, us_per_sample=round(1 / 2.048, 4))
    else:
        out.update(constellation_stats(vals))
    return out


@router.get("/history")
async def history(metrics: str = "", hours: float = 24, points: int = 240, ctx: DawnContext = Depends(get_ctx),
                  d: DiagnosticsService = Depends(diag)) -> dict[str, Any]:
    keys = [k for k in metrics.split(",") if k] or list(METRICS)
    hours = max(0.25, min(hours, ctx.config.diagnostics.history_days * 24))
    points = max(20, min(points, 1000))
    series = await d.series(keys, hours, points)
    events = ctx.db.events_since(ctx.store.now() - timedelta(hours=hours), TIMELINE)
    return {"hours": hours, "metrics": series, "events": events}


@router.get("/metrics")
async def metric_list() -> list[dict[str, str]]:
    return [{"key": k, "label": v[0], "unit": v[1]} for k, v in METRICS.items()]


@router.get("/events")
async def events(hours: float = 24, ctx: DawnContext = Depends(get_ctx)) -> list[dict[str, Any]]:
    return ctx.db.events_since(ctx.store.now() - timedelta(hours=max(0.25, min(hours, 24 * 60))))


@router.get("/actions")
async def action_list() -> list[dict[str, str]]:
    return [{"id": k, "title": v[0]} for k, v in actions.ACTIONS.items()]


@router.post("/action/{action}")
async def do_action(action: str, body: dict[str, Any] = Body(default={}), ctx: DawnContext = Depends(get_ctx),
                    d: DiagnosticsService = Depends(diag)) -> dict[str, Any]:
    if action not in actions.ACTIONS:
        raise HTTPException(404, f"unknown action {action!r}")
    ok, message = await actions.run(ctx, d.host, action, body.get("value"))
    d.invalidate()
    return {"ok": ok, "message": message}
