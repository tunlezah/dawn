"""Routers added by later phases register here so app.py stays stable."""

from __future__ import annotations

import importlib
import logging

from fastapi import FastAPI

log = logging.getLogger("dawn.api")

PHASE_ROUTERS = [
    "dawn_core.api.router_audio",
    "dawn_core.api.router_input",
    "dawn_core.api.router_dab",
    "dawn_core.api.router_alarms",
    "dawn_core.api.router_timers",
    "dawn_core.api.router_display",
    "dawn_core.api.router_weather",
    "dawn_core.api.router_system",
    "dawn_core.api.router_bluetooth",
    "dawn_core.api.router_airplay",
    "dawn_core.api.router_media",
]


def include_phase_routers(app: FastAPI) -> None:
    for mod_name in PHASE_ROUTERS:
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError as e:
            if e.name == mod_name:
                continue
            raise
        app.include_router(mod.router)
        extra = getattr(mod, "wifi_router", None)
        if extra is not None:
            app.include_router(extra)
