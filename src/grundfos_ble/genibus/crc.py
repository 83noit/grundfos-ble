"""CRC-16/CCITT as used by GENIbus.

Polynomial 0x1021, initial value 0xFFFF, result bit-inverted, transmitted high byte first.
It is computed over every byte from the length byte to the last APDU byte; the start
delimiter is excluded (GENIbus Protocol Specification, 2005, §3 and §4.4). Confirmed against
every frame captured from an ALPHA2 GO.
"""

from __future__ import annotations

_POLY = 0x1021


def _make_table() -> tuple[int, ...]:
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ _POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    return tuple(table)


_TABLE = _make_table()


def crc16(data: bytes | bytearray | memoryview) -> int:
    """Return the GENIbus CRC of ``data`` (length byte through last APDU byte)."""
    crc = 0xFFFF
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ _TABLE[((crc >> 8) ^ byte) & 0xFF]
    return crc ^ 0xFFFF


def verify_crc(frame: bytes | bytearray | memoryview) -> bool:
    """Return True when ``frame`` (SD through CRC) carries a matching CRC."""
    if len(frame) < 4:
        return False
    body = frame[1:-2]
    expected = int.from_bytes(frame[-2:], "big")
    return crc16(body) == expected
