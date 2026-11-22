"""dawn-timed tells systemd it is up (READY=1 after its first pass) and alive (WATCHDOG=1 on every pass), so the unit's
WatchdogSec restarts a dawn-timed hung on a socket. A datagram socket in a temporary directory plays systemd."""

from __future__ import annotations

import argparse
import asyncio
import socket

import httpx
import pytest

from dawn_timed import main as timed


def _args(tmp_path) -> argparse.Namespace:
    return argparse.Namespace(welle_url="http://welle", shm_unit=2, poll_hz=5.0, fic_fallback="off", time_t_bytes="auto",
                              status_file=str(tmp_path / "status.json"), dry_run=True, log_level="INFO")


async def test_ready_after_the_first_pass_then_watchdog_on_every_pass(tmp_path, monkeypatch) -> None:
    path = str(tmp_path / "notify")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(path)
    sock.setblocking(False)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    monkeypatch.setattr(timed, "WATCHDOG_EVERY_S", 0.0)
    t = timed.Timed(_args(tmp_path))
    t.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ensemble": {}})))
    passes = 0

    async def no_wait(_s: float) -> None:
        nonlocal passes
        passes += 1
        if passes >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(timed.asyncio, "sleep", no_wait)
    with pytest.raises(asyncio.CancelledError):
        await t.run()
    got = []
    while True:
        try:
            got.append(sock.recv(64).decode())
        except BlockingIOError:
            break
    sock.close()
    assert got[0] == "READY=1"
    assert got.count("READY=1") == 1 and got.count("WATCHDOG=1") >= 2


def test_the_unit_asks_for_notify_and_a_watchdog() -> None:
    from pathlib import Path

    unit = (Path(__file__).resolve().parents[2] / "deploy" / "systemd" / "dawn-timed.service").read_text()
    lines = {ln.strip() for ln in unit.splitlines()}
    for want in ("Type=notify", "NotifyAccess=main", "WatchdogSec=60", "Restart=always"):
        assert want in lines, want


async def test_an_unreachable_decoder_is_asked_less_and_less_often(tmp_path, monkeypatch) -> None:
    t = timed.Timed(_args(tmp_path))
    up = {"ok": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if not up["ok"]:
            raise httpx.ConnectError("welle-cli is stopped")
        return httpx.Response(200, json={"ensemble": {}})

    t.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    waits: list[float] = []

    async def record(s: float) -> None:
        waits.append(s)

    monkeypatch.setattr(timed.asyncio, "sleep", record)
    for _ in range(6):
        await t._poll_mux()
    assert waits == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    up["ok"] = True
    await t._poll_mux()  # answers (no sync): the backoff starts over
    waits.clear()
    up["ok"] = False
    await t._poll_mux()
    assert waits == [2.0]
