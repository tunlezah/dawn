"""DAB and the RTL-SDR Blog V4 (robustness plan WP-E): the stick named from sysfs without opening it, the "zombie"
decoder (it answers but no samples arrive) restarted by the supervisor and then re-enumerated on USB, the alarm's own
preparation restarting it once as a step under way, and dropouts logged with the heat and the power flags."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from test_supervisor import FakeHost, restarts, sv  # noqa: F401  (sv is the fixture)

from dawn_core.dab.service import PREP_NOSYNC_S, DabService
from dawn_core.db.models import DabEnsembleRow
from dawn_core.system import hw
from dawn_core.system import supervisor as sup

# What the sticks report in sysfs. The V4 strings are those rtlsdr_get_usb_strings returns for an RTL-SDR Blog V4
# (manufacturer "RTLSDRBlog", product "Blog V4"); confirm on the device with
# `lsusb -v -d 0bda:2838 | grep -E 'iManufacturer|iProduct'` and correct them here if they differ.
STICKS = {
    "v4": ("0bda", "2838", "RTLSDRBlog", "Blog V4"),
    "v3": ("0bda", "2838", "RTLSDRBlog", "Blog V3"),
    "generic": ("0bda", "2838", "Realtek", "RTL2838UHIDIR"),
    "nameless": ("0bda", "2832", "", ""),
}


def _sysfs(root: Path, vid: str, pid: str, manufacturer: str, product: str) -> None:
    hub = root / "1-1"
    hub.mkdir(parents=True)
    (hub / "idVendor").write_text("1d6b\n")
    (hub / "idProduct").write_text("0002\n")
    dev = root / "1-1.3"
    dev.mkdir()
    for name, value in (("idVendor", vid), ("idProduct", pid), ("manufacturer", manufacturer), ("product", product)):
        if value:
            (dev / name).write_text(value + "\n")


@pytest.mark.parametrize(("kind", "want"), [
    ("v4", "RTL-SDR Blog V4 (R828D)"),
    ("v3", "RTL-SDR Blog V3 (R820T2)"),
    ("generic", "Realtek RTL2838UHIDIR (RTL2838 (generic RTL2832U))"),
    ("nameless", "RTL2832U"),
])
def test_the_stick_is_named_from_sysfs_without_opening_it(tmp_path, kind, want, monkeypatch) -> None:
    _sysfs(tmp_path, *STICKS[kind])
    calls = []
    monkeypatch.setattr(hw.subprocess, "run", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("opened the stick")))
    assert hw.sdr_identity(str(tmp_path)) == (True, want, f"{STICKS[kind][0]}:{STICKS[kind][1]}")
    assert calls == []


def test_no_stick_and_welle_names() -> None:
    assert hw.sdr_identity("/nonexistent") == (False, None, None)
    assert hw.name_from_welle("RTLSDRBlog Blog V4 SN: 00000001") == "RTL-SDR Blog V4 (R828D)"
    assert hw.name_from_welle("Generic RTL2832U OEM") == "Generic RTL2832U OEM"
    assert hw.name_from_welle(None) is None


def test_rtl_test_only_runs_when_the_decoder_is_stopped(monkeypatch) -> None:
    ran = []
    monkeypatch.setattr(hw, "sdr_identity", lambda: (True, "RTL2838 (generic RTL2832U)", "0bda:2838"))

    class Out:
        stdout = "Found Rafael Micro R820T tuner\n"
        stderr = ""

    monkeypatch.setattr(hw.subprocess, "run", lambda *a, **k: ran.append(a) or Out())
    assert hw.detect_sdr(probe_tuner=False)[1] == "RTL2838 (generic RTL2832U)" and ran == []
    assert hw.detect_sdr(probe_tuner=True)[1] == "RTL2838 (generic RTL2832U) (Rafael Micro R820T tuner)"


# ---- the zombie decoder --------------------------------------------------------------------------------------------
@pytest.fixture()
def zombie(sv, monkeypatch):  # noqa: F811
    s, ctx, host = sv
    dab: DabService = ctx.svc(DabService)
    dab._task.cancel()  # its poll would race the checks below
    # the heartbeat looks for the stick on this machine's USB bus once a minute (there is none): it is there
    monkeypatch.setattr(hw, "sdr_on_usb", lambda: (True, "RTL-SDR Blog V4 (R828D)"))
    ctx.store.state.dab.sdr_present = True
    dab.channel, dab.live_sync = "9A", False
    with ctx.db.session() as db:
        db.add(DabEnsembleRow(channel="9A", eid="1001", label="ABC Sydney", snr=12.0))
        db.commit()
    calls: list[str] = []

    async def reachable() -> bool:
        return True

    async def restart(force=False, reason="unreachable") -> bool:
        calls.append(reason)
        return True

    monkeypatch.setattr(dab.client, "reachable", reachable)
    monkeypatch.setattr(dab, "restart_welle", restart)
    return s, ctx, host, dab, calls


def _usb_resets(host: FakeHost) -> int:
    return sum(1 for c in host.sudo_calls if c == ("/usr/local/bin/dawn-usb-reset",))


async def test_a_decoder_that_answers_without_sync_is_restarted(zombie) -> None:
    s, ctx, host, dab, calls = zombie
    t = 1000.0
    await s.check(now=t)
    await s.check(now=t + ctx.config.dab.no_sync_restart_s - 1)
    assert calls == []
    await s.check(now=t + ctx.config.dab.no_sync_restart_s)
    assert calls == ["no sync"]
    await s.check(now=t + ctx.config.dab.no_sync_restart_s + 10)
    assert calls == ["no sync"]  # the existing backoff holds the next one back


@pytest.mark.parametrize("why", ["scan", "unknown channel", "weak channel", "ringing"])
async def test_no_sync_is_left_alone_when_it_is_not_the_decoders_fault(zombie, why) -> None:
    s, ctx, host, dab, calls = zombie
    if why == "scan":
        import asyncio

        dab._scan_task = asyncio.create_task(asyncio.sleep(60))
    elif why == "unknown channel":
        dab.channel = "12C"  # nothing was ever found here
    elif why == "weak channel":
        with ctx.db.session() as db:
            for r in db.exec(__import__("sqlmodel").select(DabEnsembleRow)).all():
                r.snr = 4.0
                db.add(r)
            db.commit()
    else:
        from dawn_core.state.ui import RingingInfo

        ctx.store.state.alarms.ringing = RingingInfo(kind="alarm", label="Radio", started_at="x", source="dab:1002")
    t = 1000.0
    for k in range(6):
        await s.check(now=t + k * ctx.config.dab.no_sync_restart_s)
    assert calls == []
    if why == "scan":
        dab._scan_task.cancel()


async def test_three_restarts_without_sync_re_enumerate_the_stick_then_diagnostics_asks_for_a_replug(zombie) -> None:
    s, ctx, host, dab, calls = zombie
    t, n = 1000.0, ctx.config.dab.no_sync_restart_s
    step = n + s.cfg.max_backoff_s + 1
    for k in range(4):  # each round: the problem is seen afresh, then for no_sync_restart_s (and past the backoff)
        await s.check(now=t + k * step)
        await s.check(now=t + k * step + step - 1)
    assert calls == ["no sync"] * 3 and _usb_resets(host) == 1  # re-enumerated instead of a fourth restart
    reset_at = t + 3 * step + step - 1
    await s.check(now=reset_at + sup.USB_RESET_GRACE_S - 5)
    assert s.dab_stuck_since is None
    await s.check(now=reset_at + sup.USB_RESET_GRACE_S + 1)
    assert s.dab_stuck_since is not None and _usb_resets(host) == 1  # once only
    from dawn_core.diagnostics import dab as dabdiag

    ctx.store.state.system.throttled = 0x50000
    f = await dabdiag.collect(ctx, host)
    c = {x.id: x for x in dabdiag.checks(f)}
    assert c["dab.samples"].status == "fail" and "under-voltage" in c["dab.samples"].detail
    # sync comes back: all forgotten
    dab.live_sync = True
    await s.check(now=reset_at + 1000)
    assert s.dab_stuck_since is None


def test_the_reset_helper_and_its_sudo_line_take_no_arguments() -> None:
    deploy = Path(__file__).resolve().parents[2] / "deploy"
    sudoers = (deploy / "sudoers" / "dawn").read_text()
    assert '/usr/local/bin/dawn-usb-reset ""' in sudoers
    script = (deploy / "bin" / "dawn-usb-reset").read_text()
    assert "0bda:2838|0bda:2832" in script and '"$#" -ne 0' in script and "uhubctl" not in script.split("set -eu")[1]


# ---- the alarm's preparation and ring ----------------------------------------------------------------------------
async def test_preparation_restarts_a_silent_decoder_once_as_a_step_under_way(zombie, monkeypatch) -> None:
    s, ctx, host, dab, calls = zombie
    from dawn_core.dab.welle import MuxInfo, ServiceInfo

    monkeypatch.setattr(dab, "service_row", lambda sid: type("R", (), {"channel": "9A"})())
    mux = MuxInfo(channel="9A", sync=False, services=[ServiceInfo(sid="1002", label="triple j")])

    async def get_mux():
        return mux

    monkeypatch.setattr(dab.client, "mux", get_mux)
    clock = [5000.0]
    monkeypatch.setattr("dawn_core.dab.service.time.monotonic", lambda: clock[0])
    occ = "7@2026-11-23T06:30:00+11:00"
    assert await dab.prepare_for("1002", may_retune=True, occurrence=occ) == ("tuning", "waiting for sync on 9A")
    clock[0] += PREP_NOSYNC_S - 5
    assert (await dab.prepare_for("1002", may_retune=True, occurrence=occ))[0] == "tuning"
    assert calls == []
    clock[0] += 10  # two checks 30 s apart without sync
    assert await dab.prepare_for("1002", may_retune=True, occurrence=occ) == ("tuning", "restarting the decoder")
    assert calls == ["alarm"]

    async def gone() -> bool:
        return False

    monkeypatch.setattr(dab.client, "reachable", gone)  # coming back up
    clock[0] += 5
    assert await dab.prepare_for("1002", may_retune=True, occurrence=occ) == ("tuning", "restarting the decoder")
    assert calls == ["alarm"]

    async def back() -> bool:
        return True

    monkeypatch.setattr(dab.client, "reachable", back)
    clock[0] += 120  # still nothing a while after the one restart: now it is a failure (the ring starts on the chime)
    assert await dab.prepare_for("1002", may_retune=True, occurrence=occ) == ("fail", "no DAB signal on 9A")
    assert calls == ["alarm"]


async def test_no_sync_on_a_channel_never_scanned_fails_as_before(zombie, monkeypatch) -> None:
    s, ctx, host, dab, calls = zombie
    from dawn_core.dab.welle import MuxInfo

    dab.channel = "12C"
    monkeypatch.setattr(dab, "service_row", lambda sid: type("R", (), {"channel": "12C"})())

    async def get_mux():
        return MuxInfo(channel="12C", sync=False)

    monkeypatch.setattr(dab.client, "mux", get_mux)
    assert await dab.prepare_for("1002", may_retune=True, occurrence="x") == ("fail", "no DAB signal on 12C")
    assert calls == []


# ---- dropouts ----------------------------------------------------------------------------------------------------------
async def test_dropouts_are_logged_with_heat_and_power(zombie, monkeypatch) -> None:
    s, ctx, host, dab, calls = zombie
    ctx.store.state.system.cpu_temp_c = 71.5
    ctx.store.state.system.throttled = 0x50005
    ctx.store.state.system.throttle_flags = ["under-voltage", "throttled"]
    before = len([e for e in ctx.db.recent_events(50) if e["kind"] == "dab_drop"])
    now = time.monotonic()
    dab._drop_present = True
    dab.live_sync = True
    dab._watch_drops(now)
    dab.live_sync = False
    dab._watch_drops(now + 30)  # the loss is first seen
    dab._watch_drops(now + 85)
    dab._watch_drops(now + 91)  # 61 s without sync
    dab._watch_drops(now + 95)  # once per dropout
    ctx.store.state.dab.sdr_present = False
    dab._watch_drops(now + 100)
    ev = [e for e in ctx.db.recent_events(50) if e["kind"] == "dab_drop"]
    ev = ev[: len(ev) - before]  # newest first; only what this test caused
    assert [e["reason"] for e in ev] == ["the stick left the USB bus", "no sync for 60 s"]
    assert ev[1]["temp_c"] == 71.5 and ev[1]["throttled"] == "0x50005" and ev[1]["channel"] == "9A"


async def test_a_ring_restarts_an_answering_decoder_that_has_no_sync_once(zombie, monkeypatch) -> None:
    s, ctx, host, dab, calls = zombie
    from dawn_core.alarms import ringing
    from dawn_core.alarms.service import AlarmService

    monkeypatch.setattr(ringing, "DAB_NOSYNC_RESTART_S", ringing.DAB_RESTART_AFTER_S + 1)  # one second of waiting
    alarms: AlarmService = ctx.svc(AlarmService)
    await alarms.test_ring(source="chime:birds", label="Radio test", volume=20)
    ring = alarms.ring
    await ring._restart_dab_if_dead()
    assert calls == ["alarm"] and ring.restarted_dab
    await ring._restart_dab_if_dead()
    assert calls == ["alarm"]  # once per ring
    await alarms.stop_ringing()
