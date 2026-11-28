"""RTL-SDR stick identification and hot plug (the RTL-SDR Blog V4 against a generic TV stick)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dawn_core.app import create_app
from dawn_core.config import ConfigManager
from dawn_core.dab.service import DabService
from dawn_core.system import hw
from dawn_core.system import sysinfo as sysinfo_mod
from dawn_core.system.sysinfo import SysInfoService

V4 = {"idVendor": "0bda", "idProduct": "2838", "busnum": "1", "devnum": "5", "manufacturer": "RTLSDRBlog", "product": "Blog V4",
      "serial": "00000001"}
GENERIC = {**V4, "devnum": "3", "manufacturer": "Realtek", "product": "RTL2838UHIDIR"}

# `rtl_test -t` with a V4 (rtl-sdr-blog driver)
RTL_TEST_V4 = """Found 1 device(s):
  0:  RTLSDRBlog, Blog V4, SN: 00000001

Using device 0: Generic RTL2832U OEM
Found Rafael Micro R828D tuner
RTL-SDR Blog V4 Detected
Supported gain values (29): 0.0 0.9 1.4 2.7 3.7 7.7 8.7 12.5 14.4 15.7 16.6 19.7 20.7 22.9 25.4 28.0 29.7 32.8 33.8 36.4 37.2 38.6 40.2 42.1 43.4 43.9 44.5 48.0 49.6
"""
RTL_TEST_GENERIC = "Found 1 device(s):\n  0:  Realtek, RTL2838UHIDIR, SN: 00000001\n\nFound Rafael Micro R820T tuner\n"
RTL_TEST_BUSY = "Found 1 device(s):\n  0:  RTLSDRBlog, Blog V4, SN: 00000001\n\nusb_claim_interface error -6\nFailed to open rtlsdr device #0.\n"


def _sysfs(root: Path, devices: dict[str, dict[str, str]]) -> str:
    for name, attrs in devices.items():
        d = root / name
        d.mkdir(parents=True)
        for k, v in attrs.items():
            (d / k).write_text(v + "\n")
    return str(root)


def test_sdr_usb_reads_the_descriptors(tmp_path: Path) -> None:
    root = _sysfs(tmp_path, {"1-1.4": {"idVendor": "1546", "idProduct": "01a7", "product": "u-blox 7"}, "1-1.2": V4})
    u = hw.sdr_usb(root)
    assert u is not None and u["vidpid"] == "0bda:2838" and u["path"] == "1-1.2" and u["product"] == "Blog V4" and u["devnum"] == "5"
    assert hw.is_blog_v4(u) and hw.sdr_name(u["vidpid"], u["manufacturer"], u["product"]) == "RTL-SDR Blog V4 (R828D)"
    assert hw.sdr_identity(root) == (True, "RTL-SDR Blog V4 (R828D)", "0bda:2838")  # the same stick, the same name
    assert hw.sdr_usb(_sysfs(tmp_path / "none", {"1-1.4": {"idVendor": "1546", "idProduct": "01a7"}})) is None


def test_generic_stick_is_not_a_v4(tmp_path: Path) -> None:
    u = hw.sdr_usb(_sysfs(tmp_path, {"1-1.2": GENERIC}))
    assert u is not None and not hw.is_blog_v4(u)
    assert hw.sdr_name(u["vidpid"], u["manufacturer"], u["product"]) == "Realtek RTL2838UHIDIR (RTL2838 (generic RTL2832U))"


def test_v4_rule_is_the_drivers() -> None:
    # the rtl-sdr-blog driver switches on its V4 handling only for manufacturer "RTLSDRBlog" and a V4 product string
    assert hw.is_blog_v4({"manufacturer": "RTLSDRBlog", "product": "Blog V4"})
    assert not hw.is_blog_v4({"manufacturer": "RTLSDRBlog", "product": "Blog V3"})
    assert not hw.is_blog_v4({"manufacturer": "Realtek", "product": "Blog V4 clone"})
    assert not hw.is_blog_v4({})


def test_detect_sdr_with_rtl_test(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    root = _sysfs(tmp_path, {"1-1.2": GENERIC})
    real = hw.sdr_identity
    monkeypatch.setattr(hw, "sdr_identity", lambda: real(root))
    for out, want in ((RTL_TEST_GENERIC, "Realtek RTL2838UHIDIR (RTL2838 (generic RTL2832U)) (Rafael Micro R820T tuner)"),
                      (RTL_TEST_BUSY, "Realtek RTL2838UHIDIR (RTL2838 (generic RTL2832U))")):  # welle-cli has it: the descriptors' name
        monkeypatch.setattr(subprocess, "run", lambda *a, _o=out, **k: subprocess.CompletedProcess(a, 0, _o, ""))
        assert hw.detect_sdr(probe_tuner=True)[1] == want
    v4 = _sysfs(tmp_path / "v4", {"1-1.2": V4})
    monkeypatch.setattr(hw, "sdr_identity", lambda: real(v4))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, RTL_TEST_V4, ""))
    assert hw.detect_sdr(probe_tuner=True)[1] == "RTL-SDR Blog V4 (R828D)"  # the descriptors already say it all


def test_identity_changes_on_swap_and_replug() -> None:
    u = {"path": "1-1.2", **V4}
    assert hw.sdr_fingerprint(u) == hw.sdr_fingerprint(dict(u))
    assert hw.sdr_fingerprint(u) != hw.sdr_fingerprint({**u, "devnum": "6"})  # replugged into the same port
    assert hw.sdr_fingerprint(u) != hw.sdr_fingerprint({"path": "1-1.2", **GENERIC})  # another stick
    assert hw.sdr_fingerprint(None) is None


@pytest.fixture()
async def app(tmp_config):
    app = create_app(ConfigManager(tmp_config, poll_s=10))
    async with app.router.lifespan_context(app):
        yield app


async def test_swapping_in_a_v4_restarts_the_decoder(app, monkeypatch) -> None:
    ctx = app.state.ctx
    svc: SysInfoService = ctx.svc(SysInfoService)
    restarts: list[str] = []

    async def restart(self, force: bool = False, reason: str = "unreachable") -> bool:
        restarts.append(reason)
        return True

    monkeypatch.setattr(DabService, "restart_welle", restart)
    monkeypatch.setattr(sysinfo_mod, "REPLUG_SETTLE_S", 0.0)
    old = {"vidpid": "0bda:2838", "path": "1-1.2", **GENERIC}
    new = {"vidpid": "0bda:2838", "path": "1-1.2", **V4}
    svc._sdr_id = hw.sdr_fingerprint(old)
    st = ctx.store.state.system

    seq = iter([old, None, new, new])
    monkeypatch.setattr(hw, "sdr_usb", lambda root=hw.USB_DEVICES: next(seq))
    await svc._check_sdr(st)  # unchanged
    assert restarts == [] and svc._sdr_restart is None
    await svc._check_sdr(st)  # old stick pulled out
    assert st.sdr_present is False and ctx.store.state.dab.sdr_present is False and restarts == []
    await svc._check_sdr(st)  # V4 plugged in
    assert st.sdr_present and st.sdr_tuner == "RTL-SDR Blog V4 (R828D)" and ctx.store.state.dab.tuner == st.sdr_tuner
    await svc._sdr_restart
    assert restarts == ["replugged"]
    await svc._check_sdr(st)  # still the same V4: nothing more
    assert restarts == ["replugged"]


async def test_a_swap_leaves_a_cool_mode_decoder_stopped(app, monkeypatch) -> None:
    ctx = app.state.ctx
    svc: SysInfoService = ctx.svc(SysInfoService)
    restarts: list[str] = []

    async def restart(self, force: bool = False, reason: str = "unreachable") -> bool:
        restarts.append(reason)
        return True

    monkeypatch.setattr(DabService, "restart_welle", restart)
    monkeypatch.setattr(sysinfo_mod, "REPLUG_SETTLE_S", 0.0)
    ctx.svc(DabService).paused = "cool mode"
    svc._sdr_id = None
    monkeypatch.setattr(hw, "sdr_usb", lambda root=hw.USB_DEVICES: {"vidpid": "0bda:2838", "path": "1-1.2", **V4})
    await svc._check_sdr(ctx.store.state.system)
    await svc._sdr_restart
    assert restarts == [] and ctx.svc(DabService).paused == "cool mode"
