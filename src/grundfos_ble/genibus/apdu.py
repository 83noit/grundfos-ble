"""GENIbus Application Protocol Data Units.

An APDU is a two-byte head followed by 0 to 63 data bytes. Byte 0 is the data class.
Byte 1 carries the operation specifier (request) or the acknowledgement (reply) in its
top two bits and the data length in the low six bits (GENIbus Protocol Specification,
2005, §4.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from grundfos_ble.exceptions import ProtocolError

MAX_APDU_DATA = 63
APDU_HEAD_SIZE = 2


class DataClass(IntEnum):
    """GENIbus data classes. 0-7 are in the 2005 spec; 8-13 from community decoders."""

    PROTOCOL_DATA = 0
    BUS_DATA = 1
    MEASURED_DATA = 2
    COMMANDS = 3
    CONFIGURATION_PARAMETERS = 4
    REFERENCE_VALUES = 5
    TEST_DATA = 6
    ASCII_STRINGS = 7
    MEMORY_BLOCKS = 8
    EMBEDDED_PDUS = 9
    DATA_OBJECTS = 10
    MEASURED_DATA_16 = 11
    CONFIGURATION_PARAMETERS_16 = 12
    REFERENCE_VALUES_16 = 13


class Operation(IntEnum):
    """Operation specifier of a request APDU (bits 7-6 of the head's second byte)."""

    GET = 0
    SET = 2
    INFO = 3


class Ack(IntEnum):
    """Acknowledgement of a reply APDU (bits 7-6 of the head's second byte)."""

    OK = 0
    CLASS_UNKNOWN = 1
    ID_UNKNOWN = 2
    ILLEGAL_OPERATION = 3


@dataclass(frozen=True, slots=True)
class Apdu:
    """One APDU. ``op`` is the operation on a request and the ACK on a reply."""

    data_class: int
    op: int
    data: bytes = b""

    def __post_init__(self) -> None:
        if not 0 <= self.data_class <= 0x0F:
            raise ValueError(f"data class must fit a nibble, got {self.data_class}")
        if not 0 <= self.op <= 3:
            raise ValueError(f"operation/ack must be 0-3, got {self.op}")
        if len(self.data) > MAX_APDU_DATA:
            raise ValueError(f"APDU data is limited to {MAX_APDU_DATA} bytes, got {len(self.data)}")

    @property
    def ack(self) -> Ack:
        """The acknowledgement code, meaningful on replies."""
        return Ack(self.op)

    def encode(self) -> bytes:
        """Serialise head and data."""
        return bytes((self.data_class, (self.op << 6) | len(self.data))) + self.data

    @classmethod
    def decode(cls, buf: bytes, offset: int = 0) -> tuple[Apdu, int]:
        """Parse one APDU at ``offset``; return it and the offset of the next one."""
        if len(buf) - offset < APDU_HEAD_SIZE:
            raise ProtocolError("truncated APDU head", bytes(buf))
        data_class = buf[offset]
        if data_class > 0x0F:
            raise ProtocolError(f"invalid APDU class byte 0x{data_class:02x}", bytes(buf))
        head = buf[offset + 1]
        length = head & 0x3F
        start = offset + APDU_HEAD_SIZE
        end = start + length
        if end > len(buf):
            raise ProtocolError(
                f"APDU claims {length} data bytes but only {len(buf) - start} remain", bytes(buf)
            )
        return cls(data_class, head >> 6, bytes(buf[start:end])), end
