"""Parse FIG 0/10 (date and time) out of raw FIBs from welle-cli's /fic endpoint.

ETSI EN 300 401: a FIB is 30 bytes of FIGs + 2-byte CRC (CRC-16-CCITT, inverted).
FIG 0/10: Rfu(1) MJD(17) LSI(1) ConfInd(1) UTC(1) then hours(5) minutes(6)
[seconds(6) milliseconds(10) when UTC flag = 1].
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

MJD_EPOCH = date(1858, 11, 17)
FIB_LEN = 32


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc ^ 0xFFFF


def mjd_to_date(mjd: int) -> date:
    return MJD_EPOCH + timedelta(days=mjd)


def parse_fig_0_10(data: bytes) -> tuple[datetime, bool] | None:
    """`data` is the type-0 field after the FIG 0 header byte. Returns (utc datetime, has_ms)."""
    if len(data) < 4:
        return None
    bits = int.from_bytes(data[:6] if len(data) >= 6 else data[:4] + b"\x00\x00", "big")
    nbits = 48
    mjd = (bits >> (nbits - 1 - 17)) & 0x1FFFF
    utc_flag = (bits >> (nbits - 21)) & 1
    rest = bits & ((1 << (nbits - 21)) - 1)  # 27 bits remain
    if utc_flag:
        if len(data) < 6:
            return None
        hours = (rest >> 22) & 0x1F
        minutes = (rest >> 16) & 0x3F
        seconds = (rest >> 10) & 0x3F
        ms = rest & 0x3FF
        has_ms = True
    else:
        hours = (rest >> 22) & 0x1F
        minutes = (rest >> 16) & 0x3F
        seconds, ms, has_ms = 0, 0, False
    if hours > 23 or minutes > 59 or seconds > 60 or ms > 999:
        return None
    try:
        d = mjd_to_date(mjd)
        return datetime(d.year, d.month, d.day, hours, minutes, min(seconds, 59), ms * 1000, tzinfo=UTC), has_ms
    except (ValueError, OverflowError):
        return None


def parse_fibs(data: bytes, verify_crc: bool = True) -> list[tuple[datetime, bool]]:
    """Walk 32-byte FIBs and return every FIG 0/10 time found."""
    out: list[tuple[datetime, bool]] = []
    for off in range(0, len(data) - FIB_LEN + 1, FIB_LEN):
        fib = data[off : off + FIB_LEN]
        payload, crc = fib[:30], int.from_bytes(fib[30:], "big")
        if verify_crc and crc16_ccitt(payload) != crc:
            continue
        i = 0
        while i < 30:
            hdr = payload[i]
            if hdr == 0xFF:
                break
            ftype, flen = hdr >> 5, hdr & 0x1F
            body = payload[i + 1 : i + 1 + flen]
            if ftype == 0 and body:
                ext = body[0] & 0x1F
                if ext == 10:
                    parsed = parse_fig_0_10(body[1:])
                    if parsed:
                        out.append(parsed)
            i += 1 + flen
    return out
