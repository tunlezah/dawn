"""Minimal FIC/FIB encoder: FIG 0/10 (date and time) inside valid FIBs.

Used by the fake welle-cli `/fic` endpoint so dawn-timed's fallback parser can be
exercised. See ETSI EN 300 401, 5.2.1 (FIB) and 8.1.3.1 (FIG 0/10).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

MJD_EPOCH = date(1858, 11, 17)


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc ^ 0xFFFF  # FIB CRC is transmitted inverted


def fig_0_10(now: datetime) -> bytes:
    now = now.astimezone(timezone.utc)
    mjd = (now.date() - MJD_EPOCH).days
    ms = now.microsecond // 1000
    # Rfu(1) MJD(17) LSI(1) ConfInd(1) UTCflag(1) hours(5) minutes(6) seconds(6) ms(10) = 48 bits
    bits = 0
    bits = (bits << 1) | 0
    bits = (bits << 17) | (mjd & 0x1FFFF)
    bits = (bits << 1) | 0
    bits = (bits << 1) | 0
    bits = (bits << 1) | 1
    bits = (bits << 5) | now.hour
    bits = (bits << 6) | now.minute
    bits = (bits << 6) | now.second
    bits = (bits << 10) | ms
    body = bits.to_bytes(6, "big")
    fig0_header = 0x0A  # C/N=0 OE=0 P/D=0 Ext=10
    data = bytes([fig0_header]) + body
    return bytes([(0 << 5) | len(data)]) + data  # FIG type 0, length 7


def fib(figs: bytes) -> bytes:
    if len(figs) > 30:
        raise ValueError("FIB payload too long")
    payload = figs
    if len(payload) < 30:
        payload += b"\xff"  # end marker
    payload = payload.ljust(30, b"\x00")
    return payload + crc16_ccitt(payload).to_bytes(2, "big")


def fic_frame(now: datetime | None = None) -> bytes:
    """Three FIBs (one DAB frame worth of FIC in mode I), the first carrying FIG 0/10."""
    now = now or datetime.now(timezone.utc)
    return fib(fig_0_10(now)) + fib(b"") + fib(b"")
