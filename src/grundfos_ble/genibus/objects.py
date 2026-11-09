"""Class-10 data objects, the format Grundfos' Bluetooth pumps use for most values.

Not part of the 2005 specification; reverse-engineered from Grundfos GO traffic on an
ALPHA2 GO and confirmed on a real pump. A GET names objects by ``(id, sub-id)``; the reply
carries, per object, a seven-byte header ``status type(2) version size(3)`` followed by
``size`` data bytes. Replies name the object *type*, not the id asked for, so callers match
replies to requests by order. A SET repeats the header after the reference and is answered
with a single status byte.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum

from grundfos_ble.exceptions import NackError, ObjectError, ProtocolError
from grundfos_ble.genibus.apdu import Ack, Apdu, DataClass, Operation

OBJECT_HEADER_SIZE = 7
OBJECT_REF_SIZE = 3
SET_HEADER_SIZE = 6
MAX_SET_DATA = 63 - OBJECT_REF_SIZE - SET_HEADER_SIZE
"""A SET APDU spends 9 of its 63 data bytes on reference and header."""

NOT_AVAILABLE = b"\x7f\xff\xff\xff"
"""A float slot the pump does not provide (also used in SETs as "keep the stored value")."""
NAN = b"\xff\xc0\x00\x00"
"""A float slot the pump provides but cannot measure right now."""


class ObjectStatus(IntEnum):
    """Status byte of an object header or a SET acknowledgement."""

    OK = 0
    BUSY = 2
    OPERATION_FAILED = 4


@dataclass(frozen=True, slots=True, order=True)
class ObjectRef:
    """A class-10 object reference: id and 16-bit sub-id."""

    id: int
    sub: int

    def __post_init__(self) -> None:
        if not 0 <= self.id <= 0xFF or not 0 <= self.sub <= 0xFFFF:
            raise ValueError(f"invalid object reference {self.id}/{self.sub}")

    def __str__(self) -> str:
        return f"{self.id}/{self.sub}"

    def to_bytes(self) -> bytes:
        """Serialise as ``id sub-hi sub-lo``."""
        return bytes((self.id,)) + self.sub.to_bytes(2, "big")


@dataclass(frozen=True, slots=True)
class ObjectHeader:
    """The seven-byte header in front of object data."""

    status: int
    type: int
    version: int
    size: int

    def encode(self) -> bytes:
        """Serialise."""
        return (
            bytes((self.status,))
            + self.type.to_bytes(2, "big")
            + bytes((self.version,))
            + (self.size.to_bytes(3, "big"))
        )

    @classmethod
    def decode(cls, buf: bytes, offset: int = 0) -> ObjectHeader:
        """Parse a header at ``offset``."""
        if len(buf) - offset < OBJECT_HEADER_SIZE:
            raise ProtocolError("truncated object header", bytes(buf))
        return cls(
            status=buf[offset],
            type=int.from_bytes(buf[offset + 1 : offset + 3], "big"),
            version=buf[offset + 3],
            size=int.from_bytes(buf[offset + 4 : offset + 7], "big"),
        )


@dataclass(frozen=True, slots=True)
class ObjectReply:
    """One object from a GET reply."""

    header: ObjectHeader
    data: bytes

    @property
    def ok(self) -> bool:
        """True when the pump reported status OK."""
        return self.header.status == ObjectStatus.OK


def get_request(*refs: ObjectRef) -> Apdu:
    """Build a class-10 GET for one or more objects."""
    if not refs:
        raise ValueError("at least one object reference is required")
    return Apdu(DataClass.DATA_OBJECTS, Operation.GET, b"".join(r.to_bytes() for r in refs))


def parse_get_reply(apdu: Apdu, raw: bytes | None = None) -> list[ObjectReply]:
    """Split a class-10 GET reply into its objects.

    Raises :class:`NackError` on a non-OK acknowledgement. A non-zero object status is kept
    in the returned header (the data after it is of unknown layout, so parsing stops there).
    """
    if apdu.data_class != DataClass.DATA_OBJECTS:
        raise ProtocolError(f"expected a class-10 reply, got class {apdu.data_class}", raw)
    if apdu.ack != Ack.OK:
        raise NackError(apdu.ack, apdu.data_class, apdu.data, raw)
    replies: list[ObjectReply] = []
    offset = 0
    data = apdu.data
    while offset < len(data):
        if len(data) - offset < OBJECT_HEADER_SIZE and data[offset] != ObjectStatus.OK:
            # A failure may come back as a bare status byte with no type/version/size.
            replies.append(ObjectReply(ObjectHeader(data[offset], 0, 0, 0), data[offset + 1 :]))
            break
        header = ObjectHeader.decode(data, offset)
        offset += OBJECT_HEADER_SIZE
        if header.status != ObjectStatus.OK:
            replies.append(ObjectReply(header, data[offset:]))
            break
        end = offset + header.size
        if end > len(data):
            raise ProtocolError(
                f"object type {header.type} claims {header.size} bytes, {len(data) - offset} left",
                raw,
            )
        replies.append(ObjectReply(header, data[offset:end]))
        offset = end
    return replies


def expect_object(
    replies: Iterable[ObjectReply],
    expected_type: int,
    *,
    min_size: int = 0,
    raw: bytes | None = None,
) -> ObjectReply:
    """Return the first reply, checking its type and size; raise on a bad status."""
    for reply in replies:
        if not reply.ok:
            raise ObjectError(reply.header.status, raw)
        if reply.header.type != expected_type:
            raise ProtocolError(
                f"expected object type {expected_type}, pump sent type {reply.header.type}", raw
            )
        if len(reply.data) < min_size:
            raise ProtocolError(
                f"object type {expected_type} has {len(reply.data)} bytes, need {min_size}", raw
            )
        return reply
    raise ProtocolError("reply carried no object", raw)


def set_request(ref: ObjectRef, object_type: int, version: int, data: bytes) -> Apdu:
    """Build a class-10 SET writing ``data`` to ``ref``."""
    if len(data) > MAX_SET_DATA:
        raise ValueError(f"object SET data is limited to {MAX_SET_DATA} bytes, got {len(data)}")
    # The SET header is the reply header without its status byte: type(2) version(1) size(3).
    header = object_type.to_bytes(2, "big") + bytes((version,)) + len(data).to_bytes(3, "big")
    return Apdu(DataClass.DATA_OBJECTS, Operation.SET, ref.to_bytes() + header + data)


def parse_set_reply(apdu: Apdu, raw: bytes | None = None) -> ObjectStatus:
    """Return the status of a class-10 SET acknowledgement (``0A 01 <status>``)."""
    if apdu.data_class != DataClass.DATA_OBJECTS:
        raise ProtocolError(f"expected a class-10 reply, got class {apdu.data_class}", raw)
    if apdu.ack != Ack.OK:
        raise NackError(apdu.ack, apdu.data_class, apdu.data, raw)
    if len(apdu.data) != 1:
        raise ProtocolError(
            f"SET acknowledgement should be one byte, got {apdu.data.hex(' ')}", raw
        )
    status = apdu.data[0]
    try:
        return ObjectStatus(status)
    except ValueError:
        return ObjectStatus.OPERATION_FAILED if status else ObjectStatus.OK


def read_f32(data: bytes, offset: int) -> float | None:
    """Big-endian float at ``offset``; None when absent, "not available" or NaN."""
    chunk = data[offset : offset + 4]
    if len(chunk) < 4 or chunk in (NOT_AVAILABLE, NAN):
        return None
    value: float = struct.unpack(">f", chunk)[0]
    return None if math.isnan(value) else value


def read_f64(data: bytes, offset: int) -> float | None:
    """Big-endian double at ``offset``; None when absent or NaN."""
    chunk = data[offset : offset + 8]
    if len(chunk) < 8:
        return None
    value: float = struct.unpack(">d", chunk)[0]
    return None if math.isnan(value) else value


def read_uint(data: bytes, offset: int, size: int) -> int | None:
    """Big-endian unsigned integer of ``size`` bytes at ``offset``; None when absent."""
    chunk = data[offset : offset + size]
    if len(chunk) < size:
        return None
    return int.from_bytes(chunk, "big")


def encode_f32(value: float | None) -> bytes:
    """Big-endian float, or the "keep the stored value" sentinel for None."""
    if value is None:
        return NOT_AVAILABLE
    return struct.pack(">f", value)
