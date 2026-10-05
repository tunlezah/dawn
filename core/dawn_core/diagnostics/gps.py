"""GPS: USB receiver -> gpsd (or the serial reader) -> data flowing -> satellites and signal -> fix -> time."""

from __future__ import annotations

import math
import time
from typing import Any

from ..context import DawnContext
from ..timesync.service import TimeSourceService
from .checks import Check, ago
from .host import Host

UBLOX_VID = "1546"
SKY_HINT = ("The receiver needs a view of the sky: put it on a window sill on a USB extension, away from the Pi, its USB 3 ports "
            "and the screen ribbon, which radiate interference. After a long power-off the first fix can take up to 15 minutes.")


def km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


async def collect(ctx: DawnContext, host: Host) -> dict[str, Any]:
    g = ctx.config.time_sources.gps
    d = ctx.svc(TimeSourceService).gps_detail()
    usb = [u for u in await host.usb_devices() if u["vid"] == UBLOX_VID]
    paths = {p: await host.exists(p) for p in dict.fromkeys(["/dev/gps0", g.serial_device])}
    want_gpsd = g.source in ("auto", "gpsd", "sim") or d["source"] == "gpsd"
    unit = (await host.units(["gpsd"]))["gpsd"] if want_gpsd else None
    loc = ctx.config.location
    distance = round(km_between((loc.latitude, loc.longitude), (d["lat"], d["lon"])), 1) if d.get("has_fix") and d["lat"] is not None else None
    return {
        "enabled": g.enabled and g.source != "none", "configured_source": g.source, "detail": d, "usb": usb, "paths": paths,
        "unit": unit, "last_msg_age_s": round(time.time() - d["last_msg_at"], 1) if d.get("last_msg_at") else None,
        "toff_age_s": round(time.time() - d["toff_at"], 1) if d.get("toff_at") else None,
        "location_source": ctx.position()[2], "prefer_gps": loc.prefer_gps, "configured_distance_km": distance,
    }


def checks(f: dict[str, Any]) -> list[Check]:
    c: list[Check] = []

    def add(id_: str, title: str, status: str, detail: str, hint: str | None = None, actions: list[str] | None = None) -> None:
        c.append(Check(f"gps.{id_}", "gps", title, status, detail, hint, actions or []))  # type: ignore[arg-type]

    if not f["enabled"]:
        add("enabled", "GPS", "off", "Disabled in the configuration (time_sources.gps).")
        return c
    d = f["detail"]
    present = bool(f["usb"]) or any(f["paths"].values())
    if f["usb"]:
        u = f["usb"][0]
        add("receiver", "GPS receiver", "ok", f"{u['product'] or 'u-blox receiver'} on USB" + (", /dev/gps0 present" if f["paths"].get("/dev/gps0") else ""))
    elif present:
        add("receiver", "GPS receiver", "ok", "Device node present: " + ", ".join(p for p, ok in f["paths"].items() if ok))
    else:
        add("receiver", "GPS receiver", "fail", "No u-blox receiver on USB and no /dev/gps0.",
            "Plug the receiver in (directly or on its extension). /dev/gps0 comes from the udev rule for u-blox (USB vendor 1546).")
    if d["source"] == "gpsd":
        if f["unit"] not in (None, "active", "unknown"):
            add("gpsd", "gpsd", "fail", f"gpsd is {f['unit']}.", "Restart it; if it will not start, check `journalctl -u gpsd`.", ["gps.restart"])
        elif not d["connected"]:
            add("gpsd", "gpsd", "fail", f"Cannot talk to gpsd: {d.get('connect_error') or 'not connected'}.", "Restart gpsd.", ["gps.restart"])
        else:
            add("gpsd", "gpsd", "ok", f"gpsd {d.get('gpsd_version') or ''} connected".strip() + ".")
        if d["connected"]:
            if d.get("driver"):
                add("device", "gpsd sees the receiver", "ok", f"{d['driver']} on {d.get('device') or '?'}" + (f" at {d['bps']} bps" if d.get("bps") else "") + ".")
            else:
                add("device", "gpsd sees the receiver", "fail" if present else "warn", "gpsd is running but reports no device.",
                    "gpsd watches DEVICES=/dev/gps0 (/etc/default/gpsd). Replug the receiver or restart gpsd.", ["gps.restart"])
    elif d["source"] == "serial":
        if d["connected"]:
            add("serial", "Serial reader", "ok", f"Reading NMEA directly from {d.get('device')} (gpsd not running).")
        else:
            add("serial", "Serial reader", "fail", f"Cannot open {d.get('device')}: {d.get('connect_error') or 'not connected'}.",
                "The dawn user needs the dialout group; check the device path in time_sources.gps.serial_device.")
    age = f["last_msg_age_s"]
    talking = age is not None and age < 5
    if d["connected"] or d["source"] == "serial":
        if talking:
            add("data", "Receiver reports", "ok", f"Last report {ago(age)}.")
        else:
            add("data", "Receiver reports", "fail", f"No reports from the receiver ({'last ' + ago(age) if age is not None else 'none yet'}).",
                "The receiver is not sending anything: replug it, then restart gpsd." if present else "Plug the receiver in.", ["gps.restart"] if present else [])
    if not (present and talking):
        return c  # no receiver or no data: the signal and fix checks would only repeat the cause above
    sats = d.get("satellites") or []
    tracked = [s for s in sats if s.get("ss")]
    if sats or d.get("sats_seen"):
        best = max((s["ss"] for s in tracked), default=0)
        strong = sum(1 for s in tracked if s["ss"] >= 30)
        used = sum(1 for s in sats if s.get("used"))
        avg = d.get("snr_used_avg")
        detail = f"{len(tracked)} of {len(sats)} satellites heard, best {best:.0f} dBHz; {used} used" + (f" (mean {avg:.0f} dBHz)." if avg else ".")
        if not tracked:
            add("signal", "Satellite signal", "fail", f"{len(sats)} satellites expected in view, none heard.", SKY_HINT)
        elif best < 30 or strong < 4:
            add("signal", "Satellite signal", "warn", detail + " Weak: a reliable fix wants four or more above 30 dBHz.", SKY_HINT)
        else:
            add("signal", "Satellite signal", "ok", detail)
    else:
        add("signal", "Satellite signal", "warn", "No satellite report yet.", SKY_HINT)
    mode = d.get("mode") or 0
    if d.get("has_fix") and mode >= 3:
        add("fix", "Position fix", "ok", f"3D fix, {d['sats_used']} satellites" + (f", ±{d['eph']:.0f} m" if d.get("eph") else "") + ".")
    elif d.get("has_fix"):
        add("fix", "Position fix", "warn", f"2D fix only, {d['sats_used']} satellites.", "A 3D fix needs four satellites; " + SKY_HINT)
    else:
        add("fix", "Position fix", "fail", "No fix.", SKY_HINT)
    hdop = d.get("hdop")
    if d.get("has_fix") and hdop is not None and hdop > 5:
        add("dop", "Satellite geometry", "warn", f"HDOP {hdop:.1f}: the satellites in use are bunched together.", SKY_HINT)
    toff = d.get("toff_ms")
    if toff is not None and (f["toff_age_s"] or 0) < 30:
        if abs(toff) < 1000:
            add("time", "GPS time vs system clock", "ok", f"The GPS time message arrives {-toff:.0f} ms after the second it stamps "
                "(chrony allows 200 ms for this with 'delay 0.2').")
        else:
            add("time", "GPS time vs system clock", "warn", f"The system clock is {-toff / 1000:+.1f} s from GPS time.",
                "chrony steps the clock in its first updates and slews afterwards; see Time sync for whether GPS is being used.")
    if f["location_source"] == "gps":
        add("location", "Location", "ok", "Using the GPS position for sunrise, sunset and weather.")
    elif f["prefer_gps"]:
        add("location", "Location", "info", "Using the configured location until there is a fix.")
    dist = f["configured_distance_km"]
    if dist is not None and dist > 50:
        add("location_config", "Configured location", "info", f"The configured location is {dist:.0f} km from the GPS position.",
            "It is only used without a fix; update it under Settings → Location.")
    return c
