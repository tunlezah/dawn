"""Time sync: chrony, and each path into it as a chain of steps.

  GPS      receiver fix -> gpsd writes NTP SHM 0 -> chrony's GPS refclock
  DAB      ensemble sync -> dawn-timed (FIG 0/10) -> NTP SHM 2 -> chrony's DAB refclock
  Network  online -> DNS for the pool -> NTP replies -> chrony's NTP sources

chrony's own `selectdata` says why a usable-looking source is not used (needs root on chrony 4.6, via sudo).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from ..context import DawnContext
from ..timesync.chrony import (
    SELECT_STATES,
    classify,
    parse_activity,
    parse_ntpdata,
    parse_selectdata,
    parse_sources,
    parse_sourcestats,
    parse_sysvipc_shm,
    parse_tracking,
    reach_count,
)
from .checks import Check, ago
from .host import Host

SOURCE_STATES = {"*": "selected", "+": "combined", "-": "not combined", "?": "unreachable", "x": "falseticker", "~": "too variable"}


def merge_sources(src: str | None, stats: str | None, sel: str | None, ntp: str | None, stale_after_s: int) -> list[dict[str, Any]]:
    by_stats = {s.name: s for s in parse_sourcestats(stats or "")}
    by_sel = {s.name: s for s in parse_selectdata(sel or "")}
    by_ntp = {n.remote: n for n in parse_ntpdata(ntp or "")}
    out = []
    for s in parse_sources(src or ""):
        se = by_sel.get(s.name)
        state = se.state if se else s.state
        label, why = SELECT_STATES.get(state, (SOURCE_STATES.get(s.state, s.state), ""))
        st, nd = by_stats.get(s.name), by_ntp.get(s.name)
        out.append({
            "name": s.name, "kind": classify(s.name), "refclock": s.mode == "#", "state": state, "state_label": label, "reason": why,
            "used": state in ("*", "+"), "selected": state == "*", "reach": s.reach, "reach_count": reach_count(s.reach),
            "last_rx_s": s.last_rx_s, "stratum": s.stratum, "offset_ms": round(s.offset_s * 1000, 3), "error_ms": round(s.error_s * 1000, 3),
            "live": s.reach != 0 and s.state not in ("?", "x") and s.last_rx_s <= stale_after_s,
            "stats": asdict(st) if st else None, "select": asdict(se) if se else None, "ntp": asdict(nd) if nd else None,
        })
    return out


def _timed_status(path: str) -> tuple[dict[str, Any] | None, float | None, str | None]:
    try:
        d = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None, None, f"{path} does not exist"
    except (OSError, ValueError) as e:
        return None, None, str(e)
    try:
        age = time.time() - datetime.fromisoformat(d["updated_at"]).timestamp()
    except (KeyError, ValueError, TypeError):
        age = None
    return d, age, None


async def collect(ctx: DawnContext, host: Host, gps: dict[str, Any], dab: dict[str, Any], online: bool) -> dict[str, Any]:
    cfg = ctx.config
    units = await host.units(["chrony", "dawn-timed"])
    trk, trk_err = await host.chronyc("tracking")
    src, src_err = await host.chronyc("sources")
    stats, _ = await host.chronyc("sourcestats")
    act, _ = await host.chronyc("activity")
    sel, sel_err = await host.chronyc("selectdata", privileged=True)
    ntp, ntp_err = await host.chronyc("ntpdata", privileged=True)
    shm = parse_sysvipc_shm(await host.read("/proc/sysvipc/shm") or "")
    conf = await host.read("/etc/chrony/chrony.conf") or ""
    servers = [ln.split()[1] for ln in conf.splitlines() if len(ln.split()) > 1 and ln.split()[0] in ("pool", "server")]
    dns = {}
    if online:
        for h in servers[:3]:
            addrs, err = await host.resolve(h)
            dns[h] = {"addresses": addrs, "error": err}
    timed, timed_age, timed_err = _timed_status(cfg.diagnostics.timed_status_file)
    t = parse_tracking(trk) if trk else None
    return {
        "units": units, "chronyc_error": src_err or trk_err, "select_error": sel_err, "ntpdata_error": ntp_err,
        "tracking": asdict(t) if t else None, "synced": bool(t and t.synced),
        "sources": merge_sources(src, stats, sel, ntp, cfg.time_sources.stale_after_s),
        "activity": asdict(a) if (a := parse_activity(act or "")) else None,
        "shm": [asdict(s) for s in shm if s.unit is not None], "servers": servers, "dns": dns, "online": online,
        "timed": timed, "timed_age_s": round(timed_age, 1) if timed_age is not None else None, "timed_error": timed_err,
        "gps": {"enabled": gps["enabled"], "has_fix": bool(gps["detail"].get("has_fix")), "unit": gps["unit"]},
        "dab": {"enabled": dab["enabled"], "sync": bool(dab["mux"] and dab["mux"]["sync"]), "channel": (dab["mux"] or {}).get("channel"),
                "ensemble": (dab["mux"] or {}).get("ensemble")},
    }


def _source_step(c: list[Check], id_: str, group: str, title: str, src: dict[str, Any] | None, upstream_ok: bool, missing_hint: str,
                 no_samples_hint: str, actions: list[str]) -> None:
    def add(status: str, detail: str, hint: str | None = None, acts: list[str] | None = None) -> None:
        c.append(Check(id_, "time", title, status, detail, hint, acts or [], group))  # type: ignore[arg-type]

    if src is None:
        add("fail", "chrony has no such source.", missing_hint)
        return
    offs = f"offset {src['offset_ms']:+.1f} ms ±{src['error_ms']:.1f} ms"
    if src["reach_count"] == 0:
        add("fail" if upstream_ok else "warn", f"No samples (reach {src['reach']}).", no_samples_hint if upstream_ok else "Waiting for the step above.", actions)
    elif src["used"]:
        add("ok", f"{src['state_label'].capitalize()}: {offs}, {src['reach_count']}/8 recent polls answered.")
    else:
        why = src["reason"] or src["state_label"]
        add("warn" if src["state"] in ("x", "T", "~", "d", "?", "s") else "info", f"Not used: {why}. {offs.capitalize()}.")


def checks(f: dict[str, Any]) -> list[Check]:
    c: list[Check] = []

    def add(id_: str, group: str, title: str, status: str, detail: str, hint: str | None = None, actions: list[str] | None = None) -> None:
        c.append(Check(f"time.{id_}", "time", title, status, detail, hint, actions or [], group))  # type: ignore[arg-type]

    g0 = "chrony"
    if f["units"].get("chrony") not in ("active", "unknown"):
        add("chrony", g0, "chrony", "fail", f"chrony is {f['units'].get('chrony')}: nothing keeps the clock right.", "Restart chrony.", ["time.restart_chrony"])
    elif f["chronyc_error"]:
        add("chrony", g0, "chrony", "fail", f"chronyc cannot talk to chronyd: {f['chronyc_error']}",
            "chrony.conf needs 'cmdallow 127.0.0.1' and 'bindcmdaddress 127.0.0.1' (deploy/chrony/chrony.conf).", ["time.restart_chrony"])
    else:
        add("chrony", g0, "chrony", "ok", "chronyd is running and answering.")
    t = f["tracking"]
    if t and f["synced"]:
        off = t["system_offset_s"] * 1000
        detail = f"Synchronised to {t['refname'] or t['refid']} (stratum {t['stratum']}); system clock {off:+.2f} ms from it."
        if abs(off) > 500:
            add("synced", g0, "System clock", "warn", detail + " Still correcting a large offset.")
        else:
            add("synced", g0, "System clock", "ok", detail)
    elif not f["chronyc_error"]:
        add("synced", g0, "System clock", "fail", "Not synchronised to any source.",
            "Alarms use the system clock; until a source below works it free-runs. A Pi 4 has no battery clock, so after a power cut it starts "
            "from the last shutdown time.", ["time.burst"])
    if f["select_error"]:
        add("select_access", g0, "Selection details", "info", "chrony's own reasons for (not) using a source need `sudo chronyc selectdata`.",
            "Re-run install.sh to add the sudo rule (deploy/sudoers/dawn).")
    act = f["activity"]
    if act and act["offline"] and f["online"]:
        add("offline", g0, "chrony thinks it is offline", "warn", f"{act['offline']} source(s) marked offline although the network is up.",
            "NetworkManager's dispatcher normally switches chrony online; restarting chrony also does.", ["time.restart_chrony"])

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for s in f["sources"]:
        by_kind.setdefault(s["kind"], []).append(s)
    shm = {s["unit"]: s for s in f["shm"]}

    if f["gps"]["enabled"]:
        g = "GPS → chrony"
        fix = f["gps"]["has_fix"]
        add("gps.fix", g, "GPS fix", "ok" if fix else "fail", "Receiver has a fix." if fix else "No GPS fix (see GPS).")
        seg = shm.get(0)
        if seg and seg["nattch"] >= 2:
            add("gps.shm", g, "gpsd → shared memory 0", "ok", f"Segment 0 exists (perms {seg['perms']}), {seg['nattch']} processes attached (gpsd and chrony).")
        elif seg:
            add("gps.shm", g, "gpsd → shared memory 0", "warn", f"Segment 0 exists but only {seg['nattch']} process(es) attached: gpsd or chrony is not using it.",
                "Restart gpsd, then chrony.", ["gps.restart", "time.restart_chrony"])
        else:
            add("gps.shm", g, "gpsd → shared memory 0", "fail" if fix else "warn", "No NTP shared-memory segment 0.",
                "chrony creates it at start ('refclock SHM 0'), and gpsd (running as root) writes it. Restart chrony, then gpsd.",
                ["time.restart_chrony", "gps.restart"])
        _source_step(c, "time.gps.source", g, "chrony uses GPS", (by_kind.get("gps") or [None])[0], fix,
                     "Add 'refclock SHM 0 refid GPS prefer trust' to /etc/chrony/chrony.conf (deploy/chrony/chrony.conf).",
                     "gpsd has a fix but chrony gets no samples from SHM 0: restart gpsd, then chrony.", ["gps.restart", "time.restart_chrony"])

    if f["dab"]["enabled"]:
        g = "DAB → chrony"
        d = f["dab"]
        add("dab.sync", g, "DAB ensemble", "ok" if d["sync"] else "fail",
            f"{d['ensemble'] or 'Ensemble'} on {d['channel']}." if d["sync"] else "No ensemble decoded (see DAB radio).")
        unit, st, age = f["units"].get("dawn-timed"), f["timed"], f["timed_age_s"]
        if unit not in ("active", "unknown"):
            add("dab.timed", g, "dawn-timed", "fail", f"dawn-timed is {unit}.", "Restart it.", ["time.restart_timed"])
        elif st is None:
            add("dab.timed", g, "dawn-timed", "warn", f"No status from dawn-timed: {f['timed_error']}.", "Restart it (older versions do not write a status file).",
                ["time.restart_timed"])
        elif age is not None and age > 30:
            add("dab.timed", g, "dawn-timed", "warn", f"Status last written {ago(age)}: the daemon looks stuck.", "Restart it.", ["time.restart_timed"])
        else:
            last = st.get("last_sample_at")
            add("dab.timed", g, "dawn-timed", "ok", f"Running ({'FIC' if st['mode'] == 'fic' else 'mux.json'} mode), {st['samples_written']} samples"
                + (f", last at {last[11:19]} UTC" if last else "") + (" (dry run)" if st.get("dry_run") else "") + ".")
        if st is not None:
            long_, short = st.get("fig010_long", 0), st.get("fig010_short", 0)
            if long_:
                add("dab.fig", g, "Broadcast time (FIG 0/10)", "ok", f"Received with milliseconds ({long_} so far); last offset {st.get('last_offset_ms')} ms.")
            elif short:
                add("dab.fig", g, "Broadcast time (FIG 0/10)", "ok", f"Received, minutes only ({short} so far): good to about a second.")
            elif d["sync"] and st.get("mode") == "fic" and st.get("fibs", 0) > 1000:
                add("dab.fig", g, "Broadcast time (FIG 0/10)", "warn", f"{st['fibs']} FIC blocks read but no time in them.",
                    "This ensemble does not broadcast its time. DAB time follows the tuned channel, so another ensemble may.")
            if st.get("shm_attached") or st.get("dry_run"):
                seg = shm.get(st.get("shm_unit", 2))
                extra = f" Segment {seg['unit']} perms {seg['perms']}, {seg['nattch']} attached." if seg else ""
                add("dab.shm", g, "dawn-timed → shared memory 2", "ok", ("Dry run: samples are only logged." if st.get("dry_run") else "Attached.") + extra)
            else:
                add("dab.shm", g, "dawn-timed → shared memory 2", "fail", f"Not attached: {st.get('shm_error') or 'unknown error'}",
                    "chrony must create the segment world-writable: 'refclock SHM 2:perm=0666 refid DAB' in chrony.conf. Restart chrony, then dawn-timed.",
                    ["time.restart_chrony", "time.restart_timed"])
        dsrc = (by_kind.get("dab") or [None])[0]
        _source_step(c, "time.dab.source", g, "chrony uses DAB", dsrc, bool(d["sync"] and st and (st.get("shm_attached"))),
                     "Add 'refclock SHM 2:perm=0666 refid DAB delay 1.0' to /etc/chrony/chrony.conf.",
                     "dawn-timed writes samples but chrony sees none: restart chrony, then dawn-timed.", ["time.restart_chrony", "time.restart_timed"])
        others = [s for s in f["sources"] if s["used"] and s["kind"] != "dab"]
        if dsrc and dsrc["reach_count"] and others and abs(dsrc["offset_ms"]) > 1000:
            add("dab.offset", g, "Broadcaster's clock", "warn", f"DAB time is {dsrc['offset_ms'] / 1000:+.1f} s from the other sources.",
                "The ensemble's clock is off; chrony gives DAB little weight ('delay 1.0') and ignores it while GPS or NTP agree.")

    g = "Network → chrony"
    add("ntp.online", g, "Internet", "ok" if f["online"] else "fail", "Online." if f["online"] else "Offline (see Network).")
    if f["online"]:
        if not f["servers"]:
            add("ntp.dns", g, "NTP servers", "info", "No pool/server lines found in /etc/chrony/chrony.conf.")
        for h, r in f["dns"].items():
            if r["addresses"]:
                add(f"ntp.dns.{h}", g, f"DNS: {h}", "ok", f"Resolves to {len(r['addresses'])} address(es).")
            else:
                add(f"ntp.dns.{h}", g, f"DNS: {h}", "fail", f"Does not resolve: {r['error']}", "Check DNS under Network.")
    ntp = by_kind.get("ntp") or []
    silent = [s for s in ntp if s["ntp"] and s["ntp"]["total_tx"] > 2 and s["ntp"]["total_rx"] == 0]
    if silent and f["online"]:
        add("ntp.replies", g, "NTP replies", "fail", f"{len(silent)} server(s) never answered ({silent[0]['ntp']['total_tx']} requests to {silent[0]['name']}).",
            "Outgoing UDP port 123 looks blocked by the router or the ISP.")
    used = [s for s in ntp if s["used"]]
    reachable = [s for s in ntp if s["reach_count"]]
    if used:
        add("ntp.source", g, "chrony uses NTP", "ok", f"{len(used)} of {len(ntp)} servers used; best offset {min(abs(s['offset_ms']) for s in used):.1f} ms.")
    elif reachable:
        why = reachable[0]["reason"] or reachable[0]["state_label"]
        add("ntp.source", g, "chrony uses NTP", "info", f"{len(reachable)} server(s) answering, not used: {why}.")
    elif f["online"]:
        add("ntp.source", g, "chrony uses NTP", "warn", f"None of {len(ntp)} NTP servers is answering." if ntp else "No NTP servers configured.", None, ["time.burst"])
    return c
