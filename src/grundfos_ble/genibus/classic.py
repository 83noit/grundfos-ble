"""Classic GENIbus data classes: 2 (measured), 3 (commands), 4/5 (parameters), 7 (strings).

Request data is a list of one-byte IDs (GET, INFO) or ID/value pairs (SET). A GET reply
carries one byte per ID in request order; an INFO reply describes each ID's scaling; a
class-7 reply carries a NUL-terminated ASCII string. All from the GENIbus Protocol
Specification (2005), §4.2-§4.5 and §6.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import IntEnum

from grundfos_ble.exceptions import NackError, ProtocolError
from grundfos_ble.genibus.apdu import Ack, Apdu, DataClass, Operation

VALUE_NOT_AVAILABLE = 0xFF
"""An 8-bit measured value of 255 means "not available" unless INFO says VI=1."""


def get_request(data_class: int, ids: Iterable[int]) -> Apdu:
    """GET the given IDs of a classic data class."""
    return Apdu(data_class, Operation.GET, bytes(ids))


def info_request(data_class: int, ids: Iterable[int]) -> Apdu:
    """INFO (scaling description) for the given IDs."""
    return Apdu(data_class, Operation.INFO, bytes(ids))


def set_request(data_class: int, pairs: Iterable[tuple[int, int]]) -> Apdu:
    """SET ID/value pairs of a classic data class."""
    data = bytearray()
    for item_id, value in pairs:
        data += bytes((item_id, value))
    return Apdu(data_class, Operation.SET, bytes(data))


def command(command_id: int) -> Apdu:
    """Build a class-3 command (SET with the command ID and no value)."""
    return Apdu(DataClass.COMMANDS, Operation.SET, bytes((command_id,)))


def _check(apdu: Apdu, data_class: int, raw: bytes | None) -> None:
    if apdu.data_class != data_class:
        raise ProtocolError(f"expected class {data_class}, got class {apdu.data_class}", raw)
    if apdu.ack != Ack.OK:
        raise NackError(apdu.ack, apdu.data_class, apdu.data, raw)


def parse_get_reply(
    apdu: Apdu, data_class: int, ids: Sequence[int], raw: bytes | None = None
) -> dict[int, int]:
    """Map each requested ID to its one-byte value."""
    _check(apdu, data_class, raw)
    if len(apdu.data) != len(ids):
        raise ProtocolError(f"asked for {len(ids)} values, pump sent {len(apdu.data)}", raw)
    return dict(zip(ids, apdu.data, strict=True))


def parse_command_reply(apdu: Apdu, raw: bytes | None = None) -> None:
    """Check a class-3 command acknowledgement; raise NackError if rejected."""
    _check(apdu, DataClass.COMMANDS, raw)


def parse_string_reply(apdu: Apdu, raw: bytes | None = None) -> str:
    """Decode a class-7 string reply (ASCII, NUL-terminated and NUL-padded)."""
    _check(apdu, DataClass.ASCII_STRINGS, raw)
    text = apdu.data.split(b"\x00", 1)[0]
    return text.decode("ascii", errors="replace")


class ScaleInfo(IntEnum):
    """The SIF bits of an INFO head byte."""

    NONE = 0
    BITWISE = 1
    SCALED = 2
    EXTENDED = 3


@dataclass(frozen=True, slots=True)
class InfoField:
    """Scaling information for one classic data item (INFO reply)."""

    head: int
    unit_index: int | None = None
    zero: int | None = None
    range: int | None = None

    @property
    def value_255_legal(self) -> bool:
        """VI bit: when set, 255 is a legal value rather than "not available"."""
        return bool(self.head & 0x20)

    @property
    def low_order_byte(self) -> bool:
        """BO bit: this item is the low byte of a multi-byte value."""
        return bool(self.head & 0x10)

    @property
    def scale_info(self) -> ScaleInfo:
        """How the value is scaled."""
        return ScaleInfo(self.head & 0x03)

    @property
    def zero_negative(self) -> bool:
        """SZ bit of the UNIT byte: the zero point is negative."""
        return self.unit_index is not None and bool(self.unit_index & 0x80)

    @property
    def unit(self) -> tuple[str, float] | None:
        """(unit symbol, factor) from the Grundfos unit table."""
        if self.unit_index is None:
            return None
        return UNIT_TABLE.get(self.unit_index & 0x7F)

    def scale(self, value: int) -> float | None:
        """Apply 8-bit standard scaling to ``value``; None when not available."""
        if value == VALUE_NOT_AVAILABLE and not self.value_255_legal:
            return None
        if self.scale_info != ScaleInfo.SCALED or self.unit is None:
            return float(value)
        zero = -(self.zero or 0) if self.zero_negative else (self.zero or 0)
        return (zero + value * (self.range or 0) / 254) * self.unit[1]


def parse_info_reply(
    apdu: Apdu, data_class: int, ids: Sequence[int], raw: bytes | None = None
) -> dict[int, InfoField]:
    """Decode an INFO reply: one byte per unscaled item, four per scaled one."""
    _check(apdu, data_class, raw)
    result: dict[int, InfoField] = {}
    offset = 0
    data = apdu.data
    for item_id in ids:
        if offset >= len(data):
            raise ProtocolError("INFO reply shorter than the number of IDs", raw)
        head = data[offset]
        if head & 0x03 in (ScaleInfo.SCALED, ScaleInfo.EXTENDED):
            if offset + 4 > len(data):
                raise ProtocolError("truncated scaled INFO entry", raw)
            result[item_id] = InfoField(head, data[offset + 1], data[offset + 2], data[offset + 3])
            offset += 4
        else:
            result[item_id] = InfoField(head)
            offset += 1
    return result


UNIT_TABLE: dict[int, tuple[str, float]] = {
    # Grundfos unit table (GENIbus Protocol Specification 2005, Table 3): index -> (unit, factor).
    1: ("A", 0.1),
    2: ("A", 5),
    3: ("V", 0.1),
    4: ("V", 1),
    5: ("V", 5),
    6: ("Ω", 1),
    7: ("W", 1),
    8: ("W", 10),
    9: ("W", 100),
    10: ("µF", 1),
    11: ("Hz", 0.5),
    12: ("%", 0.1),
    13: ("h", 2),
    14: ("s", 30),
    15: ("µA", 1),
    16: ("Hz", 1),
    17: ("Hz", 2.5),
    18: ("rpm", 12),
    19: ("rpm", 100),
    20: ("°C", 0.1),
    21: ("°C", 1),
    22: ("m³/h", 0.1),
    23: ("m³/h", 1),
    24: ("m", 0.1),
    25: ("m", 1),
    26: ("m", 10),
    27: ("bar", 0.01),
    28: ("bar", 0.1),
    29: ("bar", 1),
    30: ("%", 1),
    31: ("kWh", 1),
    32: ("kWh", 10),
    33: ("kWh", 100),
    34: ("rad/s", 2),
    35: ("h", 1),
    36: ("min", 2),
    37: ("s", 1),
    38: ("Hz", 2),
    39: ("h", 1024),
    40: ("kWh", 512),
    41: ("m³/h", 5),
    42: ("A", 0.2),
    43: ("kΩ", 10),
    44: ("kW", 1),
    45: ("kW", 10),
    46: ("MWh", 1),
    47: ("MWh", 10),
    48: ("MWh", 100),
    49: ("°", 1),
    50: ("", 1),
    51: ("bar", 0.001),
    52: ("l/s", 1),
    53: ("m³/s", 1),
    54: ("gpm", 1),
    55: ("psi", 1),
    56: ("ft", 1),
    57: ("°F", 1),
    58: ("gpm", 10),
    59: ("ft", 10),
    60: ("psi", 10),
    61: ("kPa", 1),
    62: ("A", 0.5),
    63: ("l/s", 0.1),
    64: ("m³", 0.1),
    65: ("m³", 1000),
    66: ("kWh/m³", 10),
    67: ("m³", 256),
    68: ("m²", 1),
    69: ("ml/h", 0.1),
    70: ("ml", 0.1),
    71: ("nl", 1),
    72: ("min", 1024),
    73: ("l/h", 0.5),
    74: ("Wh/m³", 1),
    75: ("Nm", 1),
    76: ("%", 10),
    77: ("", 0.01),
    78: ("s", 10),
    79: ("s", 0.1),
    80: ("min", 1),
    81: ("h", 100),
    82: ("l/min", 0.1),
    83: ("m", 0.01),
    84: ("K", 0.01),
    85: ("kWh", 2),
    86: ("m³", 1),
    87: ("Ws", 1),
    88: ("ml", 1),
    89: ("kΩ", 100),
    90: ("mm/s", 1),
    91: ("m", 0.0001),
    92: ("m³/h", 10),
    93: ("m³/h", 100),
    94: ("Wh", 1),
    95: ("m³/h", 0.01),
}
