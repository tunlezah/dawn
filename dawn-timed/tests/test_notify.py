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


def test_dab_time_is_checked_against_the_last_saved_time(tmp_path) -> None:
    from datetime import UTC, datetime, timedelta

    fake = tmp_path / "fake-hwclock.data"
    fake.write_text("2026-11-22 03:00:00\n")
    ref = timed.reference_time(fake, tmp_path / "no-rtc")
    assert ref == datetime(2026, 11, 22, 3, 0, tzinfo=UTC)
    assert timed.implausible(ref + timedelta(minutes=50), ref) is None  # later today: fine
    assert timed.implausible(ref - timedelta(seconds=30), ref) is None  # within a minute of it
    assert "before the last saved time" in timed.implausible(ref - timedelta(weeks=1024), ref)  # a GPS-style week rollover
    assert "two years" in timed.implausible(ref + timedelta(days=800), ref)
    assert timed.implausible(datetime(2001, 1, 1, tzinfo=UTC), None) is None  # nothing to judge by
    rtc = tmp_path / "since_epoch"
    rtc.write_text(str(int(datetime(2026, 11, 23, tzinfo=UTC).timestamp())))
    assert timed.reference_time(fake, rtc) == datetime(2026, 11, 23, tzinfo=UTC)  # the later of the two


def test_a_rejected_sample_is_counted_and_not_written(tmp_path, monkeypatch) -> None:
    from datetime import UTC, datetime

    t = timed.Timed(_args(tmp_path))
    monkeypatch.setattr(timed, "reference_time", lambda: datetime(2026, 11, 22, tzinfo=UTC))
    now = datetime(2026, 11, 22, 1, 0, tzinfo=UTC)
    t._sample(datetime(2007, 4, 8, tzinfo=UTC), now, precision=-3)
    assert t.written == 0 and t.rejected == 1 and "before" in t.status()["last_rejected"]
    t._sample(now, now, precision=-3)
    assert t.written == 1
