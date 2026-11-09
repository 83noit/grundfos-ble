"""Bluetooth LE transport for GENIbus, built on bleak and bleak-retry-connector.

The transport takes a ``BLEDevice`` from the caller and never scans. On every connection it
pairs (on an ESPHome proxy this is "start encryption": a re-encrypt when the proxy already
holds a bond, SMP pairing otherwise), subscribes to the GENI characteristic, and then runs
one request at a time: write the frame in 20-byte pieces, wait for the reassembled reply.

Grundfos pumps accept SMP pairing only for about three seconds after their Bluetooth button
is pressed; outside that window ESP-IDF reports reason 82, which becomes
:class:`~grundfos_ble.exceptions.PairingRequired`. How a pump behaves when it has dropped a
bond the adapter still holds has not been measured, so callers should also watch
``reply_seen``: a link that came up but never answered is suspicious when it keeps happening.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Final, Protocol

from bleak import BleakClient
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from grundfos_ble.advertisement import GENI_CHARACTERISTIC_UUID
from grundfos_ble.exceptions import (
    ConnectionLost,
    GrundfosConnectionError,
    PairingRequired,
    ProtocolError,
    RequestTimeout,
)
from grundfos_ble.genibus.telegram import SD_ERROR, FrameAssembler, Telegram

if TYPE_CHECKING:
    from bleak.backends.characteristic import BleakGATTCharacteristic
    from bleak.backends.device import BLEDevice

_LOGGER = logging.getLogger(__name__)

WRITE_CHUNK: Final = 20
"""The pump ignores writes longer than this, whatever MTU was negotiated."""
CHUNK_SPACING: Final = 0.025
DEFAULT_REQUEST_TIMEOUT: Final = 2.0
"""Seconds to wait for a whole reply, write included; a healthy pump answers well inside it."""
DEFAULT_CONNECT_TIMEOUT: Final = 10.0
"""Seconds allowed for one connect-and-pair attempt."""
RETRY_DELAY: Final = 0.25

PAIRING_REFUSED_CODES: Final = frozenset({82})
"""ESP-IDF SMP reasons that mean "no bond and the button window is closed".

Others seen in the same message (timeouts, busy, internal errors) are transient.
"""

_PAIRING_FAILED = re.compile(r"[Pp]airing failed\D*?(-?\d+)")
_LINK_ERRORS: Final = (BleakError, EOFError, BrokenPipeError, AttributeError, NotImplementedError)
"""What bleak backends raise for link trouble; bleak-retry-connector retries most of these."""


class Transport(Protocol):
    """What :class:`~grundfos_ble.pump.GrundfosPump` needs from a transport."""

    last_frames: deque[tuple[str, bytes]]
    """Recent traffic as ("tx" | "rx", frame), for diagnostics and fixture capture."""

    @property
    def is_connected(self) -> bool:
        """True while a link is up."""
        ...

    @property
    def reply_seen(self) -> bool:
        """True once the pump has answered on the current or last link."""
        ...

    async def connect(self) -> None:
        """Connect, encrypt and subscribe."""
        ...

    async def disconnect(self) -> None:
        """Drop the link."""
        ...

    async def request(self, telegram: Telegram, *, timeout: float | None = None) -> Telegram:
        """Send a request and return the reply."""
        ...

    async def send(self, telegram: Telegram) -> None:
        """Send a telegram without waiting for a reply."""
        ...


class BleTransport:
    """GENIbus over one BLE connection."""

    def __init__(
        self,
        device: BLEDevice,
        *,
        name: str | None = None,
        max_attempts: int = 2,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        disconnected_callback: Callable[[], None] | None = None,
    ) -> None:
        self._device = device
        self._name = name or device.name or device.address
        self._max_attempts = max(1, max_attempts)
        self._connect_timeout = connect_timeout
        self._request_timeout = request_timeout
        self._disconnected_callback = disconnected_callback
        self._client: BleakClient | None = None
        self._lock = asyncio.Lock()
        self._pending: asyncio.Future[bytes] | None = None
        self._assembler = FrameAssembler(replies_only=True)
        self._reply_seen = False
        self.last_frames: deque[tuple[str, bytes]] = deque(maxlen=32)
        """Recent traffic as ("tx" | "rx", frame) for diagnostics and fixture capture."""

    @property
    def device(self) -> BLEDevice:
        """The device this transport talks to."""
        return self._device

    @property
    def is_connected(self) -> bool:
        """True while the BLE link is up."""
        return self._client is not None and self._client.is_connected

    @property
    def reply_seen(self) -> bool:
        """True once the pump has answered on the current or last link."""
        return self._reply_seen

    async def connect(self) -> None:
        """Connect through the device's path, pair (encrypt) and subscribe to replies.

        Retries are run here, one connection attempt at a time, so that a refusal on any
        attempt is seen; bleak-retry-connector reports only the last attempt's error.
        """
        if self.is_connected:
            return
        self._assembler.reset()
        self._reply_seen = False
        client = await self._connect_with_retries()
        self._client = client
        try:
            await client.start_notify(GENI_CHARACTERISTIC_UUID, self._on_notify)
        except (*_LINK_ERRORS, TimeoutError) as err:
            await self.disconnect()
            raise self._translate(err) from err
        except BaseException:
            await self.disconnect()
            raise
        _LOGGER.debug("%s: connected and subscribed (mtu %s)", self._name, client.mtu_size)

    async def _connect_with_retries(self) -> BleakClient:
        last: GrundfosConnectionError | None = None
        for attempt in range(self._max_attempts):
            if attempt:
                await asyncio.sleep(RETRY_DELAY)
            try:
                async with asyncio.timeout(self._connect_timeout):
                    return await establish_connection(
                        BleakClientWithServiceCache,
                        self._device,
                        self._name,
                        disconnected_callback=self._on_disconnect,
                        max_attempts=1,
                        pair=True,
                    )
            except _LINK_ERRORS as err:
                last = self._translate(err)
                last.__cause__ = err
                if isinstance(last, PairingRequired):
                    raise last from err
            except TimeoutError as err:
                last = GrundfosConnectionError(f"timed out connecting to {self._name}")
                last.__cause__ = err
        assert last is not None
        raise last

    async def disconnect(self) -> None:
        """Drop the link; safe to call when not connected."""
        client, self._client = self._client, None
        if client is None:
            return
        with contextlib.suppress(Exception):
            await client.disconnect()

    async def request(self, telegram: Telegram, *, timeout: float | None = None) -> Telegram:
        """Write ``telegram`` and return the pump's reply, one request at a time.

        A request that times out closes the link. Replies carry no request id, so a late
        answer on the same link would otherwise be taken for the next request's reply.
        """
        async with self._lock:
            client = self._require_client()
            loop = asyncio.get_running_loop()
            future: asyncio.Future[bytes] = loop.create_future()
            self._assembler.reset()
            self._pending = future
            frame = telegram.encode()
            try:
                async with asyncio.timeout(timeout or self._request_timeout):
                    await self._write(client, frame)
                    reply = await future
            except TimeoutError as err:
                await self.disconnect()
                raise RequestTimeout(f"{self._name}: no reply to {frame.hex(' ')}") from err
            except _LINK_ERRORS as err:
                raise self._translate(err) from err
            finally:
                self._pending = None
                if future.done() and not future.cancelled():
                    future.exception()  # mark as retrieved
                else:
                    future.cancel()
            decoded = Telegram.decode(reply)
            if decoded.sd == SD_ERROR:
                raise ProtocolError("the pump sent an error reply", reply)
            return decoded

    async def send(self, telegram: Telegram) -> None:
        """Write ``telegram`` without waiting for a reply."""
        async with self._lock:
            client = self._require_client()
            try:
                await self._write(client, telegram.encode())
            except _LINK_ERRORS as err:
                raise self._translate(err) from err

    def _require_client(self) -> BleakClient:
        if self._client is None or not self._client.is_connected:
            raise ConnectionLost(f"{self._name}: not connected")
        return self._client

    async def _write(self, client: BleakClient, frame: bytes) -> None:
        self.last_frames.append(("tx", frame))
        for start in range(0, len(frame), WRITE_CHUNK):
            if start:
                await asyncio.sleep(CHUNK_SPACING)
            await client.write_gatt_char(
                GENI_CHARACTERISTIC_UUID, frame[start : start + WRITE_CHUNK], response=False
            )

    def _on_notify(self, _characteristic: BleakGATTCharacteristic, data: bytearray) -> None:
        for frame in self._assembler.feed(data):
            self.last_frames.append(("rx", frame))
            self._reply_seen = True
            pending = self._pending
            if pending is not None and not pending.done():
                pending.set_result(frame)
            else:
                _LOGGER.debug("%s: unsolicited frame %s", self._name, frame.hex(" "))

    def _on_disconnect(self, client: BleakClient) -> None:
        if self._client is not None and client is not self._client:
            return  # a late callback from an earlier link
        self._client = None
        pending = self._pending
        if pending is not None and not pending.done():
            pending.set_exception(ConnectionLost(f"{self._name}: disconnected"))
        if self._disconnected_callback is not None:
            self._disconnected_callback()

    def _translate(self, err: BaseException) -> GrundfosConnectionError:
        text = str(err)
        if match := _PAIRING_FAILED.search(text):
            code = int(match.group(1))
            if code in PAIRING_REFUSED_CODES:
                return PairingRequired(f"{self._name}: {text}", code)
            return GrundfosConnectionError(f"{self._name}: {text}", code)
        return GrundfosConnectionError(f"{self._name}: {type(err).__name__}: {text}")
