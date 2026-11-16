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
from grundfos_ble.genibus.apdu import APDU_HEAD_SIZE, MAX_APDU_DATA, Apdu
from grundfos_ble.genibus.crc import crc16, verify_crc

SD_REQUEST = 0x27
"""Data request: the receiver must reply."""
SD_MESSAGE = 0x26
"""Data message: no reply expected."""
SD_REPLY = 0x24
"""Data reply."""
SD_ERROR = 0x25
"""Error reply. Not in the 2005 spec and never seen from a pump by this project; community
decoders accept it, so the reply-side checks do too."""

START_DELIMITERS = frozenset({SD_REQUEST, SD_MESSAGE, SD_REPLY, SD_ERROR})

BLE_PUMP_ADDRESS = 0xE7
BLE_MASTER_ADDRESS = 0xF8
BROADCAST_ADDRESS = 0xFF
CONNECTION_REQUEST_ADDRESS = 0xFE

FRAME_OVERHEAD = 4
"""SD, LEN and the two CRC bytes."""
MAX_FRAME_LENGTH = FRAME_OVERHEAD + 0xFF
"""What LEN can describe at all."""
MAX_REPLY_LENGTH = FRAME_OVERHEAD + 2 + APDU_HEAD_SIZE + MAX_APDU_DATA
"""The longest reply to a one-APDU request: addresses, one APDU head and 63 data bytes (71).

The ALPHA2 GO's longest measured reply, motor state, is 56 bytes."""


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
    def decode_reply(cls, frame: bytes | bytearray | memoryview) -> Telegram:
        """Parse a frame that must be the pump's reply to this client over Bluetooth LE.

        On top of :meth:`decode`, checks the start delimiter (reply or error) and that the
        frame goes from the pump (0xE7) to the client (0xF8).
        """
        telegram = cls.decode(frame)
        if not telegram.is_reply:
            raise ProtocolError(f"not a reply: start delimiter 0x{telegram.sd:02x}", telegram.raw)
        if telegram.da != BLE_MASTER_ADDRESS or telegram.sa != BLE_PUMP_ADDRESS:
            raise ProtocolError(
                f"reply addressed {telegram.sa:02x}->{telegram.da:02x}, "
                f"not {BLE_PUMP_ADDRESS:02x}->{BLE_MASTER_ADDRESS:02x}",
                telegram.raw,
            )
        return telegram

    def answers(self, request: Telegram) -> bool:
        """Return True when this reply has the shape of an answer to ``request``.

        GENIbus replies carry no request id, so the shape is all there is: one APDU per
        request APDU, in the same order and of the same data class. An error reply
        (``SD_ERROR``) answers anything.
        """
        if self.sd == SD_ERROR:
            return True
        return (
            self.sd == SD_REPLY
            and len(self.apdus) == len(request.apdus)
            and all(
                a.data_class == b.data_class for a, b in zip(self.apdus, request.apdus, strict=True)
            )
        )

    @classmethod
    def decode(cls, frame: bytes | bytearray | memoryview) -> Telegram:
        """Parse a complete frame of any kind, verifying length and CRC."""
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
    the pump, be no longer than ``max_length`` and carry a good CRC, otherwise scanning resumes
    one byte further on. While such a candidate is still incomplete, a complete frame with a
    good CRC that starts later in the buffer wins, so a false start with a long LEN cannot
    swallow the genuine reply behind it.
    """

    def __init__(
        self,
        *,
        chunk_timeout: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        replies_only: bool = False,
        max_length: int = MAX_FRAME_LENGTH,
    ) -> None:
        self._buffer = bytearray()
        self._chunk_timeout = chunk_timeout
        self._clock = clock
        self._replies_only = replies_only
        self._max_length = max_length
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

    def _plausible_start(self, at: int = 0) -> bool:
        buffer = self._buffer
        size = len(buffer) - at
        if not self._replies_only:
            return buffer[at] in START_DELIMITERS
        return (
            buffer[at] in (SD_REPLY, SD_ERROR)
            and (size < 2 or 2 <= buffer[at + 1] <= self._max_length - FRAME_OVERHEAD)
            and (size < 3 or buffer[at + 2] == BLE_MASTER_ADDRESS)
            and (size < 4 or buffer[at + 3] == BLE_PUMP_ADDRESS)
        )

    def _later_frame(self) -> int | None:
        """Offset of a complete, CRC-valid reply after a still incomplete candidate at 0."""
        for at in range(1, len(self._buffer) - FRAME_OVERHEAD):
            if not self._plausible_start(at):
                continue
            end = at + self._buffer[at + 1] + FRAME_OVERHEAD
            if end <= len(self._buffer) and verify_crc(bytes(self._buffer[at:end])):
                return at
        return None

    def _drain(self) -> Iterable[bytes]:
        while self._buffer:
            if not self._plausible_start():
                self.dropped_bytes += 1
                del self._buffer[0]
                continue
            expected = frame_length(self._buffer)
            if expected is None or len(self._buffer) < expected:
                if self._replies_only and (later := self._later_frame()) is not None:
                    self.dropped_bytes += later
                    del self._buffer[:later]
                    continue
                return
            frame = bytes(self._buffer[:expected])
            if self._replies_only and not verify_crc(frame):
                # Most likely a false start: rescan from the next byte, not past the "frame".
                self.dropped_bytes += 1
                del self._buffer[0]
                continue
            del self._buffer[:expected]
            yield frame
