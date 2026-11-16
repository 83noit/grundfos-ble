"""Test helpers: a transport that replays recorded frames, and the fixture file format.

Fixture files are plain text, one frame per line, ``>`` for a request written to the pump
and ``<`` for the reply it produced, hex bytes separated by spaces. Lines starting with ``#``
are comments. Consecutive ``<`` lines after a ``>`` are the notification pieces of one reply
and are concatenated.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from grundfos_ble.exceptions import ConnectionLost, ProtocolError, RequestTimeout
from grundfos_ble.genibus.apdu import Apdu, DataClass
from grundfos_ble.genibus.telegram import (
    BLE_MASTER_ADDRESS,
    BLE_PUMP_ADDRESS,
    SD_ERROR,
    SD_REPLY,
    Telegram,
)

FIXTURES = Path(__file__).parent / "testdata" / "alpha2go"
"""Frames recorded from a real ALPHA2 GO, shipped so that dependants can test against them."""


@dataclass(slots=True)
class Exchange:
    """One recorded request/reply pair."""

    request: bytes
    reply: bytes
    comment: str = ""


def parse_fixture(text: str) -> list[Exchange]:
    """Parse the fixture format described in the module docstring."""
    exchanges: list[Exchange] = []
    current: Exchange | None = None
    pending_comment = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            pending_comment = line.lstrip("# ").strip()
            continue
        marker, _, hexpart = line.partition(" ")
        data = bytes.fromhex(hexpart.replace(" ", ""))
        if marker == ">":
            current = Exchange(data, b"", pending_comment)
            pending_comment = ""
            exchanges.append(current)
        elif marker == "<":
            if current is None:
                raise ValueError("reply line before any request line")
            current.reply += data
        else:
            raise ValueError(f"unexpected fixture line: {raw_line!r}")
    return exchanges


def load_fixture(path: str | Path) -> list[Exchange]:
    """Read and parse a fixture file."""
    return parse_fixture(Path(path).read_text(encoding="utf-8"))


@dataclass(slots=True)
class ReplayTransport:
    """A :class:`~grundfos_ble.transport.Transport` that answers from recorded exchanges.

    Requests are matched by their exact bytes. An unknown request raises
    :class:`~grundfos_ble.exceptions.RequestTimeout`, which is what a silent pump looks like,
    and closes the link as :class:`~grundfos_ble.transport.BleTransport` does: until
    :meth:`connect` is called again, requests raise
    :class:`~grundfos_ble.exceptions.ConnectionLost`. Replies go through the same shape checks
    as on a real link (:meth:`~grundfos_ble.genibus.telegram.Telegram.answers` and the
    caller's ``expect``).

    The faults a real link shows can be switched on: ``lag`` delays every reply (and times out
    a request whose timeout is shorter), ``silent_on`` lists requests the pump ignores,
    ``stray`` holds frames delivered one per request ahead of its reply, ``duplicate`` delivers
    the previous reply again ahead of each reply, and ``busy_once`` lists class-10 requests
    first answered with object status BUSY.
    """

    exchanges: list[Exchange] = field(default_factory=list)
    connected: bool = False
    sent: list[bytes] = field(default_factory=list)
    last_frames: deque[tuple[str, bytes]] = field(default_factory=lambda: deque(maxlen=32))
    reply_seen: bool = False
    lag: float = 0.0
    silent_on: set[bytes] = field(default_factory=set)
    stray: deque[bytes] = field(default_factory=deque)
    duplicate: bool = False
    busy_once: set[bytes] = field(default_factory=set)
    connects: int = 0
    """How many times :meth:`connect` was called."""
    closed: bool = False
    """True after a request timed out, until the next :meth:`connect`."""
    _previous: bytes | None = None

    @classmethod
    def from_fixture(cls, path: str | Path) -> ReplayTransport:
        """Build a transport from a fixture file."""
        return cls(load_fixture(path))

    @property
    def is_connected(self) -> bool:
        """True after :meth:`connect`."""
        return self.connected

    async def connect(self) -> None:
        """Pretend to connect."""
        self.connects += 1
        self.connected = True
        self.closed = False

    async def disconnect(self) -> None:
        """Pretend to disconnect."""
        self.connected = False

    async def request(
        self,
        telegram: Telegram,
        *,
        timeout: float | None = None,
        expect: Callable[[Telegram], bool] | None = None,
    ) -> Telegram:
        """Return the recorded reply for ``telegram``, after any configured faults."""
        if self.closed:
            raise ConnectionLost("not connected (the last request timed out)")
        frame = telegram.encode()
        self.sent.append(frame)
        self.last_frames.append(("tx", frame))
        if timeout is not None and self.lag > timeout:
            await asyncio.sleep(timeout)
            return self._time_out(frame)
        if self.lag:
            await asyncio.sleep(self.lag)
        for candidate in self._delivered(frame):
            self.last_frames.append(("rx", candidate))
            self.reply_seen = True
            try:
                reply = Telegram.decode_reply(candidate)
            except ProtocolError:
                continue
            if not reply.answers(telegram):
                continue
            if reply.sd != SD_ERROR and expect is not None and not expect(reply):
                continue
            self._previous = candidate
            return reply
        return self._time_out(frame)

    def _delivered(self, frame: bytes) -> list[bytes]:
        """Return the frames the pump would send while ``frame`` is pending, in order."""
        frames: list[bytes] = []
        if self.stray:
            frames.append(self.stray.popleft())
        if self.duplicate and self._previous is not None:
            frames.append(self._previous)
        if frame in self.silent_on:
            return frames
        if frame in self.busy_once and frame[4] == DataClass.DATA_OBJECTS:
            self.busy_once.discard(frame)
            frames.append(busy_reply())
            return frames
        recorded = next((e.reply for e in self.exchanges if e.request == frame and e.reply), None)
        if recorded is not None:
            frames.append(recorded)
        return frames

    def _time_out(self, frame: bytes) -> Telegram:
        self.closed = True
        self.connected = False
        raise RequestTimeout(f"no recorded reply for {frame.hex(' ')}")

    async def send(self, telegram: Telegram) -> None:
        """Record a no-reply message."""
        self.sent.append(telegram.encode())


def busy_reply() -> bytes:
    """Return a class-10 reply whose only object reports BUSY, as a bare status byte."""
    return Telegram(
        SD_REPLY, BLE_MASTER_ADDRESS, BLE_PUMP_ADDRESS, (Apdu(DataClass.DATA_OBJECTS, 0, b"\x02"),)
    ).encode()
