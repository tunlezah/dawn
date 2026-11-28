"""Is a GPS receiver fitted? GPS is optional, so "none fitted" is a state of its own, not a fault.

A pure function over what the service saw, so every case is unit-tested without a receiver:

  off      time_sources.gps is disabled (or its source is "none"): nothing is looked at or shown
  present  a u-blox receiver on USB, /dev/gps0 (the udev rule's link), a device gpsd has activated, or NMEA the
           serial reader parsed in the last 30 s
  missing  not present now, but seen within forget_after_days: a receiver that was there and has gone (a warning)
  absent   none of that: never fitted, or forgotten

Not evidence: a bare /dev/ttyACM0 (any USB CDC device makes one) and a connection to gpsd (gpsd is socket-activated,
so it answers with no receiver at all).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

NMEA_FRESH_S = 30.0


@dataclass(frozen=True)
class PresenceInputs:
    enabled: bool
    usb_ublox: bool = False
    gps0: bool = False
    gpsd_device: bool = False  # gpsd is connected and reports an activated device
    nmea_age_s: float | None = None  # serial reader: seconds since the last valid sentence
    last_seen_at: datetime | None = None


def gps_presence(i: PresenceInputs, now: datetime, forget_after_days: int) -> str:
    if not i.enabled:
        return "off"
    if i.usb_ublox or i.gps0 or i.gpsd_device or (i.nmea_age_s is not None and i.nmea_age_s < NMEA_FRESH_S):
        return "present"
    if i.last_seen_at is not None and now - i.last_seen_at <= timedelta(days=forget_after_days):
        return "missing"
    return "absent"
