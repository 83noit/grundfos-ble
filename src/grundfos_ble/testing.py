"""Test helpers: a transport that replays recorded frames, and the fixture file format.

Fixture files are plain text, one frame per line, ``>`` for a request written to the pump
and ``<`` for the reply it produced, hex bytes separated by spaces. Lines starting with ``#``
are comments. Consecutive ``<`` lines after a ``>`` are the notification pieces of one reply
and are concatenated.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from grundfos_ble.exceptions import RequestTimeout
from grundfos_ble.genibus.telegram import Telegram

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

    Requests are matched by their exact bytes. Unknown requests raise
    :class:`~grundfos_ble.exceptions.RequestTimeout`, which is what a silent pump looks like.
    """

    exchanges: list[Exchange] = field(default_factory=list)
    connected: bool = False
    sent: list[bytes] = field(default_factory=list)
    last_frames: deque[tuple[str, bytes]] = field(default_factory=lambda: deque(maxlen=32))
    reply_seen: bool = False

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
        self.connected = True

    async def disconnect(self) -> None:
        """Pretend to disconnect."""
        self.connected = False

    async def request(self, telegram: Telegram, *, timeout: float | None = None) -> Telegram:
        """Return the recorded reply for ``telegram``."""
        del timeout  # a replay answers instantly
        frame = telegram.encode()
        self.sent.append(frame)
        self.last_frames.append(("tx", frame))
        for exchange in self.exchanges:
            if exchange.request == frame and exchange.reply:
                self.last_frames.append(("rx", exchange.reply))
                self.reply_seen = True
                return Telegram.decode(exchange.reply)
        raise RequestTimeout(f"no recorded reply for {frame.hex(' ')}")

    async def send(self, telegram: Telegram) -> None:
        """Record a no-reply message."""
        self.sent.append(telegram.encode())
