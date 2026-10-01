"""chrony/ntpd SHM refclock writer using the libc SysV shm calls via ctypes.

struct shmTime (ntp.org / chrony refclock_shm):
  int mode; volatile int count; time_t clockTimeStampSec; int clockTimeStampUSec;
  time_t receiveTimeStampSec; int receiveTimeStampUSec; int leap; int precision;
  int nsamples; volatile int valid; unsigned clockTimeStampNSec;
  unsigned receiveTimeStampNSec; int dummy[8];
Mode 1 with the count protocol is used (chrony checks count before/after).
"""

from __future__ import annotations

import ctypes
import logging
import platform
import sys
from datetime import datetime

log = logging.getLogger("dawn-timed.shm")

NTPD_SHM_KEY = 0x4E545030
IPC_CREAT = 0o1000


def time_t_bytes(override: str = "auto") -> int:
    if override in ("4", "8"):
        return int(override)
    if sys.maxsize > 2**32:
        return 8
    # 32-bit: Debian 13 (trixie) and later armhf builds use 64-bit time_t
    try:
        rel = platform.freedesktop_os_release()
        if int(rel.get("VERSION_ID", "0").split(".")[0]) >= 13:
            return 8
    except (OSError, ValueError, AttributeError):
        pass
    return 4


def make_struct(tt_bytes: int) -> type[ctypes.Structure]:
    time_t = ctypes.c_int64 if tt_bytes == 8 else ctypes.c_int32

    class ShmTime(ctypes.Structure):
        _fields_ = [
            ("mode", ctypes.c_int),
            ("count", ctypes.c_int),
            ("clockTimeStampSec", time_t),
            ("clockTimeStampUSec", ctypes.c_int),
            ("receiveTimeStampSec", time_t),
            ("receiveTimeStampUSec", ctypes.c_int),
            ("leap", ctypes.c_int),
            ("precision", ctypes.c_int),
            ("nsamples", ctypes.c_int),
            ("valid", ctypes.c_int),
            ("clockTimeStampNSec", ctypes.c_uint),
            ("receiveTimeStampNSec", ctypes.c_uint),
            ("dummy", ctypes.c_int * 8),
        ]

    return ShmTime


class ChronyShm:
    def __init__(self, unit: int = 2, dry_run: bool = False, tt_bytes: str = "auto", perm: int = 0o666):
        self.unit = unit
        self.dry_run = dry_run
        self.struct = make_struct(time_t_bytes(tt_bytes))
        self.perm = perm
        self._seg: ctypes.Structure | None = None
        self.samples = 0

    def attach(self) -> None:
        if self.dry_run:
            log.info("dry run: not attaching SHM unit %d", self.unit)
            return
        libc = ctypes.CDLL(None, use_errno=True)
        libc.shmget.restype = ctypes.c_int
        libc.shmget.argtypes = [ctypes.c_int, ctypes.c_size_t, ctypes.c_int]
        libc.shmat.restype = ctypes.c_void_p
        libc.shmat.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        size = ctypes.sizeof(self.struct)
        shmid = libc.shmget(NTPD_SHM_KEY + self.unit, size, IPC_CREAT | self.perm)
        if shmid < 0:
            raise OSError(ctypes.get_errno(), f"shmget failed for unit {self.unit}")
        addr = libc.shmat(shmid, None, 0)
        if addr is None or addr == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_errno(), "shmat failed")
        self._seg = self.struct.from_address(addr)
        log.info("attached SHM unit %d (key 0x%x, %d bytes, time_t %d bytes)", self.unit, NTPD_SHM_KEY + self.unit, size, ctypes.sizeof(self.struct._fields_[2][1]))

    def write(self, clock: datetime, received: datetime, precision: int = -3, leap: int = 0) -> None:
        """clock: the reference (DAB) time; received: local system time when it was observed."""
        self.samples += 1
        if self.dry_run or self._seg is None:
            log.debug("sample clock=%s received=%s offset=%.3fs", clock.isoformat(), received.isoformat(), (clock - received).total_seconds())
            return
        seg = self._seg
        ct, rt = clock.timestamp(), received.timestamp()
        seg.valid = 0
        seg.count += 1
        seg.mode = 1
        seg.clockTimeStampSec = int(ct)
        seg.clockTimeStampUSec = int((ct % 1) * 1_000_000)
        seg.clockTimeStampNSec = int((ct % 1) * 1_000_000_000)
        seg.receiveTimeStampSec = int(rt)
        seg.receiveTimeStampUSec = int((rt % 1) * 1_000_000)
        seg.receiveTimeStampNSec = int((rt % 1) * 1_000_000_000)
        seg.leap = leap
        seg.precision = precision
        seg.nsamples = 0
        seg.count += 1
        seg.valid = 1
