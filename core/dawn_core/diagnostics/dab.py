"""DAB radio: stick -> kernel driver -> welle-cli -> ensemble sync -> signal -> the station playing -> scan data."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any

from ..context import DawnContext
from ..dab.service import DabService, snr_to_signal
from ..dab.welle import CHANNEL_MHZ, welle_args
from .checks import Check
from .host import Host

RTL_IDS = {"0bda:2838": "RTL2838", "0bda:2832": "RTL2832U"}
# welle's SNR estimate: above ~12 dB reception is solid, below ~8 dB audio breaks up
SNR_GOOD, SNR_POOR = 12.0, 8.0
ANTENNA_HINT = ("Check the antenna is screwed on and both elements are extended (about 37 cm each for Band III), "
                "mounted high and vertical near a window, and away from the Pi, the screen and the speaker leads "
                "(the amp's output is unfiltered PWM). The short USB extension keeps the stick away from the Pi.")


# welle-cli's /spectrum: 2048 bins of 1 kHz (2.048 MS/s), centred on the channel; the ensemble fills about ±768 kHz
SPUR_DB = 30.0  # a single carrier this far above the ensemble around it


def inband_spurs(spectrum: list[float] | None, half_width_khz: int = 700, window: int = 40, min_db: float = SPUR_DB) -> list[dict[str, float]]:
    """Narrow carriers inside the ensemble's block, strongest first: {"offset_khz", "db"} (dB above the local median).

    A generic RTL2832U/R820T stick's own synthesiser can put one there (it lands somewhere else on every channel). Strong
    enough, it fills the null symbol welle-cli finds each frame by, so the ensemble cannot be decoded at all."""
    if not spectrum or len(spectrum) < 2 * (half_width_khz + window):
        return []
    mid = len(spectrum) // 2
    out = []
    for i in range(mid - half_width_khz, mid + half_width_khz + 1):
        if abs(i - mid) < 3:  # the DC spike at the centre is the stick's, and harmless
            continue
        around = sorted(spectrum[i - window:i] + spectrum[i + 1:i + 1 + window])
        above = spectrum[i] - around[len(around) // 2]
        if above >= min_db and spectrum[i] >= max(spectrum[i - 1], spectrum[i + 1]):
            out.append({"offset_khz": float(i - mid), "db": round(above, 1)})
    return sorted(out, key=lambda x: -x["db"])[:5]


def is_blog_v4(u: dict[str, str]) -> bool:
    return "blog" in (u.get("manufacturer", "") + " " + u.get("product", "")).lower()


def _known_sids(dab: DabService) -> set[str]:
    return {s.sid for s in dab.services()}


async def collect(ctx: DawnContext, host: Host) -> dict[str, Any]:
    cfg = ctx.config.dab
    st = ctx.store.state
    dab: DabService = ctx.svc(DabService)
    usb = await host.usb_devices()
    sticks = [u for u in usb if f"{u['vid']}:{u['pid']}" in RTL_IDS]
    modules = await host.read("/proc/modules")
    dvb_loaded = None if modules is None else any(line.split()[0] == "dvb_usb_rtl28xxu" for line in modules.splitlines() if line.strip())
    unit = (await host.units([cfg.service_name]))[cfg.service_name]
    m = await dab.fresh_mux() if cfg.enabled else None
    expected, notes = welle_args(list(cfg.welle_args), cfg.gain)
    services = []
    mux = None
    spectrum = await dab.client.plot("spectrum") if m is not None else None
    if m is not None:
        now_ms = time.time() * 1000
        mux = {
            "channel": m.channel, "mhz": CHANNEL_MHZ.get((m.channel or "").upper()), "ensemble": m.ensemble_label, "ensemble_id": m.ensemble_id,
            "sync": m.sync, "snr": m.snr, "signal": snr_to_signal(m.snr), "freq_correction_hz": m.freq_correction_hz,
            "fic_crc_errors": m.fic_crc_errors, "fic_errors_per_min": dab.fic_errors_per_min,
            "fct0_age_s": round((now_ms - m.last_fct0_ms) / 1000, 1) if m.last_fct0_ms else None,
            "gain_db": m.gain_db, "spurs": inband_spurs(spectrum), "hardware": m.hardware, "software": m.software, "tii": m.tii, "cir_peaks": m.cir_peaks, "utc_time": m.utc_time,
        }
        services = [
            {"sid": s.sid, "label": s.label, "codec": s.codec, "bitrate": s.bitrate, "protection": s.protection, "subchannel": s.subchannel_id,
             "decoding": s.decoding, "audio_format": s.audio_format, "samplerate": s.samplerate, "audio_level": s.audio_level,
             "frame_errors": s.frame_errors, "rs_errors": s.rs_errors, "aac_errors": s.aac_errors, "rates": dab.error_rates.get(s.sid)}
            for s in m.services
        ]
    # database reads run in a thread: the antenna page asks for these facts once a second
    known = await asyncio.to_thread(_known_sids, dab)
    ensembles = await asyncio.to_thread(dab.ensembles)
    presets_unknown = [p.label for p in st.presets if p.source.startswith("dab:") and p.source[4:] not in known]
    alarms_unknown = [a.label for a in st.alarms.items if a.enabled and a.source.startswith("dab:") and a.source[4:] not in known]
    np = st.now_playing
    since = ctx.store.now() - timedelta(hours=24)
    events = await asyncio.to_thread(ctx.db.events_since, since, ["dab_restart", "ring_fallback"])
    return {
        "enabled": cfg.enabled,
        "sdr": {"present": st.system.sdr_present, "tuner": st.system.sdr_tuner, "usb": sticks},
        "dvb_driver_loaded": dvb_loaded,
        "unit": unit, "service_name": cfg.service_name, "reachable": m is not None, "welle_url": cfg.welle_url,
        "cmdline": await host.process_cmdline("welle-cli"), "expected_args": expected, "arg_notes": notes,
        "gain_config": cfg.gain,
        "mux": mux, "services": services,
        "playing": {"sid": np.station_sid, "label": np.station, "flowing": st.audio.audio_flowing} if np.source == "dab" else None,
        "stations": len(known), "ensembles": ensembles, "last_scan_at": st.dab.last_scan_at, "scanning": st.dab.scan.running,
        "presets_unknown": presets_unknown, "alarms_unknown": alarms_unknown,
        "restarts_24h": [e["at"] for e in events if e["kind"] == "dab_restart" and e.get("reason") not in ("manual", "supervisor")],
        "supervisor_restarts_24h": [e["at"] for e in events if e["kind"] == "dab_restart" and e.get("reason") == "supervisor"],
        # a DAB alarm that left the radio for the chime (not the chime's own climb to the backup tone)
        "fallbacks_24h": [e for e in events if e["kind"] == "ring_fallback" and str(e.get("source", "")).startswith("dab:") and e.get("from_tier", "source") == "source"],
        "messages": [{"at": at, "text": line.replace("\n.", ".").strip()} for at, line in list(dab.messages)[-60:]],
    }


def checks(f: dict[str, Any]) -> list[Check]:
    c: list[Check] = []

    def add(id_: str, title: str, status: str, detail: str, hint: str | None = None, actions: list[str] | None = None) -> None:
        c.append(Check(f"dab.{id_}", "dab", title, status, detail, hint, actions or []))  # type: ignore[arg-type]

    if not f["enabled"]:
        add("enabled", "DAB radio", "off", "Disabled in the configuration (dab.enabled).")
        return c
    sdr = f["sdr"]
    if sdr["present"]:
        add("sdr", "RTL-SDR stick", "ok", f"Detected: {sdr['tuner'] or 'RTL2832U'}")
        generic = [u for u in sdr["usb"] if not is_blog_v4(u)]
        if generic and len(generic) == len(sdr["usb"]):
            u = generic[0]
            add("sdr_model", "Stick model", "info", f"A generic TV stick ({u.get('manufacturer') or '?'} {u.get('product') or 'RTL2832U'}), not the RTL-SDR Blog V4.",
                "Generic sticks have no temperature-compensated crystal (the tuning drifts as it warms), little filtering ahead of the tuner "
                "and an unshielded case, and their R820T can put its own carriers inside an ensemble. A dedicated DAB radio is far more "
                "tolerant. If reception is unreliable, the Blog V4 on a short USB extension is the first thing to change.")
    else:
        add("sdr", "RTL-SDR stick", "fail", "No RTL-SDR stick on USB.",
            "Check the stick is plugged in (via its short extension), try another USB port, and look for under-voltage under System: "
            "the stick draws about 0.3 A.")
    if f["dvb_driver_loaded"]:
        add("dvb", "Kernel TV driver", "fail", "dvb_usb_rtl28xxu is loaded and grabs the stick before welle-cli can.",
            "The installer blacklists it in /etc/modprobe.d/; re-run install.sh, or unplug and replug the stick, then restart the decoder.",
            ["dab.restart"])
    unit, reachable = f["unit"], f["reachable"]
    if unit == "active" and reachable:
        add("decoder", "DAB decoder (welle-cli)", "ok", f"{f['service_name']} is running and answering on {f['welle_url']}.")
    elif unit in ("active", "unknown") and not reachable:
        add("decoder", "DAB decoder (welle-cli)", "fail" if unit == "active" else "warn",
            f"{f['service_name']} is {unit} but {f['welle_url']}/mux.json does not answer.",
            "welle-cli may be starting or stuck; restart it. If it keeps failing, see its log under System → Logs.", ["dab.restart"])
    else:
        add("decoder", "DAB decoder (welle-cli)", "fail", f"{f['service_name']} is {unit}.",
            "With no stick plugged in welle-cli exits and systemd keeps retrying; otherwise check its log under System → Logs.", ["dab.restart"])
    if f["arg_notes"]:
        add("args", "Decoder options", "warn", "dab.welle_args was corrected: " + "; ".join(f["arg_notes"]) + ".",
            "Edit dab.welle_args under Settings → All options to remove the warning.")
    cmd, want = f["cmdline"] or "", " ".join(f["expected_args"])
    if cmd and want and want not in cmd:
        add("args_pending", "Decoder options", "info", f"Running: {cmd}. The configuration now asks for: {want}.",
            "Restart the decoder to apply the new options.", ["dab.restart"])
    mux = f["mux"]
    if mux is None:
        return c
    ch = mux["channel"] or "?"
    where = f"{ch}" + (f" ({mux['mhz']:.3f} MHz)" if mux.get("mhz") else "")
    if mux["sync"]:
        add("sync", "Ensemble", "ok", f"{mux['ensemble'] or 'Ensemble'} on {where}, {len(f['services'])} services.")
    else:
        add("sync", "Ensemble", "fail", f"No DAB ensemble decoded on {where}.",
            f"Nothing receivable on this channel. {ANTENNA_HINT} Run a scan to find the channels in your area.", ["dab.scan", "dab.restart"])
    snr = mux["snr"]
    if snr is not None and mux["sync"]:
        if snr >= SNR_GOOD:
            add("snr", "Signal quality", "ok", f"SNR {snr:.1f} dB ({mux['signal']}%).")
        elif snr >= SNR_POOR:
            add("snr", "Signal quality", "warn", f"SNR {snr:.1f} dB: marginal, expect the odd dropout.", ANTENNA_HINT)
        else:
            add("snr", "Signal quality", "fail", f"SNR {snr:.1f} dB: too weak for clean audio.", ANTENNA_HINT)
    fic = mux["fic_errors_per_min"]
    if fic is not None and mux["sync"]:
        if fic < 5:
            add("fic", "Data channel (FIC) errors", "ok", f"{fic:g} CRC errors per minute.")
        else:
            add("fic", "Data channel (FIC) errors", "warn" if fic < 75 else "fail",
                f"{fic:g} CRC errors per minute (about 7500 blocks a minute arrive).",
                "Errors in the ensemble's data channel come from a weak or reflected signal. " + ANTENNA_HINT)
    fc = mux["freq_correction_hz"]
    ppm = abs(fc) / mux["mhz"] if fc is not None and mux.get("mhz") else None
    if fc is not None and mux["sync"] and (abs(fc) > 5000 or (ppm is not None and ppm > 10)):
        add("freq", "Tuner frequency offset", "warn", f"welle corrects {fc / 1000:+.1f} kHz" + (f" ({ppm:.0f} ppm)." if ppm is not None else "."),
            "A large offset suggests a stick without a TCXO, or one still warming up; it also drifts as the stick warms, and every "
            "resync has to find it again. The RTL-SDR Blog V4 should be within about ±0.5 kHz (2 ppm).")
    spurs = mux.get("spurs") or []
    if spurs:
        top = spurs[0]
        where = f"{mux['mhz'] + top['offset_khz'] / 1000:.3f} MHz" if mux.get("mhz") else f"{top['offset_khz']:+.0f} kHz"
        add("spur", "Interfering carrier", "warn" if mux["sync"] else "fail",
            f"A single carrier {top['db']:.0f} dB above the ensemble at {where} ({top['offset_khz']:+.0f} kHz from the centre).",
            "It stops welle-cli finding each frame's null symbol. Retune to another channel and back: if it lands on a different "
            "frequency, the stick makes it itself (an R820T synthesiser spur) and a better stick (RTL-SDR Blog V4) is the cure; if it "
            "stays put, something nearby transmits it (a switch-mode supply, a screen, LED lighting).")
    if f["playing"]:
        p = f["playing"]
        svc = next((s for s in f["services"] if s["sid"] == p["sid"]), None)
        rates = (svc or {}).get("rates") or {}
        errs = sum(v for k, v in rates.items() if k in ("frame", "rs", "aac"))
        detail = f"{p['label'] or p['sid']}" + (f": {svc['audio_format']}" if svc and svc.get("audio_format") else "")
        if not p["flowing"]:
            add("audio", "Station playing", "fail", f"{detail}, but no audio is coming through.",
                "Without sync welle-cli has nothing to stream; with sync, check Audio. An alarm falls back to the chime after 15 s.",
                ["dab.restart"])
        elif rates and errs > 0:
            add("audio", "Station playing", "warn" if errs < 30 else "fail",
                f"{detail}. Per minute: {rates.get('frame', 0):g} frame, {rates.get('rs', 0):g} Reed-Solomon, {rates.get('aac', 0):g} AAC errors.",
                "Decoding errors are heard as glitches and dropouts. " + ANTENNA_HINT)
        else:
            add("audio", "Station playing", "ok", f"{detail}, decoding without errors." if rates else f"{detail}.")
    if f["scanning"]:
        add("stations", "Station list", "info", "A scan is running.")
    elif not f["stations"]:
        add("stations", "Station list", "warn", "No stations stored yet.", "Run a scan (Radio → Scan or below).", ["dab.scan"])
    else:
        when = (f["last_scan_at"] or "")[:16].replace("T", " ") or "unknown"
        add("stations", "Station list", "ok", f"{f['stations']} stations in {len(f['ensembles'])} ensembles; last scan {when}.")
    if f["alarms_unknown"]:
        add("alarm_sources", "Alarm stations", "warn", "Not in the last scan: " + ", ".join(f["alarms_unknown"]) + ".",
            "These alarms will fall back to the chime. Rescan, or pick another station for them.", ["dab.scan"])
    if f["presets_unknown"]:
        add("presets", "Presets", "warn", "Not in the last scan: " + ", ".join(f["presets_unknown"]) + ".", "Rescan or remove them on the Radio page.")
    if f["fallbacks_24h"] or f["restarts_24h"] or f.get("supervisor_restarts_24h"):
        parts = []
        if f["fallbacks_24h"]:
            n = len(f["fallbacks_24h"])
            parts.append(f"{n} alarm{'s' if n > 1 else ''} fell back to the chime")
        if f["restarts_24h"]:
            n = len(f["restarts_24h"])
            parts.append(f"an alarm found DAB not working and restarted the decoder{'' if n == 1 else f' ({n} times)'}")
        if f.get("supervisor_restarts_24h"):
            n = len(f["supervisor_restarts_24h"])
            parts.append(f"the decoder stopped answering and was restarted{'' if n == 1 else f' {n} times'}")
        add("history", "Last 24 hours", "warn", parts[0][0].upper() + "; ".join(parts)[1:] + ".",
            "The DAB history graphs show the signal around those times (marked with thin vertical lines).")
    return c

