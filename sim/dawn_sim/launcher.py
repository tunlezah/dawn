"""`dawn-sim` / `python -m dawn_sim`: run the simulators, and optionally core and
dawn-timed as child processes, with prefixed logs. `make sim` uses this."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

import uvicorn

from . import dab, gps, hub
from .keyboard import keyboard_loop
from .simstate import STATE

ROOT = Path(__file__).resolve().parents[2]


async def _serve(app, port: int, host: str = "127.0.0.1") -> None:
    cfg = uvicorn.Config(app, host=host, port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(cfg)
    await server.serve()


async def _child(name: str, cmd: list[str], env: dict[str, str], stop: asyncio.Event) -> None:
    while not stop.is_set():
        proc = await asyncio.create_subprocess_exec(*cmd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        STATE.note(f"started {name} pid {proc.pid}")
        assert proc.stdout
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                sys.stdout.write(f"[{name}] {line.decode(errors='ignore')}")
                sys.stdout.flush()
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except TimeoutError:
                    proc.kill()
        if not stop.is_set():
            STATE.note(f"{name} exited with {proc.returncode}; restarting in 2 s")
            await asyncio.sleep(2)


async def amain(args: argparse.Namespace) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(_serve(hub.create_app(), args.hub_port), name="hub"),
        asyncio.create_task(_serve(dab.create_app(), args.welle_port), name="welle"),
    ]
    g = gps.GpsSim(gpsd_port=args.gpsd_port, pty=args.pty)
    await g.start()
    STATE.note(f"sim hub http://127.0.0.1:{args.hub_port}/  fake welle-cli http://127.0.0.1:{args.welle_port}/mux.json")

    env = dict(os.environ)
    env["DAWN_SIM"] = "1"
    env.setdefault("DAWN_CONFIG", str(ROOT / "config" / "config.sim.yaml"))
    env.setdefault("DAWN_DATA_DIR", str(ROOT / "var" / "dawn"))
    env.setdefault("PYTHONUNBUFFERED", "1")
    py = sys.executable
    if not args.no_core:
        tasks.append(asyncio.create_task(_child("core", [py, "-m", "dawn_core"], env, stop)))
    if not args.no_timed:
        status = str(ROOT / "var" / "run" / "dawn-timed" / "status.json")
        tasks.append(asyncio.create_task(_child("timed", [py, "-m", "dawn_timed", "--dry-run", "--welle-url", f"http://127.0.0.1:{args.welle_port}", "--status-file", status], env, stop)))
    if not args.no_keyboard:
        tasks.append(asyncio.create_task(keyboard_loop(stop)))

    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dawn-sim", description="Dawn laptop simulator")
    ap.add_argument("--hub-port", type=int, default=8099)
    ap.add_argument("--welle-port", type=int, default=8000)
    ap.add_argument("--gpsd-port", type=int, default=2947)
    ap.add_argument("--pty", action="store_true", help="also emit NMEA on a pty for a real gpsd")
    ap.add_argument("--no-core", action="store_true", help="do not start dawn-core")
    ap.add_argument("--no-timed", action="store_true", help="do not start dawn-timed")
    ap.add_argument("--no-keyboard", action="store_true")
    args = ap.parse_args(argv)
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
