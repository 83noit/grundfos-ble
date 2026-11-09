"""GENIbus telegrams and reassembly of fragmented frames.

Telegram layout (GENIbus Protocol Specification, 2005, §3):

    SD  LEN  DA  SA  [APDU ...]  CRC-hi  CRC-lo

``LEN`` counts every byte after itself up to, but not including, the CRC: destination,
source and all APDU bytes. The CRC covers ``LEN`` through the last APDU byte.

Over Bluetooth LE the pump is address 0xE7 and the client 0xF8, so requests read
``27 LEN E7 F8 …`` and replies ``24 LEN F8 E7 …``. Notifications carry at most 20 bytes, so a
reply longer than that arrives in several pieces; :class:`FrameAssembler` puts them together.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from grundfos_ble.exceptions import CrcError, ProtocolError
from grundfos_ble.genibus.apdu import Apdu
from grundfos_ble.genibus.crc import crc16, verify_crc

SD_REQUEST = 0x27
"""Data request: the receiver must reply."""
SD_MESSAGE = 0x26
"""Data message: no reply expected."""
SD_REPLY = 0x24
"""Data reply."""
SD_ERROR = 0x25
"""Error reply (seen by community decoders, not in the 2005 spec)."""

START_DELIMITERS = frozenset({SD_REQUEST, SD_MESSAGE, SD_REPLY, SD_ERROR})

BLE_PUMP_ADDRESS = 0xE7
BLE_MASTER_ADDRESS = 0xF8
BROADCAST_ADDRESS = 0xFF
CONNECTION_REQUEST_ADDRESS = 0xFE

FRAME_OVERHEAD = 4
"""SD, LEN and the two CRC bytes."""
MAX_FRAME_LENGTH = FRAME_OVERHEAD + 0xFF


def frame_length(prefix: bytes | bytearray | memoryview) -> int | None:
    """Return the full frame length implied by the first two bytes, or None if unknown yet."""
    if len(prefix) < 2:
        return None
    return prefix[1] + FRAME_OVERHEAD


@dataclass(frozen=True, slots=True)
class Telegram:
    """A complete GENIbus telegram."""

    sd: int
    da: int
    sa: int
    apdus: tuple[Apdu, ...]
    raw: bytes | None = field(default=None, compare=False, repr=False)
    """The bytes this telegram was decoded from, kept for diagnostics and fixtures."""

    @classmethod
    def request(
        cls,
        *apdus: Apdu,
        da: int = BLE_PUMP_ADDRESS,
        sa: int = BLE_MASTER_ADDRESS,
        sd: int = SD_REQUEST,
    ) -> Telegram:
        """Build a request (or, with ``sd=SD_MESSAGE``, a no-reply message) to the pump."""
        return cls(sd, da, sa, tuple(apdus))

    @property
    def is_reply(self) -> bool:
        """True for data replies and error replies."""
        return self.sd in (SD_REPLY, SD_ERROR)

    @property
    def apdu(self) -> Apdu:
        """The single APDU of a one-APDU telegram."""
        if len(self.apdus) != 1:
            raise ProtocolError(f"expected one APDU, telegram has {len(self.apdus)}")
        return self.apdus[0]

    def encode(self) -> bytes:
        """Serialise with length and CRC."""
        payload = bytes((self.da, self.sa)) + b"".join(a.encode() for a in self.apdus)
        if len(payload) > 0xFF:
            raise ValueError(f"telegram payload {len(payload)} bytes exceeds 255")
        body = bytes((len(payload),)) + payload
        return bytes((self.sd,)) + body + crc16(body).to_bytes(2, "big")

    @classmethod
    def decode(cls, frame: bytes | bytearray | memoryview) -> Telegram:
        """Parse a complete frame, verifying length and CRC."""
        raw = bytes(frame)
        if len(raw) < FRAME_OVERHEAD + 2:
            raise ProtocolError(f"frame too short ({len(raw)} bytes)", raw)
        expected = frame_length(raw)
        if expected != len(raw):
            raise ProtocolError(f"LEN says {expected} bytes, frame has {len(raw)}", raw)
        if raw[0] not in START_DELIMITERS:
            raise ProtocolError(f"unknown start delimiter 0x{raw[0]:02x}", raw)
        if not verify_crc(raw):
            raise CrcError("CRC mismatch", raw)
        apdus: list[Apdu] = []
        offset = 4
        end = len(raw) - 2
        while offset < end:
            apdu, offset = Apdu.decode(raw[:end], offset)
            apdus.append(apdu)
        return cls(raw[0], raw[2], raw[3], tuple(apdus), raw)


class FrameAssembler:
    """Reassemble frames from notification-sized pieces.

    Feed every notification payload to :meth:`feed`; it returns the complete frames
    (as raw bytes) that the piece completed. A piece arriving more than ``chunk_timeout``
    seconds after the previous one discards any partial frame, and bytes that cannot start a
    frame are dropped one at a time. With ``replies_only`` a frame must look like a reply from
    the pump and carry a good CRC, otherwise scanning resumes one byte further on.
    """

    def __init__(
        self,
        *,
        chunk_timeout: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        replies_only: bool = False,
    ) -> None:
        self._buffer = bytearray()
        self._chunk_timeout = chunk_timeout
        self._clock = clock
        self._replies_only = replies_only
        self._last_piece_at: float | None = None
        self.dropped_bytes = 0
        self.discarded_partials = 0

    def reset(self) -> None:
        """Forget any partial frame."""
        self._buffer.clear()
        self._last_piece_at = None

    def feed(self, piece: bytes | bytearray | memoryview) -> list[bytes]:
        """Add ``piece`` and return every frame completed by it."""
        now = self._clock()
        if (
            self._buffer
            and self._last_piece_at is not None
            and now - self._last_piece_at > self._chunk_timeout
        ):
            self.discarded_partials += 1
            self._buffer.clear()
        self._last_piece_at = now
        self._buffer.extend(piece)
        return list(self._drain())

    def _plausible_start(self) -> bool:
        buffer = self._buffer
        if not self._replies_only:
            return buffer[0] in START_DELIMITERS
        return (
            buffer[0] in (SD_REPLY, SD_ERROR)
            and (len(buffer) < 2 or buffer[1] >= 2)
            and (len(buffer) < 3 or buffer[2] == BLE_MASTER_ADDRESS)
            and (len(buffer) < 4 or buffer[3] == BLE_PUMP_ADDRESS)
        )

    def _drain(self) -> Iterable[bytes]:
        while self._buffer:
            if not self._plausible_start():
                self.dropped_bytes += 1
                del self._buffer[0]
                continue
            expected = frame_length(self._buffer)
            if expected is None or len(self._buffer) < expected:
                return
            frame = bytes(self._buffer[:expected])
            if self._replies_only and not verify_crc(frame):
                # Most likely a false start: rescan from the next byte, not past the "frame".
                self.dropped_bytes += 1
                del self._buffer[0]
                continue
            del self._buffer[:expected]
            yield frame
