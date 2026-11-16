"""Bluetooth LE transport for GENIbus, built on bleak and bleak-retry-connector.

The transport takes a ``BLEDevice`` from the caller and never scans. On every connection it
pairs (on an ESPHome proxy this is "start encryption": a re-encrypt when the proxy already
holds a bond, SMP pairing otherwise), subscribes to the GENI characteristic, lets the link
settle, and then runs one request at a time: write the frame in 20-byte pieces, wait for the
reassembled reply.

GENIbus replies carry no request id, so a reply is matched to its request by shape (data
class and APDU count, and whatever the caller adds through ``expect``), and a request that does
not complete with a reply closes the link: a late answer on the same link would otherwise be
taken for the next request's reply.

Grundfos pumps accept SMP pairing only for about three seconds after their Bluetooth button
is pressed; outside that window ESP-IDF reports reason 82 and BlueZ ``AuthenticationFailed``,
both of which become :class:`~grundfos_ble.exceptions.PairingRequired`. A local BlueZ adapter
also needs a pairing agent, which :mod:`grundfos_ble.bluez` registers around the connection.
How a pump behaves when it has dropped a bond the adapter still holds has not been measured. A
link that comes up and then never answers is no evidence of it: unreliable adapters do that with
a sound bond. ``reply_seen`` tells the two apart only within a pairing attempt, where silence
means the button window was missed.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import re
from collections import deque
from collections.abc import Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Final, Protocol, Self

import bleak_retry_connector
from bleak import BleakClient
from bleak.exc import BleakCharacteristicNotFoundError, BleakError
from bleak_retry_connector import establish_connection

from grundfos_ble.advertisement import GENI_CHARACTERISTIC_UUID
from grundfos_ble.bluez import bluez_device_path, bluez_pairing_agent
from grundfos_ble.exceptions import (
    ConnectionLost,
    GrundfosConnectionError,
    PairingRequired,
    PairingUnsupported,
    ProtocolError,
    RequestTimeout,
)
from grundfos_ble.genibus.telegram import MAX_REPLY_LENGTH, SD_ERROR, FrameAssembler, Telegram

if TYPE_CHECKING:
    from types import TracebackType

    from bleak.backends.characteristic import BleakGATTCharacteristic
    from bleak.backends.device import BLEDevice

_LOGGER = logging.getLogger(__name__)

WRITE_CHUNK: Final = 20
"""Bytes per write. Every frame the GO app sends goes out in pieces of at most 20 bytes, and
the esphome-alpha2go author reports that the pump ignores a longer write whatever the MTU; this
project has not tested that itself (every request it sends is 11 bytes)."""
CHUNK_SPACING: Final = 0.025
DEFAULT_REQUEST_TIMEOUT: Final = 10.0
"""Seconds to wait for a whole reply, write included; a healthy pump answers well inside it.

Kept above the link supervision timeout, as yalexs-ble does: a link that stalls and recovers
then answers late instead of being torn down, and a dead one reads as ConnectionLost. Home
Assistant (habluetooth on BlueZ) and ESPHome proxies both run an established link with an 8 s
supervision timeout.
"""
DEFAULT_CONNECT_TIMEOUT: Final = 10.0
"""Seconds allowed for one connect-and-pair attempt."""
SETTLE_AFTER_SUBSCRIBE: Final = 0.5
"""Seconds the link is left to settle after subscribing, before the first write, on every path.

ESPHome measured on an ALPHA3 (esphome/esphome#22651) that a request sent 0-200 ms after
notifications were enabled went unanswered in 4-5 of 10 connections, and none at 300-500 ms.
On a local BlueZ adapter a first request also went unanswered in most connections without a
pause (ha-grundfos-ble#1). Pairing finishes inside the connect, before the subscription, so the
pause belongs here rather than between pairing and subscribing.
"""
SUBSCRIBE_RETRY_DELAY: Final = 0.5
"""Seconds before the one retry of a failed subscription, e.g. while encryption settles."""
STOP_NOTIFY_TIMEOUT: Final = 1.0
"""Seconds allowed for unsubscribing before a disconnect."""
MAX_PIECES_REPORTED: Final = 8
"""Notification pieces of an unfinished reply quoted in a RequestTimeout."""
MAX_PIECES_KEPT: Final = 64
"""Notification pieces kept for that report; older ones are dropped."""
RETRY_DELAY: Final = 0.25

PAIRING_REFUSED_CODES: Final = frozenset({82})
"""ESP-IDF SMP reasons that mean "no bond and the button window is closed".

Others seen in the same message (timeouts, busy, internal errors) are transient.
"""

_PAIRING_FAILED = re.compile(r"Pairing failed due to error: (-?\d+)")
"""bleak-esphome's report of a failed pairing; it survives bleak-retry-connector's wrapping."""
_PAIRING_UNAVAILABLE: Final = "Pairing is not available"
"""bleak-esphome's NotImplementedError when the proxy's ESPHome has no pairing support."""
BLUEZ_AUTHENTICATION_FAILED: Final = "org.bluez.Error.AuthenticationFailed"
"""BlueZ's report of a refused pairing; with the pairing agent up it means a missed window."""
_LINK_ERRORS: Final = (BleakError, EOFError, BrokenPipeError, AttributeError, NotImplementedError)
"""What bleak backends raise for link trouble; bleak-retry-connector retries most of these.

The ``except`` clauses that use it wrap only bleak calls, so an ``AttributeError`` here comes
from a backend whose link went away under it, and a ``NotImplementedError`` from a proxy that
cannot pair (see :class:`~grundfos_ble.exceptions.PairingUnsupported`).
"""

_attempt_source: ContextVar[list[str | None] | None] = ContextVar(
    "grundfos_ble_attempt_source", default=None
)
"""Where the client class below records the Bluetooth source of the current attempt."""


@functools.cache
def _source_recording(base: Any) -> Any:  # noqa: ANN401 - a class only known at run time
    """Wrap Home Assistant's client class so that it records the scanner it connects through.

    Home Assistant (habluetooth) replaces bleak's client with one that picks a local adapter or
    a proxy at connect time, by signal and free slots, and keeps the choice private. This
    subclass reads it from that private method; with plain bleak the method does not exist and
    ``base`` is returned unchanged.
    """
    if not hasattr(base, "_async_get_best_available_backend_and_device"):
        return base

    class SourceRecordingClient(base):  # type: ignore[misc]
        def _async_get_best_available_backend_and_device(self, manager: Any) -> Any:  # noqa: ANN401
            backend = super()._async_get_best_available_backend_and_device(manager)
            if (slot := _attempt_source.get()) is not None:
                with contextlib.suppress(Exception):
                    slot[0] = backend.scanner.source
            return backend

    return SourceRecordingClient


def _details_source(device: BLEDevice) -> str | None:
    details = device.details
    if isinstance(details, dict) and isinstance(source := details.get("source"), str):
        return source
    return None


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

    async def request(
        self,
        telegram: Telegram,
        *,
        timeout: float | None = None,
        expect: Callable[[Telegram], bool] | None = None,
    ) -> Telegram:
        """Send a request and return the reply.

        Only a frame that :meth:`~grundfos_ble.genibus.telegram.Telegram.answers` the request
        and passes ``expect`` is taken as the reply. A request that does not complete with a
        reply closes the link.
        """
        ...

    async def send(self, telegram: Telegram) -> None:
        """Send a telegram without waiting for a reply."""
        ...


class BleTransport:
    """GENIbus over one BLE connection.

    ``device_callback``, when given, is asked for the device again before every connection
    attempt, so a retry goes through whichever adapter or proxy now hears the pump best, as
    bleak-retry-connector's ``ble_device_callback`` does. It may return None to keep the last
    device.

    ``async with BleTransport(device) as transport:`` connects on entry and disconnects on exit.

    ``pairing`` says that a new bond is expected (the pump's button was just pressed). Only then
    is a BlueZ pairing agent registered for a local adapter; without it an unbonded local
    adapter's pairing is refused, which is what a missed window looks like anyway.

    ``disconnected_callback`` is called when the pump or the adapter drops the live link, not
    when this transport disconnects on purpose.
    """

    def __init__(
        self,
        device: BLEDevice,
        *,
        name: str | None = None,
        max_attempts: int = 2,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        disconnected_callback: Callable[[], None] | None = None,
        device_callback: Callable[[], BLEDevice | None] | None = None,
        pairing: bool = False,
    ) -> None:
        self._device = device
        self._device_callback = device_callback
        self._name = name or device.name or device.address
        self._max_attempts = max(1, max_attempts)
        self._connect_timeout = connect_timeout
        self._request_timeout = request_timeout
        self._disconnected_callback = disconnected_callback
        self._pairing = pairing
        self._client: BleakClient | None = None
        self._lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._pending: asyncio.Future[Telegram] | None = None
        self._pending_request: Telegram | None = None
        self._expect: Callable[[Telegram], bool] | None = None
        self._assembler = FrameAssembler(replies_only=True, max_length=MAX_REPLY_LENGTH)
        self._pieces: deque[bytes] = deque(maxlen=MAX_PIECES_KEPT)
        self._unmatched = 0
        self._reply_seen = False
        self._last_source: str | None = None
        self.last_frames: deque[tuple[str, bytes]] = deque(maxlen=32)
        """Recent traffic as ("tx" | "rx", frame) for diagnostics and fixture capture."""

    async def __aenter__(self) -> Self:
        """Connect, as :meth:`connect` does, and return the transport."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Disconnect, whatever happened in the block."""
        await self.disconnect()

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

    @property
    def last_source(self) -> str | None:
        """The Bluetooth source (adapter or proxy) the latest connection attempt went through.

        Under Home Assistant this is the scanner habluetooth chose for the attempt, read from a
        private method; elsewhere, or if that changes, it is the source the device was last
        heard through when the attempt started. None when neither is known.
        """
        return self._last_source

    async def connect(self) -> None:
        """Connect through the device's path, pair (encrypt) and subscribe to replies.

        Retries are run here, one connection attempt at a time, so that a refusal on any
        attempt is seen; bleak-retry-connector reports only the last attempt's error. Concurrent
        calls share one connection.
        """
        async with self._connect_lock:
            if self.is_connected:
                return
            self._assembler.reset()
            self._pieces.clear()
            self._reply_seen = False
            client = await self._connect_with_retries()
            self._client = client
            try:
                await self._subscribe(client)
            except BaseException:
                await self.disconnect()
                raise
            # No MTU here: bleak on BlueZ only knows it after a private call, warns when asked
            # and reports its default of 23. The transport writes 20-byte pieces whatever it is.
            _LOGGER.debug("%s: connected and subscribed", self._name)
            await asyncio.sleep(SETTLE_AFTER_SUBSCRIBE)

    async def _subscribe(self, client: BleakClient) -> None:
        """Subscribe to replies, retrying once on the same link (never pairing evidence)."""
        for final_try in (False, True):
            try:
                await client.start_notify(GENI_CHARACTERISTIC_UUID, self._on_notify)
            except BleakCharacteristicNotFoundError as err:
                # The service cache is stale, as yalexs-ble also finds: drop it so that the next
                # connection resolves the services afresh.
                if hasattr(client, "clear_cache"):
                    with contextlib.suppress(Exception):
                        await client.clear_cache()
                raise self._link_error(err) from err
            except (*_LINK_ERRORS, TimeoutError) as err:
                if final_try or not client.is_connected:
                    raise self._link_error(err) from err
                _LOGGER.debug("%s: subscribing failed (%s); retrying once", self._name, err)
                await asyncio.sleep(SUBSCRIBE_RETRY_DELAY)
            else:
                return

    async def _connect_with_retries(self) -> BleakClient:
        last: GrundfosConnectionError | None = None
        for attempt in range(self._max_attempts):
            if attempt:
                await asyncio.sleep(RETRY_DELAY)
            if self._device_callback is not None:
                self._device = self._device_callback() or self._device
            slot: list[str | None] = [None]
            token = _attempt_source.set(slot)
            try:
                async with (
                    asyncio.timeout(self._connect_timeout),
                    bluez_pairing_agent(self._device, enabled=self._pairing),
                ):
                    return await establish_connection(
                        _source_recording(bleak_retry_connector.BleakClientWithServiceCache),
                        self._device,
                        self._name,
                        disconnected_callback=self._on_disconnect,
                        max_attempts=1,
                        pair=True,
                        # With bleak's own cache a local connect returns before bluetoothd has
                        # finished setting up GATT; without it bleak waits for ServicesResolved,
                        # which BlueZ's cache for a bonded device keeps quick. A proxy keeps its
                        # cache: it reports the link only once encrypted and set up.
                        use_services_cache=bluez_device_path(self._device) is None,
                    )
            except _LINK_ERRORS as err:
                last = self._translate(err)
                last.__cause__ = err
                if isinstance(last, (PairingRequired, PairingUnsupported)):
                    raise last from err
            except TimeoutError as err:
                last = GrundfosConnectionError(f"timed out connecting to {self._name}")
                last.__cause__ = err
            finally:
                _attempt_source.reset(token)
                self._last_source = slot[0] or _details_source(self._device)
        assert last is not None
        raise last

    async def disconnect(self) -> None:
        """Unsubscribe and drop the link; safe to call when not connected."""
        client, self._client = self._client, None
        if client is None:
            return
        if client.is_connected:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(STOP_NOTIFY_TIMEOUT):
                    await client.stop_notify(GENI_CHARACTERISTIC_UUID)
        with contextlib.suppress(Exception):
            await client.disconnect()

    async def request(
        self,
        telegram: Telegram,
        *,
        timeout: float | None = None,
        expect: Callable[[Telegram], bool] | None = None,
    ) -> Telegram:
        """Write ``telegram`` and return the pump's reply, one request at a time.

        A frame is taken as the reply only when it answers ``telegram`` (same data classes,
        one APDU each) and passes ``expect``; any other frame is logged as unsolicited and the
        wait goes on within the same deadline. A request that does not complete with a reply,
        whether it timed out, was cancelled or failed to write, closes the link: replies carry
        no request id, so a late answer on the same link would otherwise be taken for the next
        request's reply.
        """
        async with self._lock:
            client = self._require_client()
            loop = asyncio.get_running_loop()
            future: asyncio.Future[Telegram] = loop.create_future()
            self._assembler.reset()
            self._pieces.clear()
            self._unmatched = 0
            self._pending, self._pending_request, self._expect = future, telegram, expect
            frame = telegram.encode()
            replied = False
            try:
                async with asyncio.timeout(self._request_timeout if timeout is None else timeout):
                    await self._write(client, frame)
                    reply = await future
                replied = True
            except TimeoutError as err:
                raise RequestTimeout(
                    f"{self._name}: no reply to {frame.hex(' ')}; {self._received()}"
                ) from err
            finally:
                self._pending = self._pending_request = self._expect = None
                if future.done() and not future.cancelled():
                    future.exception()  # mark as retrieved
                else:
                    future.cancel()
                if not replied:
                    await self.disconnect()
            if reply.sd == SD_ERROR:
                raise ProtocolError("the pump sent an error reply", reply.raw)
            return reply

    async def send(self, telegram: Telegram) -> None:
        """Write ``telegram`` without waiting for a reply; a failed write closes the link."""
        async with self._lock:
            client = self._require_client()
            try:
                await self._write(client, telegram.encode())
            except BaseException:
                await self.disconnect()
                raise

    def _require_client(self) -> BleakClient:
        if self._client is None or not self._client.is_connected:
            raise ConnectionLost(f"{self._name}: not connected")
        return self._client

    async def _write(self, client: BleakClient, frame: bytes) -> None:
        self.last_frames.append(("tx", frame))
        for start in range(0, len(frame), WRITE_CHUNK):
            if start:
                await asyncio.sleep(CHUNK_SPACING)
            try:
                await client.write_gatt_char(
                    GENI_CHARACTERISTIC_UUID, frame[start : start + WRITE_CHUNK], response=False
                )
            except _LINK_ERRORS as err:
                raise self._link_error(err) from err

    def _received(self) -> str:
        """Describe what came for a request that got no whole reply: silence, or pieces."""
        unmatched = (
            f"; {self._unmatched} frame(s) did not answer the request" if self._unmatched else ""
        )
        if not self._pieces:
            return "nothing received" + unmatched
        pieces = list(self._pieces)
        quoted = " | ".join(piece.hex(" ") for piece in pieces[:MAX_PIECES_REPORTED])
        more = " | ..." if len(pieces) > MAX_PIECES_REPORTED else ""
        return f"{len(pieces)} piece(s) received: {quoted}{more}{unmatched}"

    def _on_notify(self, _characteristic: BleakGATTCharacteristic, data: bytearray) -> None:
        self._pieces.append(bytes(data))
        for frame in self._assembler.feed(data):
            self.last_frames.append(("rx", frame))
            self._reply_seen = True
            pending = self._pending
            if pending is None or pending.done():
                _LOGGER.debug("%s: unsolicited frame %s", self._name, frame.hex(" "))
            elif (reply := self._as_reply(frame)) is None:
                self._unmatched += 1
                _LOGGER.debug("%s: unsolicited frame %s", self._name, frame.hex(" "))
            else:
                pending.set_result(reply)

    def _as_reply(self, frame: bytes) -> Telegram | None:
        """Decode ``frame`` if it answers the pending request, else None."""
        request = self._pending_request
        try:
            reply = Telegram.decode_reply(frame)
        except ProtocolError:
            return None
        if request is not None and not reply.answers(request):
            return None
        if reply.sd != SD_ERROR and self._expect is not None and not self._expect(reply):
            return None
        return reply

    def _on_disconnect(self, client: BleakClient) -> None:
        if self._client is None or client is not self._client:
            return  # our own disconnect, a failed attempt, or a late callback from an old link
        self._client = None
        pending = self._pending
        if pending is not None and not pending.done():
            pending.set_exception(ConnectionLost(f"{self._name}: disconnected"))
        if self._disconnected_callback is not None:
            self._disconnected_callback()

    def _link_error(self, err: BaseException) -> GrundfosConnectionError:
        """Translate an error on an established link: never evidence about pairing."""
        return GrundfosConnectionError(f"{self._name}: {type(err).__name__}: {err}")

    def _translate(self, err: BaseException) -> GrundfosConnectionError:
        text = str(err)
        if match := _PAIRING_FAILED.search(text):
            code = int(match.group(1))
            if code in PAIRING_REFUSED_CODES:
                return PairingRequired(f"{self._name}: {text}", code)
            return GrundfosConnectionError(f"{self._name}: {text}", code)
        if BLUEZ_AUTHENTICATION_FAILED in text:
            return PairingRequired(f"{self._name}: {text}")
        if _PAIRING_UNAVAILABLE in text:
            return PairingUnsupported(f"{self._name}: {text}")
        return GrundfosConnectionError(f"{self._name}: {type(err).__name__}: {text}")
