"""Audio, AirPlay, Bluetooth, display, inputs, weather and the system itself (services, power, heat, disk, config)."""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any

from ..context import DawnContext
from ..logging_setup import RING
from .checks import Check, ago
from .host import Host

UNITS = ["dawn-core", "dawn-dab", "dawn-timed", "dawn-face", "gpsd", "chrony", "shairport-sync", "nqptp", "bluetooth", "NetworkManager", "avahi-daemon"]


def _svc(ctx: DawnContext, module: str, cls: str) -> Any:
    import importlib

    try:
        return ctx.svc(getattr(importlib.import_module(module), cls))
    except (KeyError, ImportError, AttributeError):
        return None


async def collect(ctx: DawnContext, host: Host) -> dict[str, Any]:
    st = ctx.store.state
    cfg = ctx.config
    units = await host.units(UNITS)
    audio = _svc(ctx, "dawn_core.audio.service", "AudioService")
    inputs = _svc(ctx, "dawn_core.inputs.service", "InputService")
    weather = _svc(ctx, "dawn_core.weather.service", "WeatherService")
    sysinfo = _svc(ctx, "dawn_core.system.sysinfo", "SysInfoService")
    data = await host.disk(str(ctx.data_dir))
    root = await host.disk("/")
    hb = st.system.heartbeat_at
    hb_age = time.time() - datetime.fromisoformat(hb).timestamp() if hb else None
    recent = [r for r in RING.records if r["level"] in ("ERROR", "CRITICAL")]
    bl = cfg.display.backlight.sysfs_path or (sysinfo.hw.backlight_sysfs if sysinfo else None)
    conns = ctx.ws_hub.connections() if ctx.ws_hub else []
    last_input = inputs.last_event if inputs else None
    return {
        "units": units,
        "audio": {
            "backend": st.audio.backend, "sink": st.audio.sink.model_dump() if st.audio.sink else None, "sinks": [s.model_dump() for s in st.audio.sinks],
            "pinned": cfg.audio.pinned_sink, "volume": st.audio.volume, "muted": st.audio.muted, "active_source": st.audio.active_source,
            "flowing": st.audio.audio_flowing, "eq_present": bool(audio and audio.backend.eq_present), "ceiling": cfg.audio.output_ceiling_percent,
            "max_volume": cfg.audio.max_volume, "sources": [s.model_dump() for s in st.audio.sources],
        },
        "airplay": {"enabled": cfg.airplay.enabled, "available": st.airplay.available, "name": st.airplay.name, "active": st.airplay.active,
                    "pipe": await host.exists(cfg.airplay.metadata_pipe)},
        "bluetooth": {"enabled": cfg.bluetooth.enabled, "available": st.bluetooth.available, "powered": st.bluetooth.powered,
                      "paired": sum(1 for d in st.bluetooth.devices if d.paired), "connected": st.bluetooth.connected},
        "display": {
            "panel": st.system.panel, "backlight": st.display.backlight_driver, "brightness": st.display.brightness, "sysfs": bl,
            "sysfs_writable": bool(bl and os.access(os.path.join(bl, "brightness"), os.W_OK)), "sensor": st.display.sensor,
            "sensor_found": st.display.sensor_found, "lux": st.display.lux, "mode": st.display.mode, "night": st.display.night,
            "face_clients": [c for c in conns if c.get("role") == "face"], "clients": conns, "face_mode": st.face.mode,
        },
        "inputs": {"encoder": cfg.inputs.encoder.enabled, "button": cfg.inputs.big_button.enabled,
                   "backend": inputs.backend.name if inputs and inputs.backend else None,
                   "last": {"event": last_input[0], "age_s": round(time.time() - last_input[1], 1), "via": last_input[2]} if last_input else None},
        "weather": {"enabled": cfg.weather.enabled and cfg.weather.provider != "none", "available": st.weather.available, "stale": st.weather.stale,
                    "fetched_at": st.weather.fetched_at, "error": weather.last_error if weather else None,
                    "last_attempt": weather.last_attempt if weather else None},
        "system": {
            "model": st.system.model, "version": st.system.version, "git_rev": st.system.git_rev, "uptime_s": st.system.uptime_s,
            "load1": st.system.load1, "mem_used_percent": st.system.mem_used_percent, "cpu_temp_c": st.system.cpu_temp_c,
            "throttled": st.system.throttled, "throttle_flags": st.system.throttle_flags, "heartbeat_age_s": round(hb_age, 1) if hb_age is not None else None,
            "config_error": st.system.config_error, "failed_services": dict(ctx.registry.failed), "sim": ctx.sim,
            "disk_data": {"path": str(ctx.data_dir), "total": data[0], "free": data[1]} if data else None,
            "disk_root": {"total": root[0], "free": root[1]} if root else None,
            "errors": recent[-8:], "error_count": len(recent), "update_running": st.system.update_running,
        },
    }


def checks(f: dict[str, Any]) -> list[Check]:
    c: list[Check] = []

    def add(area: str, id_: str, title: str, status: str, detail: str, hint: str | None = None, actions: list[str] | None = None) -> None:
        c.append(Check(f"{area}.{id_}", area, title, status, detail, hint, actions or []))  # type: ignore[arg-type]

    u = f["units"]
    # ---- audio
    a = f["audio"]
    if not a["sinks"]:
        add("audio", "sinks", "Audio outputs", "fail", f"No audio outputs found ({a['backend']} backend).",
            "With the Amp SHIM, config.txt needs dtoverlay=hifiberry-dac and gpio=25=op,dh; `aplay -l` should list snd_rpi_hifiberry_dac.")
    else:
        sink = a["sink"]
        add("audio", "sinks", "Audio output", "ok" if sink else "warn",
            f"{sink['description']} ({sink['kind']}) of {len(a['sinks'])} outputs, {a['backend']}." if sink else "Outputs found but none selected.")
        if a["pinned"] and not any(a["pinned"] in (s["name"], s["id"], s["description"], s["kind"]) for s in a["sinks"]):
            add("audio", "pinned", "Pinned output", "warn", f"audio.pinned_sink is {a['pinned']!r} but no such output is present; using the priority order.",
                "Plug the device in, or change the pin on the Audio page.")
    if a["backend"] == "pipewire" and not a["eq_present"]:
        add("audio", "eq", "Speaker protection filter", "warn", "The dawn_eq filter chain (mono mix, 110 Hz high-pass, tone controls) is not loaded.",
            "Re-run install.sh (--audio hifiberry for the SHIM); it installs /etc/pipewire/pipewire.conf.d/dawn-eq.conf.")
    if a["muted"] or a["volume"] == 0:
        add("audio", "volume", "Volume", "warn", "Muted." if a["muted"] else "Volume is 0.", "Alarms unmute and ramp up on their own; user playback stays silent.")
    else:
        add("audio", "volume", "Volume", "ok", f"{a['volume']} of {a['max_volume']}; volume 100 drives the output at {a['ceiling']}%.")
    if a["active_source"] != "none":
        add("audio", "flowing", "Audio flowing", "ok" if a["flowing"] else "fail",
            f"{a['active_source']} is playing and audio is reaching the output." if a["flowing"] else f"{a['active_source']} is selected but no audio is flowing.",
            None if a["flowing"] else "For DAB see DAB radio; otherwise check the output and play a test tone.", None if a["flowing"] else ["audio.test_tone"])
    else:
        add("audio", "test", "Speaker test", "info", "Nothing is playing.", "Play a short test tone to check the speaker.", ["audio.test_tone"])
    # ---- airplay
    ap = f["airplay"]
    if not ap["enabled"]:
        add("airplay", "enabled", "AirPlay", "off", "Disabled in the configuration.")
    else:
        for unit in ("shairport-sync", "nqptp"):
            if u.get(unit) not in ("active", "unknown"):
                add("airplay", unit, unit, "fail", f"{unit} is {u.get(unit)}.",
                    "AirPlay 2 needs both shairport-sync and nqptp (timing)." + (" Restart shairport-sync." if unit == "shairport-sync" else ""),
                    ["airplay.restart"] if unit == "shairport-sync" else [])
        if ap["available"]:
            add("airplay", "service", "AirPlay", "ok", f"Advertised as \"{ap['name']}\"" + (", a phone is connected." if ap["active"] else "."))
        else:
            add("airplay", "service", "AirPlay", "warn", "No metadata from shairport-sync yet.", f"The metadata pipe is {'present' if ap['pipe'] else 'missing'}.",
                ["airplay.restart"])
    # ---- bluetooth
    bt = f["bluetooth"]
    if not bt["enabled"]:
        add("bluetooth", "enabled", "Bluetooth", "off", "Disabled in the configuration.")
    elif u.get("bluetooth") not in ("active", "unknown"):
        add("bluetooth", "unit", "Bluetooth", "fail", f"bluetooth.service is {u.get('bluetooth')}.")
    elif not bt["available"] or not bt["powered"]:
        add("bluetooth", "adapter", "Bluetooth", "warn", "No powered adapter.", "Check `bluetoothctl show`; the Pi's adapter is hci0.")
    else:
        add("bluetooth", "adapter", "Bluetooth", "ok", f"Adapter on; {bt['paired']} paired device(s)" + (", one connected." if bt["connected"] else "."))
    # ---- display
    d = f["display"]
    if d["face_clients"]:
        add("display", "face", "Face", "ok", f"The face is connected (since {d['face_clients'][0]['connected_at'][11:16]}), showing {d['face_mode']}.")
    elif not f["system"]["sim"]:
        add("display", "face", "Face", "fail" if u.get("dawn-face") not in ("active", "unknown") else "warn",
            f"dawn-face is {u.get('dawn-face')} and the face is not connected.", "Restart the kiosk.", ["display.restart_face"])
    if d["backlight"] in ("none",):
        add("display", "backlight", "Backlight", "warn", "No backlight control: brightness cannot follow the room.",
            "DSI panels expose /sys/class/backlight; HDMI panels use the software dimmer (display.backlight.driver: overlay).")
    elif d["backlight"] == "sysfs" and not d["sysfs_writable"]:
        add("display", "backlight", "Backlight", "fail", f"{d['sysfs']}/brightness is not writable by the dawn user.",
            "The udev rule in deploy/udev/99-dawn.rules grants the video group write access; replug or reboot after installing it.")
    else:
        add("display", "backlight", "Backlight", "ok", f"{d['backlight']} at {d['brightness']}% ({d['mode']}).")
    if d["sensor_found"]:
        add("display", "sensor", "Light sensor", "ok", f"{d['sensor']}: {d['lux']} lx" + (", night palette" if d["night"] else "") + ".")
    else:
        add("display", "sensor", "Light sensor", "info", "Not found: brightness and the night palette follow sunrise and sunset.",
            "The VEML6030 sits on I2C bus 1 at 0x10; check `i2cdetect -y 1`. Sleep mode's dark and bright triggers need it.")
    # ---- inputs
    i = f["inputs"]
    if i["encoder"] or i["button"]:
        last = i["last"]
        add("inputs", "gpio", "Encoder and button", "ok" if i["backend"] else "warn",
            f"{i['backend'] or 'no'} backend; " + ("encoder" if i["encoder"] else "") + (" and " if i["encoder"] and i["button"] else "")
            + ("big button" if i["button"] else "") + " enabled" + (f"; last input {last['event']} {ago(last['age_s'])}." if last else "."))
    # ---- weather
    w = f["weather"]
    if not w["enabled"]:
        add("weather", "enabled", "Weather", "off", "Disabled in the configuration.")
    elif w["error"] and (not w["available"] or w["stale"]):
        add("weather", "fetch", "Weather", "fail" if not w["available"] else "warn", f"Last fetch failed: {w['error']}",
            "Needs the internet (see Network); the face keeps showing the cached forecast, marked stale.")
    elif w["available"]:
        add("weather", "fetch", "Weather", "warn" if w["stale"] else "ok", f"Forecast from {w['fetched_at'][11:16] if w['fetched_at'] else '?'}" + (" (stale)." if w["stale"] else "."))
    else:
        add("weather", "fetch", "Weather", "warn", "No forecast yet.")
    # ---- system
    s = f["system"]
    if s["config_error"]:
        add("system", "config", "Configuration", "fail", f"config.yaml was rejected: {s['config_error']}", "Fix the value under Settings → All options; the last good config is in use.")
    for name, err in s["failed_services"].items():
        add("system", f"svc.{name}", f"Service {name}", "fail", f"Failed to start: {err}", "See System → Logs.")
    th = s["throttled"]
    if th:
        now_bits = th & 0xF
        add("system", "power", "Power and throttling", "fail" if th & 0x1 else "warn", ", ".join(s["throttle_flags"]) + f" (0x{th:x}).",
            "Under-voltage: use the 5 V 3 A supply directly, no hub or long thin cable." if th & 0x10001 else
            "Throttling from heat: give the heatsink air." if now_bits or th & 0xE0000 else None)
    elif th == 0:
        add("system", "power", "Power and throttling", "ok", "No under-voltage or throttling since boot.")
    t = s["cpu_temp_c"]
    if t is not None:
        add("system", "temp", "CPU temperature", "ok" if t < 75 else "warn" if t < 82 else "fail", f"{t:.1f} °C.",
            None if t < 75 else "The Pi slows itself down from 80 °C; give the case ventilation.")
    if s["mem_used_percent"] is not None and s["mem_used_percent"] > 90:
        add("system", "memory", "Memory", "warn", f"{s['mem_used_percent']:.0f}% in use.")
    for key, label in (("disk_data", "Data storage"), ("disk_root", "System storage")):
        dk = s[key]
        if dk and dk["total"]:
            free_pct = dk["free"] / dk["total"] * 100
            status = "ok" if free_pct >= 10 and dk["free"] > 200e6 else "warn" if dk["free"] > 50e6 else "fail"
            add("system", key, label, status, f"{dk['free'] / 1e9:.1f} GB free of {dk['total'] / 1e9:.1f} GB ({free_pct:.0f}%).",
                None if status == "ok" else "Free space: old journal logs (`journalctl --vacuum-size=50M`) or files under /var/lib/dawn/media.")
    hb = s["heartbeat_age_s"]
    if hb is not None and hb > 30:
        add("system", "heartbeat", "Watchdog heartbeat", "fail", f"Last heartbeat {ago(hb)}.", "dawn-core is stalling; systemd restarts it after 60 s.")
    down = [n for n, v in f["units"].items() if v in ("failed",)]
    if down:
        add("system", "units", "Services", "fail", "Failed: " + ", ".join(down) + ".", "See each area above, and System → Logs.")
    if s["error_count"]:
        last = s["errors"][-1]
        add("system", "errors", "Recent errors in the log", "warn", f"{s['error_count']} error(s) in the recent log; last at {last['ts'][11:]}: {last['logger']}: {last['msg'][:160]}",
            "The full log is under System → Logs.")
    add("system", "version", "Software", "info", f"dawn-core {s['version']}" + (f" ({s['git_rev']})" if s["git_rev"] else "") + f" on {s['model']}, up {ago(s['uptime_s']).replace(' ago', '')}.")
    return c
