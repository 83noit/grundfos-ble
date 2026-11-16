"""Bluetooth LE transport for GENIbus, on top of ble-bond.

ble-bond's :class:`~ble_bond.BondedLink` does the Bluetooth part: pairing on every connection
(on an ESPHome proxy, a re-encrypt with the stored bond), recording which adapter or proxy each
attempt used, the BlueZ pairing agent, one request at a time, and closing the link whenever a
request does not complete. This module adds GENIbus: frames are reassembled by
:class:`~grundfos_ble.genibus.telegram.FrameAssembler`, a reply is taken only if it has the
shape of an answer to the request (and passes the caller's ``expect``), and ble-bond's
exceptions become this library's.

Grundfos pumps accept SMP pairing only for about three seconds after their Bluetooth button is
pressed; outside that window ESP-IDF reports reason 82 and BlueZ ``AuthenticationFailed``, both
of which become :class:`~grundfos_ble.exceptions.PairingRequired`. A link that comes up and then
never answers is no evidence about the bond: unreliable adapters do that with a sound bond.
``reply_seen`` tells the two apart only within a pairing attempt, where silence means the button
window was missed.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Final, Protocol, Self

import ble_bond
from ble_bond import BondedLink, LinkPolicy, SubscribeRetry

from grundfos_ble.advertisement import GENI_CHARACTERISTIC_UUID
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

    from bleak.backends.device import BLEDevice

WRITE_CHUNK: Final = 20
"""Bytes per write. Every frame the GO app sends goes out in pieces of at most 20 bytes, and
the esphome-alpha2go author reports that the pump ignores a longer write whatever the MTU; this
project has not tested that itself (every request it sends is 11 bytes)."""
SETTLE_AFTER_SUBSCRIBE: Final = 0.5
"""Seconds the link is left to settle after subscribing, before the first write, on every path.

ESPHome measured on an ALPHA3 (esphome/esphome#22651) that a request sent 0-200 ms after
notifications were enabled went unanswered in 4-5 of 10 connections, and none at 300-500 ms.
On a local BlueZ adapter a first request also went unanswered in most connections without a
pause (ha-grundfos-ble#1). Pairing finishes inside the connect, before the subscription, so the
pause belongs here rather than between pairing and subscribing.
"""
DEFAULT_REQUEST_TIMEOUT: Final = 10.0
"""Seconds to wait for a whole reply, write included; a healthy pump answers well inside it.

Kept above the link supervision timeout, as yalexs-ble does: a link that stalls and recovers
then answers late instead of being torn down, and a dead one reads as ConnectionLost. Home
Assistant (habluetooth on BlueZ) and ESPHome proxies both run an established link with an 8 s
supervision timeout.
"""
DEFAULT_CONNECT_TIMEOUT: Final = 10.0
"""Seconds allowed for one connect-and-pair attempt."""

POLICY: Final = LinkPolicy(
    settle_after_pair=0.0,
    settle_after_subscribe=SETTLE_AFTER_SUBSCRIBE,
    subscribe_retry=SubscribeRetry.ANY_LINK_ERROR,
    subscribe_retry_delay=0.5,
    # A refused subscription says nothing about the bond on this pump: never pairing evidence.
    insufficient_authentication_is_evidence=False,
    # The pump drops a link that is not encrypted within about two seconds and never answers
    # one, so a proxy that cannot pair cannot reach it at all.
    retry_unpaired=False,
    write_chunk=WRITE_CHUNK,
    connect_timeout=DEFAULT_CONNECT_TIMEOUT,
    request_timeout=DEFAULT_REQUEST_TIMEOUT,
)
"""How an ALPHA2 GO's link is handled (see the constants above for where each value comes
from)."""


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

    ``pairing`` says that a new bond is expected (the pump's button was just pressed). Only then
    is a BlueZ pairing agent registered for a local adapter; without it an unbonded local
    adapter's pairing is refused, which is what a missed window looks like anyway.

    ``disconnected_callback`` is called when the pump or the adapter drops the live link, not
    when this transport disconnects on purpose.

    ``async with BleTransport(device) as transport:`` connects on entry and disconnects on exit.
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
        self._link = BondedLink(
            device,
            notify_uuid=GENI_CHARACTERISTIC_UUID,
            assembler=FrameAssembler(replies_only=True, max_length=MAX_REPLY_LENGTH),
            policy=POLICY,
            name=name,
            max_attempts=max_attempts,
            connect_timeout=connect_timeout,
            request_timeout=request_timeout,
            disconnected_callback=disconnected_callback,
            device_callback=device_callback,
            expect_new_bond=pairing,
        )
        self.last_frames = self._link.last_frames
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
        return self._link.device

    @property
    def is_connected(self) -> bool:
        """True while the BLE link is up."""
        return self._link.is_connected

    @property
    def reply_seen(self) -> bool:
        """True once the pump has answered on the current or last link."""
        return self._link.reply_seen

    @property
    def last_source(self) -> str | None:
        """The Bluetooth source (adapter or proxy) the latest connection attempt went through.

        Under Home Assistant this is the scanner habluetooth chose for the attempt; elsewhere it
        is the source the device was last heard through when the attempt started. None when
        neither is known.
        """
        return self._link.last_source

    async def connect(self) -> None:
        """Connect through the device's path, pair (encrypt) and subscribe to replies."""
        with _translated():
            await self._link.connect()

    async def disconnect(self) -> None:
        """Unsubscribe and drop the link; safe to call when not connected."""
        await self._link.disconnect()

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
        wait goes on within the same deadline. A request that does not complete with a reply
        closes the link: replies carry no request id, so a late answer on the same link would
        otherwise be taken for the next request's reply.
        """

        def accept(frame: bytes) -> bool:
            reply = Telegram.decode_reply(frame)
            if reply.sd == SD_ERROR:
                return True
            return reply.answers(telegram) and (expect is None or expect(reply))

        with _translated():
            raw = await self._link.request(telegram.encode(), accept=accept, timeout=timeout)
        reply = Telegram.decode_reply(raw)
        if reply.sd == SD_ERROR:
            raise ProtocolError("the pump sent an error reply", raw)
        return reply

    async def send(self, telegram: Telegram) -> None:
        """Write ``telegram`` without waiting for a reply; a failed write closes the link."""
        with _translated():
            await self._link.send(telegram.encode())


class _translated:  # noqa: N801 - used as a context manager, reads like one
    """Turn ble-bond's exceptions into this library's, keeping the cause."""

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not isinstance(exc, ble_bond.LinkError):
            return
        mapped: GrundfosConnectionError
        if isinstance(exc, ble_bond.PairingRequired):
            mapped = PairingRequired(str(exc), exc.error_code)
        elif isinstance(exc, ble_bond.PairingUnsupported):
            mapped = PairingUnsupported(str(exc), exc.error_code)
        elif isinstance(exc, ble_bond.RequestTimeout):
            mapped = RequestTimeout(str(exc), exc.error_code)
        elif isinstance(exc, ble_bond.ConnectionLost):
            mapped = ConnectionLost(str(exc), exc.error_code)
        else:
            mapped = GrundfosConnectionError(str(exc), exc.error_code)
        raise mapped from exc
