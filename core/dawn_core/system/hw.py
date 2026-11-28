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
    if ("waveshare" in cfg and "dsi" in cfg.lower()) or "vc4-kms-dsi-7inch" in cfg:
        return "waveshare_dsi"  # the 43H 4.3" panel uses the official 7" display's overlay
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


V4_MANUFACTURER = "RTLSDRBlog"  # what an RTL-SDR Blog V4 reports over USB (rtlsdr_get_usb_strings, sysfs)
V4_PRODUCT = "Blog V4"
USB_DEVICES = "/sys/bus/usb/devices"


def sdr_name(vidpid: str, manufacturer: str = "", product: str = "") -> str:
    """A readable name for the stick from its USB strings alone (opening it would race welle-cli for it)."""
    m, p = manufacturer.strip(), product.strip()
    if m == V4_MANUFACTURER and "V4" in p:
        return "RTL-SDR Blog V4 (R828D)"
    if m == V4_MANUFACTURER and "V3" in p:
        return "RTL-SDR Blog V3 (R820T2)"
    generic = _RTL_IDS.get(vidpid, "RTL2832U")
    label = " ".join(x for x in (m, p) if x)
    return f"{label} ({generic})" if label else generic


def is_blog_v4(u: dict[str, str]) -> bool:
    """The RTL-SDR Blog V4 says so in its EEPROM (manufacturer "RTLSDRBlog", product "Blog V4"). The rtl-sdr-blog
    driver goes by the same strings to switch on its V4 handling (R828D input switching), so a stick without them
    is treated as a generic one whatever is printed on its case. Same rule as sdr_name()."""
    return (u.get("manufacturer") or "").strip() == V4_MANUFACTURER and "V4" in (u.get("product") or "")


def sdr_usb(root: str = USB_DEVICES) -> dict[str, str] | None:
    """The first RTL-SDR stick's USB descriptors from sysfs, without opening it: vidpid, path, busnum, devnum,
    manufacturer, product, serial. busnum/devnum change whenever the stick is unplugged and plugged back."""
    for dev in sorted(glob.glob(os.path.join(root, "*"))):
        vid, pid = (_read(os.path.join(dev, "idVendor")) or "").strip(), (_read(os.path.join(dev, "idProduct")) or "").strip()
        if vid and pid and f"{vid}:{pid}" in _RTL_IDS:
            return {"vidpid": f"{vid}:{pid}", "path": os.path.basename(dev),
                    **{k: (_read(os.path.join(dev, k)) or "").strip() for k in ("busnum", "devnum", "manufacturer", "product", "serial")}}
    return None


def sdr_identity(root: str = USB_DEVICES) -> tuple[bool, str | None, str | None]:
    """(present, name, vid:pid) of the first RTL-SDR stick, from sysfs (idVendor, idProduct, manufacturer, product)."""
    u = sdr_usb(root)
    if u is None:
        return False, None, None
    return True, sdr_name(u["vidpid"], u.get("manufacturer", ""), u.get("product", "")), u["vidpid"]


def sdr_fingerprint(u: dict[str, str] | None) -> tuple[str, ...] | None:
    """What changes when a stick is swapped or replugged (a new bus address, or another stick)."""
    return None if u is None else (u["path"], u.get("busnum", ""), u.get("devnum", ""), u.get("serial", ""), u.get("product", ""))


def name_from_welle(hardware: str | None) -> str | None:
    """welle-cli's own name for the receiver (mux.json receiver.hardware.name, from rtlsdr_get_usb_strings)."""
    if not hardware:
        return None
    if V4_MANUFACTURER in hardware and "V4" in hardware:
        return "RTL-SDR Blog V4 (R828D)"
    return hardware.strip() or None


def sdr_on_usb() -> tuple[bool, str | None]:
    """(present, name) from the USB ids and strings alone, without opening the stick."""
    present, name, _ = sdr_identity()
    return present, name


def detect_sdr(probe_tuner: bool = False) -> tuple[bool, str | None, str | None]:
    """Presence and name from sysfs. `rtl_test -t` (which opens the stick to read the tuner) only with `probe_tuner`,
    i.e. when the decoder is not running: at boot welle-cli starts at the same moment and the two raced for the stick."""
    present, name, vidpid = sdr_identity()
    if not present or not probe_tuner:
        return present, name, vidpid
    try:
        out = subprocess.run(["rtl_test", "-t"], capture_output=True, text=True, timeout=6)
        m = re.search(r"Found (.+?) tuner", out.stdout + out.stderr)
        if m and "V4" not in (name or ""):
            name = f"{name} ({m.group(1).strip()} tuner)"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return present, name, vidpid


def _unit_active(unit: str) -> bool:
    """active or activating (a decoder starting at boot counts: it is about to open the stick)."""
    try:
        out = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() in ("active", "activating", "reloading")
    except (OSError, subprocess.TimeoutExpired):
        return True  # cannot tell: do not open the stick


# vcgencmd get_throttled bits: (mask, label, happening now?)
THROTTLE_FLAGS: list[tuple[int, str, bool]] = [
    (0x1, "under-voltage", True),
    (0x2, "frequency capped", True),
    (0x4, "throttled", True),
    (0x8, "soft temperature limit", True),
    (0x10000, "under-voltage since boot", False),
    (0x20000, "frequency capped since boot", False),
    (0x40000, "throttled since boot", False),
    (0x80000, "soft temperature limit since boot", False),
]


def parse_throttled(text: str) -> int | None:
    """`throttled=0x50005` -> 0x50005; None when vcgencmd gave nothing usable."""
    m = re.search(r"throttled=(0x[0-9a-fA-F]+|\d+)", text or "")
    return int(m.group(1), 0) if m else None


def throttle_flags(value: int | None) -> list[str]:
    if not value:
        return []
    return [label for mask, label, _ in THROTTLE_FLAGS if value & mask]


THROTTLED_SYSFS = "/sys/devices/platform/soc/soc:firmware/get_throttled"


def read_throttled_sysfs(path: str = THROTTLED_SYSFS) -> int | None:
    """The firmware's throttling flags straight from sysfs (hex, as vcgencmd prints them), without starting a process
    every few seconds. None when the file is not there (older kernels)."""
    raw = _read(path)
    if raw is None:
        return None
    try:
        return int(raw.strip(), 16)
    except ValueError:
        return None


def read_throttled(binary: str = "vcgencmd") -> int | None:
    try:
        out = subprocess.run([binary, "get_throttled"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_throttled(out.stdout) if out.returncode == 0 else None


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
    hw.sdr_present, hw.sdr_tuner, hw.sdr_vid_pid = detect_sdr(probe_tuner=not _unit_active("dawn-dab"))
    hw.i2c_buses = detect_i2c()
    if hw.panel == "hyperpixel4":
        hw.notes.append("HyperPixel 4 uses GPIO 0-25 for DPI; remap encoder/button pins in config.yaml")
    log.info(
        "hardware: model=%s panel=%s backlight=%s sdr=%s tuner=%s i2c=%s",
        hw.model, hw.panel, hw.backlight_sysfs, hw.sdr_present, hw.sdr_tuner, hw.i2c_buses,
    )
    return hw
