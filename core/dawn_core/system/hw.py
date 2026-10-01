"""Hardware detection: Pi model, display panel, SDR tuner, I2C, backlight."""

from __future__ import annotations

import glob
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("dawn.hw")


@dataclass
class HardwareInfo:
    model: str = "unknown"
    is_pi: bool = False
    pi_generation: int = 0  # 3, 4, 5, 2 (zero 2 w counts as 3-class CPU)
    low_power: bool = False  # Zero 2 W / 3B+
    panel: str = "none"  # waveshare_dsi | hyperpixel4 | hdmi | none
    backlight_sysfs: str | None = None
    sdr_present: bool = False
    sdr_tuner: str | None = None
    sdr_vid_pid: str | None = None
    i2c_buses: list[int] = field(default_factory=list)
    serial: str | None = None
    notes: list[str] = field(default_factory=list)


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore").strip("\x00\n ")
    except OSError:
        return None


def detect_model() -> tuple[str, bool, int, bool]:
    m = _read("/proc/device-tree/model") or ""
    is_pi = "Raspberry Pi" in m
    gen = 0
    low = False
    if is_pi:
        if "Pi 5" in m:
            gen = 5
        elif "Pi 4" in m or "Pi 400" in m:
            gen = 4
        elif "Zero 2" in m:
            gen = 3
            low = True
        elif "Pi 3" in m:
            gen = 3
            low = True
        elif "Pi 2" in m:
            gen = 2
            low = True
        else:
            low = True
    return (m or "generic-linux", is_pi, gen, low)


def detect_serial() -> str | None:
    cpuinfo = _read("/proc/cpuinfo") or ""
    m = re.search(r"Serial\s*:\s*([0-9a-f]+)", cpuinfo)
    return m.group(1) if m else None


def detect_backlight(override: str | None = None) -> str | None:
    if override and Path(override, "brightness").exists():
        return override
    for d in sorted(glob.glob("/sys/class/backlight/*")):
        if Path(d, "brightness").exists():
            return d
    return None


def detect_panel(config_panel: str = "auto") -> str:
    if config_panel != "auto":
        return config_panel
    # DSI panels register a backlight and a DRM connector named DSI-*
    connectors = glob.glob("/sys/class/drm/card*-*")
    names = [os.path.basename(c) for c in connectors]
    status = {}
    for c in connectors:
        st = _read(os.path.join(c, "status")) or "unknown"
        status[os.path.basename(c)] = st
    if any("DSI" in n and status.get(n) == "connected" for n in names):
        return "waveshare_dsi"
    if any("DPI" in n and status.get(n) == "connected" for n in names):
        return "hyperpixel4"
    cfg = _read("/boot/firmware/config.txt") or _read("/boot/config.txt") or ""
    if "hyperpixel4" in cfg:
        return "hyperpixel4"
    if "waveshare" in cfg and "dsi" in cfg.lower():
        return "waveshare_dsi"
    if any("HDMI" in n and status.get(n) == "connected" for n in names):
        return "hdmi"
    if detect_backlight():
        return "waveshare_dsi"
    return "hdmi" if names else "none"


_RTL_IDS = {
    "0bda:2838": "RTL2838 (generic RTL2832U)",
    "0bda:2832": "RTL2832U",
    "1d50:604b": "HackRF (unsupported)",
}


def detect_sdr() -> tuple[bool, str | None, str | None]:
    """Presence from USB IDs; tuner type from rtl_test when available."""
    present = False
    vidpid = None
    for dev in glob.glob("/sys/bus/usb/devices/*"):
        vid = _read(os.path.join(dev, "idVendor"))
        pid = _read(os.path.join(dev, "idProduct"))
        if vid and pid and f"{vid}:{pid}" in _RTL_IDS:
            present = True
            vidpid = f"{vid}:{pid}"
            break
    if not present:
        return False, None, None
    tuner = None
    try:
        out = subprocess.run(["rtl_test", "-t"], capture_output=True, text=True, timeout=6)
        txt = out.stdout + out.stderr
        m = re.search(r"Found (.+?) tuner", txt)
        if m:
            tuner = m.group(1).strip()
            if "R828D" in tuner or "RTL-SDR Blog V4" in txt:
                tuner += " (RTL-SDR Blog V4)" if "V4" in txt or "R828D" in tuner else ""
    except (OSError, subprocess.TimeoutExpired):
        tuner = _RTL_IDS.get(vidpid or "", "RTL2832U")
    return True, tuner, vidpid


def detect_i2c() -> list[int]:
    out = []
    for d in glob.glob("/dev/i2c-*"):
        try:
            out.append(int(d.rsplit("-", 1)[1]))
        except ValueError:
            pass
    return sorted(out)


def detect(config_panel: str = "auto", backlight_override: str | None = None) -> HardwareInfo:
    model, is_pi, gen, low = detect_model()
    hw = HardwareInfo(model=model, is_pi=is_pi, pi_generation=gen, low_power=low)
    hw.serial = detect_serial()
    hw.backlight_sysfs = detect_backlight(backlight_override)
    hw.panel = detect_panel(config_panel)
    hw.sdr_present, hw.sdr_tuner, hw.sdr_vid_pid = detect_sdr()
    hw.i2c_buses = detect_i2c()
    if hw.panel == "hyperpixel4":
        hw.notes.append("HyperPixel 4 uses GPIO 0-25 for DPI; remap encoder/button pins in config.yaml")
    log.info(
        "hardware: model=%s panel=%s backlight=%s sdr=%s tuner=%s i2c=%s",
        hw.model, hw.panel, hw.backlight_sysfs, hw.sdr_present, hw.sdr_tuner, hw.i2c_buses,
    )
    return hw
