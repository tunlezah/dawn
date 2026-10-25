"""Core soak: dawn-core in-process under tracemalloc, driven hard through its API and WebSocket while the simulator
stack (hub, fake welle-cli, fake gpsd) runs separately. Samples RSS, fds, threads, asyncio tasks, reachable objects and
the sizes of every unbounded-looking structure every 30 s; prints a tracemalloc growth table every 5 minutes."""

from __future__ import annotations

import asyncio
import gc
import json
import os
import random
import sys
import threading
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("SOAK_OUT", str(Path(__file__).resolve().parent / "out")))
OUT.mkdir(parents=True, exist_ok=True)
DUR = float(os.environ.get("SOAK_MINUTES", "20")) * 60
BASE = "http://127.0.0.1:8080"
HUB = "http://127.0.0.1:8099"
os.chdir(ROOT)
os.environ.update(DAWN_SIM="1", DAWN_CONFIG=str(ROOT / "config/config.sim.yaml"), DAWN_DATA_DIR=str(ROOT / "var/dawn-soak"), DAWN_RUNTIME_DIR=str(ROOT / "var/run-soak"))
tracemalloc.start(1)  # one frame: snapshots and comparisons stay cheap (20 frames blocked the loop for minutes)

import httpx  # noqa: E402
import uvicorn  # noqa: E402
import websockets  # noqa: E402

from dawn_core.app import create_app  # noqa: E402
from dawn_core.config import ConfigManager, config_path  # noqa: E402
from dawn_core.logging_setup import RING, setup_logging  # noqa: E402

setup_logging("WARNING")
LOG = open(OUT / "soak.log", "a")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=LOG, flush=True)


def rss_mb() -> float:
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return 0.0


async def ok(c: httpx.AsyncClient, method: str, path: str, **kw):
    try:
        r = await c.request(method, BASE + path, timeout=15, **kw)
        return r
    except Exception as e:  # noqa: BLE001
        log("req failed", method, path, type(e).__name__, e)
        return None


async def hub_set(c: httpx.AsyncClient, **patch):
    try:
        await c.post(HUB + "/set", json=patch, timeout=5)
    except Exception as e:  # noqa: BLE001
        log("hub set failed", e)


# ---- drivers ----------------------------------------------------------------------------------------------------
async def ws_persistent(role: str | None, stop: asyncio.Event, counter: dict):
    while not stop.is_set():
        try:
            async with websockets.connect("ws://127.0.0.1:8080/ws", max_size=None) as ws:
                if role:
                    await ws.send(json.dumps({"type": "hello", "role": role}))
                last_ping = time.monotonic()
                while not stop.is_set():
                    try:
                        msg = await asyncio.wait_for(ws.recv(), 5)
                        counter["msgs"] += 1
                        counter["bytes"] += len(msg)
                    except TimeoutError:
                        pass
                    if role and time.monotonic() - last_ping > 15:
                        await ws.send(json.dumps({"type": "ping"}))
                        last_ping = time.monotonic()
        except Exception as e:  # noqa: BLE001
            log("ws", role, "dropped:", type(e).__name__, e)
            await asyncio.sleep(1)


async def ws_churn(stop: asyncio.Event, counter: dict):
    """A client connects, listens 12 s, goes away (a phone opening and closing the control UI); sometimes vanishes
    without closing (TCP cut)."""
    while not stop.is_set():
        try:
            ws = await websockets.connect("ws://127.0.0.1:8080/ws", max_size=None)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 12 and not stop.is_set():
                try:
                    await asyncio.wait_for(ws.recv(), 3)
                    counter["churn_msgs"] += 1
                except TimeoutError:
                    pass
            if random.random() < 0.3:
                ws.transport.abort() if hasattr(ws, "transport") else await ws.close()  # vanish
            else:
                await ws.close()
            counter["churn_conns"] += 1
        except Exception as e:  # noqa: BLE001
            log("ws churn:", type(e).__name__, e)
        await asyncio.sleep(2)


async def play_stop(c: httpx.AsyncClient, stop: asyncio.Event, sids: list[str], counter: dict):
    i = 0
    while not stop.is_set():
        i += 1
        try:
            if sids:
                await ok(c, "POST", "/api/dab/play", json={"sid": sids[i % len(sids)]})
                await asyncio.sleep(2.5)
            await ok(c, "POST", "/api/audio/play", json={"source": "chime:birds"})
            await asyncio.sleep(1.5)
            await ok(c, "POST", "/api/presets/next")
            await asyncio.sleep(1.5)
            await ok(c, "POST", "/api/audio/stop")
            counter["play_cycles"] += 1
        except Exception as e:  # noqa: BLE001
            log("play_stop:", e)
        await asyncio.sleep(1)


async def rings(c: httpx.AsyncClient, stop: asyncio.Event, counter: dict):
    n = 0
    while not stop.is_set():
        n += 1
        try:
            silent = n % 4 == 0
            if silent:
                await hub_set(c, audio_flowing=False)  # nothing reaches the speaker: the ladder must climb to the backup tone
            await ok(c, "POST", "/api/alarms/test", json={"source": "chime:birds", "volume": 50, "label": f"Soak {n}"})
            await asyncio.sleep(28 if silent else 3)
            await ok(c, "POST", "/api/alarms/snooze")
            await asyncio.sleep(2)
            await ok(c, "POST", "/api/alarms/stop")
            if silent:
                await hub_set(c, audio_flowing=True)
            counter["rings"] += 1
        except Exception as e:  # noqa: BLE001
            log("rings:", e)
        await asyncio.sleep(12)


async def alarms_crud(c: httpx.AsyncClient, stop: asyncio.Event, counter: dict):
    while not stop.is_set():
        try:
            r = await ok(c, "POST", "/api/alarms", json={"label": "Soak", "time": f"{random.randint(0, 23):02d}:{random.randint(0, 59):02d}", "repeat": random.choice(["daily", "weekdays", "custom"]), "days": [0, 2, 4], "source": "chime:birds", "skip_public_holidays": True})
            if r is not None and r.status_code == 201:
                aid = r.json()["id"]
                await ok(c, "GET", "/api/alarms")
                await ok(c, "PUT", f"/api/alarms/{aid}", json={"label": "Soak edited", "time": "06:30", "repeat": "daily", "source": "dab:1002"})
                await ok(c, "POST", f"/api/alarms/{aid}/skip-next", json={"skip": True})
                await ok(c, "POST", f"/api/alarms/{aid}/enabled", json={"enabled": False})
                await ok(c, "POST", f"/api/alarms/{aid}/enabled", json={"enabled": True})
                await asyncio.sleep(2)
                await ok(c, "DELETE", f"/api/alarms/{aid}")
                counter["alarm_crud"] += 1
        except Exception as e:  # noqa: BLE001
            log("alarms_crud:", e)
        await asyncio.sleep(10)


async def inputs(c: httpx.AsyncClient, stop: asyncio.Event, counter: dict):
    evs = ["touch", "encoder_cw", "encoder_ccw", "encoder_push", "touch", "encoder_cw", "encoder_ccw"]
    while not stop.is_set():
        try:
            await ok(c, "POST", "/api/input", json={"event": random.choice(evs)})
            counter["inputs"] += 1
        except Exception as e:  # noqa: BLE001
            log("inputs:", e)
        await asyncio.sleep(1.5)


async def polls(c: httpx.AsyncClient, stop: asyncio.Event, counter: dict):
    paths = ["/api/state", "/api/health", "/api/alarms", "/api/events?limit=50", "/api/diag", "/api/diag/history?keys=dab.snr,sys.mem&hours=1", "/api/system/backup", "/api/dab/services", "/api/presets", "/api/config", "/api/system/logs?lines=50", "/api/display/sun", "/api/weather"]
    while not stop.is_set():
        for p in paths:
            r = await ok(c, "GET", p)
            if r is not None and r.status_code >= 500:
                log("poll", p, r.status_code, r.text[:200])
            counter["polls"] += 1
            await asyncio.sleep(0.7)


async def config_churn(c: httpx.AsyncClient, stop: asyncio.Event, counter: dict):
    flip = False
    while not stop.is_set():
        await asyncio.sleep(45)
        flip = not flip
        r = await ok(c, "PATCH", "/api/config", json={"display": {"show_seconds": flip}, "holidays": {"region": "VIC" if flip else "NSW"}})
        if r is not None and r.status_code != 200:
            log("config patch", r.status_code, r.text[:200])
        counter["config"] += 1


async def timers(c: httpx.AsyncClient, stop: asyncio.Event, counter: dict):
    while not stop.is_set():
        await asyncio.sleep(70)
        try:
            await ok(c, "POST", "/api/timers/sleep", json={"minutes": 1})
            await asyncio.sleep(15)
            await ok(c, "DELETE", "/api/timers/sleep")
            await ok(c, "POST", "/api/timers/nap", json={"minutes": 1})
            await asyncio.sleep(63)  # it rings
            await ok(c, "POST", "/api/alarms/stop")
            counter["timers"] += 1
        except Exception as e:  # noqa: BLE001
            log("timers:", e)


# ---- sampler ----------------------------------------------------------------------------------------------------
def structures(ctx) -> dict[str, int]:
    from dawn_core.alarms.service import AlarmService
    from dawn_core.audio.service import AudioService
    from dawn_core.dab.service import DabService
    from dawn_core.diagnostics.service import DiagnosticsService
    from dawn_core.system.supervisor import SupervisorService

    out: dict[str, int] = {}
    audio: AudioService = ctx.svc(AudioService)
    out["players"] = len(audio.players)
    out["listeners"] = sum(len(getattr(p, "_listeners", [])) for p in audio.players.values())
    out["slots"] = len(audio.arbiter.slots)
    out["ws_clients"] = len(ctx.ws_hub.clients)
    out["ws_info"] = len(ctx.ws_hub.info)
    out["store_subs"] = len(ctx.store._subs)
    out["store_events"] = len(ctx.store.events)
    out["ring_log"] = len(RING.records)
    a: AlarmService = ctx.svc(AlarmService)
    out["calendars"] = len(a._calendars)
    out["handled"] = len(a._handled)
    out["row_errors"] = len(a._row_errors)
    out["dab_fail"] = len(a._dab_fail)
    d: DabService = ctx.svc(DabService)
    out["dab_msgs"] = len(d.messages)
    out["dab_live"] = len(d.live)
    out["dab_err_prev"] = len(d._err_prev)
    out["dab_slide_seen"] = len(d._slide_seen)
    diag: DiagnosticsService = ctx.svc(DiagnosticsService)
    out["series_cache"] = len(diag._series_cache)
    out["host_cache"] = len(diag.host._cache)
    sup: SupervisorService = ctx.svc(SupervisorService)
    out["repairs"] = len(sup.repairs)
    out["sup_bad"] = len(sup._bad_since) + len(sup._good_since) + len(sup._backoff)
    from dawn_core.alarms.holidays import _calendar

    out["holiday_lru"] = _calendar.cache_info().currsize
    return out


BASE_SNAP: list = []


async def sampler(ctx, stop: asyncio.Event, counter: dict):
    csv = open(OUT / "samples.csv", "w")
    keys = None
    t0 = time.monotonic()
    while not stop.is_set():
        await asyncio.sleep(30)
        gc.collect()
        s = structures(ctx)
        cur, peak = tracemalloc.get_traced_memory()
        row = {"t_s": int(time.monotonic() - t0), "rss_mb": round(rss_mb(), 1), "traced_mb": round(cur / 1048576, 2), "fds": len(os.listdir("/proc/self/fd")), "threads": threading.active_count(),
               "tasks": len(asyncio.all_tasks()), "objects": len(gc.get_objects()), **s, **{k: v for k, v in counter.items()}}
        if keys is None:
            keys = list(row)
            csv.write(",".join(keys) + "\n")
        csv.write(",".join(str(row.get(k, "")) for k in keys) + "\n")
        csv.flush()
        if not BASE_SNAP and time.monotonic() - t0 >= 120:
            BASE_SNAP.append(tracemalloc.take_snapshot())
            log("baseline tracemalloc snapshot taken")


def final_table(ctx) -> None:
    """Once, after the drivers have stopped: what grew since the 2-minute baseline, by source line."""
    gc.collect()
    snap = tracemalloc.take_snapshot()
    with open(OUT / "tracemalloc.txt", "a") as f:
        f.write("=== growth since the baseline at t=120 s, by line (top 30) ===\n")
        diffs = snap.compare_to(BASE_SNAP[0], "lineno") if BASE_SNAP else []
        for st in diffs[:30]:
            f.write(f"{st.size_diff / 1024:+9.1f} KB  {st.count_diff:+7d}  {st}\n")
        f.write("--- dawn_core lines only (top 20 by growth) ---\n")
        n = 0
        for st in diffs:
            if "dawn_core" in str(st.traceback) and n < 20:
                n += 1
                f.write(f"{st.size_diff / 1024:+9.1f} KB  {st.count_diff:+7d}  {st}\n")
        f.write("--- asyncio tasks by name prefix ---\n")
        names: dict[str, int] = {}
        for t in asyncio.all_tasks():
            names[t.get_name().split("-")[0]] = names.get(t.get_name().split("-")[0], 0) + 1
        f.write(json.dumps(dict(sorted(names.items(), key=lambda kv: -kv[1]))) + "\n")


async def main() -> None:
    app = create_app(ConfigManager(config_path()))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8080, log_level="warning", ws_ping_interval=20, ws_ping_timeout=20))
    serve = asyncio.create_task(server.serve())
    async with httpx.AsyncClient() as c:
        for _ in range(60):
            r = await ok(c, "GET", "/api/health")
            if r is not None and r.status_code == 200:
                break
            await asyncio.sleep(1)
        log("core up:", r.json() if r else None)
        await hub_set(c, audio_flowing=True, sdr_present=True)
        await ok(c, "POST", "/api/dab/scan")
        sids: list[str] = []
        for _ in range(90):
            await asyncio.sleep(1)
            r = await ok(c, "GET", "/api/dab/services")
            if r is not None and r.status_code == 200 and len(r.json()) >= 3:
                sids = [s["sid"] for s in r.json()][:6]
                break
        log("scan done; stations:", sids)
        for sid in sids[:3]:
            await ok(c, "POST", "/api/presets", json={"label": f"P {sid}", "source": f"dab:{sid}"})
        ctx = app.state.ctx
        stop = asyncio.Event()
        counter: dict = {k: 0 for k in ("msgs", "bytes", "churn_msgs", "churn_conns", "play_cycles", "rings", "alarm_crud", "inputs", "polls", "config", "timers")}
        tasks = [
            asyncio.create_task(ws_persistent("face", stop, counter)), asyncio.create_task(ws_persistent(None, stop, counter)),
            asyncio.create_task(ws_churn(stop, counter)), asyncio.create_task(play_stop(c, stop, sids, counter)), asyncio.create_task(rings(c, stop, counter)),
            asyncio.create_task(alarms_crud(c, stop, counter)), asyncio.create_task(inputs(c, stop, counter)), asyncio.create_task(polls(c, stop, counter)),
            asyncio.create_task(config_churn(c, stop, counter)), asyncio.create_task(timers(c, stop, counter)), asyncio.create_task(sampler(ctx, stop, counter)),
        ]
        log("drivers started; running for", DUR / 60, "min")
        await asyncio.sleep(DUR)
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        log("done; counters:", counter)
        await asyncio.sleep(5)  # let the last ring and sources wind down
        final_table(ctx)
        log("final tracemalloc table written")
    server.should_exit = True
    await serve


if __name__ == "__main__":
    asyncio.run(main())
