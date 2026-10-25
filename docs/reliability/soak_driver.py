"""Plain-process soak: dawn-core started as `python -m dawn_core` (as on the Pi), driven through its API and WebSocket by
this separate process, with its RSS, descriptors and threads sampled from /proc every 30 s. No tracemalloc, so the
RSS curve is the real one."""

from __future__ import annotations

import asyncio
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("SOAK_OUT", str(Path(__file__).resolve().parent / "out")))
OUT.mkdir(parents=True, exist_ok=True)
DUR = float(os.environ.get("SOAK_MINUTES", "15")) * 60
PORT = int(os.environ.get("SOAK_PORT", "8080"))
BASE = f"http://127.0.0.1:{PORT}"
HUB = "http://127.0.0.1:8099"
LOG = open(OUT / "plain-soak.log", "a")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=LOG, flush=True)


def proc_stats(pid: int) -> dict:
    out = {"rss_mb": 0.0, "threads": 0, "fds": 0}
    try:
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("VmRSS:"):
                out["rss_mb"] = round(int(line.split()[1]) / 1024, 1)
            elif line.startswith("Threads:"):
                out["threads"] = int(line.split()[1])
        out["fds"] = len(os.listdir(f"/proc/{pid}/fd"))
    except OSError:
        pass
    return out


async def ok(c: httpx.AsyncClient, method: str, path: str, **kw):
    try:
        return await c.request(method, BASE + path, timeout=15, **kw)
    except Exception as e:  # noqa: BLE001
        log("req failed", method, path, type(e).__name__, e)
        return None


async def hub_set(c: httpx.AsyncClient, **patch):
    try:
        await c.post(HUB + "/set", json=patch, timeout=5)
    except Exception as e:  # noqa: BLE001
        log("hub set failed", e)


async def ws_persistent(role: str | None, stop: asyncio.Event, counter: dict):
    while not stop.is_set():
        try:
            async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws", max_size=None) as ws:
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
    while not stop.is_set():
        try:
            ws = await websockets.connect(f"ws://127.0.0.1:{PORT}/ws", max_size=None)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 12 and not stop.is_set():
                try:
                    await asyncio.wait_for(ws.recv(), 3)
                except TimeoutError:
                    pass
            if random.random() < 0.3 and hasattr(ws, "transport"):
                ws.transport.abort()
            else:
                await ws.close()
            counter["churn_conns"] += 1
        except Exception as e:  # noqa: BLE001
            log("ws churn:", type(e).__name__, e)
        await asyncio.sleep(2)


async def play_stop(c, stop, sids, counter):
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


async def rings(c, stop, counter):
    n = 0
    while not stop.is_set():
        n += 1
        try:
            silent = n % 4 == 0
            if silent:
                await hub_set(c, audio_flowing=False)
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


async def alarms_crud(c, stop, counter):
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


async def inputs(c, stop, counter):
    evs = ["touch", "encoder_cw", "encoder_ccw", "encoder_push", "touch", "encoder_cw", "encoder_ccw"]
    while not stop.is_set():
        await ok(c, "POST", "/api/input", json={"event": random.choice(evs)})
        counter["inputs"] += 1
        await asyncio.sleep(1.5)


async def polls(c, stop, counter):
    paths = ["/api/state", "/api/health", "/api/alarms", "/api/events?limit=50", "/api/diag", "/api/diag/history?keys=dab.snr,sys.mem&hours=1", "/api/system/backup", "/api/dab/services", "/api/presets", "/api/config", "/api/system/logs?lines=50", "/api/display/sun", "/api/weather"]
    while not stop.is_set():
        for p in paths:
            r = await ok(c, "GET", p)
            if r is not None and r.status_code >= 500:
                log("poll", p, r.status_code, r.text[:200])
            counter["polls"] += 1
            await asyncio.sleep(0.7)


async def config_churn(c, stop, counter):
    flip = False
    while not stop.is_set():
        await asyncio.sleep(45)
        flip = not flip
        r = await ok(c, "PATCH", "/api/config", json={"display": {"show_seconds": flip}, "holidays": {"region": "VIC" if flip else "NSW"}})
        if r is not None and r.status_code != 200:
            log("config patch", r.status_code, r.text[:200])
        counter["config"] += 1


async def timers(c, stop, counter):
    while not stop.is_set():
        await asyncio.sleep(70)
        try:
            await ok(c, "POST", "/api/timers/sleep", json={"minutes": 1})
            await asyncio.sleep(15)
            await ok(c, "DELETE", "/api/timers/sleep")
            await ok(c, "POST", "/api/timers/nap", json={"minutes": 1})
            await asyncio.sleep(63)
            await ok(c, "POST", "/api/alarms/stop")
            counter["timers"] += 1
        except Exception as e:  # noqa: BLE001
            log("timers:", e)


async def sampler(pid: int, stop: asyncio.Event, counter: dict, c: httpx.AsyncClient):
    csv = open(OUT / "plain-samples.csv", "w")
    csv.write("t_s,rss_mb,threads,fds,alive,msgs,bytes,churn_conns,play_cycles,rings,alarm_crud,inputs,polls,config,timers\n")
    t0 = time.monotonic()
    while not stop.is_set():
        await asyncio.sleep(30)
        s = proc_stats(pid)
        r = await ok(c, "GET", "/api/health")
        alive = r.json().get("alarms_alive") if r is not None and r.status_code == 200 else None
        csv.write(",".join(str(x) for x in (int(time.monotonic() - t0), s["rss_mb"], s["threads"], s["fds"], alive, *[counter[k] for k in ("msgs", "bytes", "churn_conns", "play_cycles", "rings", "alarm_crud", "inputs", "polls", "config", "timers")])) + "\n")
        csv.flush()


async def main() -> None:
    env = dict(os.environ, DAWN_SIM="1", DAWN_CONFIG=str(ROOT / "config/config.sim.yaml"), DAWN_DATA_DIR=str(ROOT / "var/dawn-plain"), DAWN_RUNTIME_DIR=str(ROOT / "var/run-plain"), PYTHONUNBUFFERED="1")
    core = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "dawn_core", "--port", str(PORT)], cwd=ROOT, env=env, stdout=open(OUT / "plain-core.log", "w"), stderr=subprocess.STDOUT)
    log("core pid", core.pid)
    try:
        async with httpx.AsyncClient() as c:
            for _ in range(60):
                r = await ok(c, "GET", "/api/health")
                if r is not None and r.status_code == 200:
                    break
                await asyncio.sleep(1)
            await hub_set(c, audio_flowing=True, sdr_present=True)
            await ok(c, "POST", "/api/dab/scan")
            sids: list[str] = []
            for _ in range(90):
                await asyncio.sleep(1)
                r = await ok(c, "GET", "/api/dab/services")
                if r is not None and r.status_code == 200 and len(r.json()) >= 3:
                    sids = [s["sid"] for s in r.json()][:6]
                    break
            for sid in sids[:3]:
                await ok(c, "POST", "/api/presets", json={"label": f"P {sid}", "source": f"dab:{sid}"})
            log("scan done; stations:", sids, "start RSS", proc_stats(core.pid))
            stop = asyncio.Event()
            counter: dict = {k: 0 for k in ("msgs", "bytes", "churn_conns", "play_cycles", "rings", "alarm_crud", "inputs", "polls", "config", "timers")}
            tasks = [asyncio.create_task(x) for x in (
                ws_persistent("face", stop, counter), ws_persistent(None, stop, counter), ws_churn(stop, counter), play_stop(c, stop, sids, counter),
                rings(c, stop, counter), alarms_crud(c, stop, counter), inputs(c, stop, counter), polls(c, stop, counter), config_churn(c, stop, counter),
                timers(c, stop, counter), sampler(core.pid, stop, counter, c))]
            log("drivers started for", DUR / 60, "min")
            await asyncio.sleep(DUR)
            stop.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            log("done; counters:", counter, "end RSS", proc_stats(core.pid))
    finally:
        core.terminate()
        try:
            core.wait(10)
        except subprocess.TimeoutExpired:
            core.kill()


if __name__ == "__main__":
    asyncio.run(main())
