"""NMEA 0183 sentence generation for the GPS simulator (u-blox 7 style)."""

from __future__ import annotations

import math
from datetime import UTC, datetime


def checksum(body: str) -> str:
    c = 0
    for ch in body:
        c ^= ord(ch)
    return f"{c:02X}"


def sentence(body: str) -> str:
    return f"${body}*{checksum(body)}\r\n"


def _lat(lat: float) -> tuple[str, str]:
    h = "N" if lat >= 0 else "S"
    lat = abs(lat)
    d = int(lat)
    m = (lat - d) * 60
    return f"{d:02d}{m:07.4f}", h


def _lon(lon: float) -> tuple[str, str]:
    h = "E" if lon >= 0 else "W"
    lon = abs(lon)
    d = int(lon)
    m = (lon - d) * 60
    return f"{d:03d}{m:07.4f}", h


def gga(now: datetime, lat: float, lon: float, fix: bool, sats: int, alt: float = 20.0) -> str:
    t = now.strftime("%H%M%S.00")
    if fix:
        la, lah = _lat(lat)
        lo, loh = _lon(lon)
        body = f"GPGGA,{t},{la},{lah},{lo},{loh},1,{sats:02d},1.2,{alt:.1f},M,19.6,M,,"
    else:
        body = f"GPGGA,{t},,,,,0,00,99.99,,,,,,"
    return sentence(body)


def rmc(now: datetime, lat: float, lon: float, fix: bool) -> str:
    t = now.strftime("%H%M%S.00")
    d = now.strftime("%d%m%y")
    if fix:
        la, lah = _lat(lat)
        lo, loh = _lon(lon)
        body = f"GPRMC,{t},A,{la},{lah},{lo},{loh},0.05,0.00,{d},,,A"
    else:
        body = f"GPRMC,{t},V,,,,,,,{d},,,N"
    return sentence(body)


def gsa(fix: bool, sats: int) -> str:
    prns = ",".join(f"{p:02d}" for p in range(1, sats + 1)) if fix else ""
    prns = (prns + "," * 12)[: 12 * 3 - 1] if fix else ",,,,,,,,,,,"
    mode = "3" if fix else "1"
    return sentence(f"GPGSA,A,{mode},{prns},2.1,1.2,1.7")


def gsv(sats: int) -> list[str]:
    total = max(sats, 4)
    msgs = math.ceil(total / 4)
    out = []
    for i in range(msgs):
        parts = [f"GPGSV,{msgs},{i + 1},{total:02d}"]
        for j in range(4):
            prn = i * 4 + j + 1
            if prn > total:
                break
            parts.append(f"{prn:02d},{20 + (prn * 7) % 60:02d},{(prn * 37) % 360:03d},{30 + prn % 15:02d}")
        out.append(sentence(",".join(parts)))
    return out


def burst(now: datetime | None, lat: float, lon: float, fix: bool, sats: int) -> str:
    now = now or datetime.now(UTC)
    return "".join([rmc(now, lat, lon, fix), gga(now, lat, lon, fix, sats), gsa(fix, sats), *gsv(sats)])
