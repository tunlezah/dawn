"""Build the service graph. One place lists every service and its order."""

from __future__ import annotations

import logging

from .context import DawnContext
from .system.sysinfo import SysInfoService

log = logging.getLogger("dawn.wiring")


def build_services(ctx: DawnContext) -> None:
    reg = ctx.registry
    reg.add(SysInfoService(ctx))
    # Later phases append services here (audio, inputs, dab, alarms, display, time, weather, airplay, bluetooth...).
    _optional(ctx, "dawn_core.audio.service", "AudioService")
    _optional(ctx, "dawn_core.dab.service", "DabService")
    _optional(ctx, "dawn_core.alarms.service", "AlarmService")
    _optional(ctx, "dawn_core.alarms.timers", "TimerService")
    _optional(ctx, "dawn_core.inputs.service", "InputService")
    _optional(ctx, "dawn_core.display.service", "DisplayService")
    _optional(ctx, "dawn_core.timesync.service", "TimeSourceService")
    _optional(ctx, "dawn_core.weather.service", "WeatherService")
    _optional(ctx, "dawn_core.net.service", "NetworkService")
    _optional(ctx, "dawn_core.airplay.service", "AirPlayService")
    _optional(ctx, "dawn_core.bluetooth.service", "BluetoothService")
    _optional(ctx, "dawn_core.diagnostics.service", "DiagnosticsService")
    _optional(ctx, "dawn_core.face.service", "FaceService")


def _optional(ctx: DawnContext, module: str, cls_name: str) -> None:
    import importlib

    try:
        mod = importlib.import_module(module)
    except ModuleNotFoundError as e:
        if e.name and module.startswith(e.name):
            return
        raise
    cls = getattr(mod, cls_name)
    ctx.registry.add(cls(ctx))
