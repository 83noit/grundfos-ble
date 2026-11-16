"""Exceptions raised by grundfos-ble."""

from __future__ import annotations


class GrundfosError(Exception):
    """Base class for every error raised by this library."""


class ProtocolError(GrundfosError):
    """A frame or APDU did not look like GENIbus, or not like what was asked for.

    ``raw`` holds the offending bytes when they are available, so callers can log or
    store them for later analysis.
    """

    def __init__(self, message: str, raw: bytes | None = None) -> None:
        super().__init__(message)
        self.raw = raw


class CrcError(ProtocolError):
    """The frame's CRC did not match its contents."""


class NackError(ProtocolError):
    """The pump answered an APDU with a non-OK acknowledgement."""

    def __init__(self, ack: int, data_class: int, data: bytes, raw: bytes | None = None) -> None:
        super().__init__(f"class {data_class} answered ACK {ack} (data {data.hex(' ')})", raw)
        self.ack = ack
        self.data_class = data_class
        self.data = data


class ObjectError(ProtocolError):
    """A class-10 object reply carried a non-zero status (busy, failed, unknown)."""

    def __init__(self, status: int, raw: bytes | None = None) -> None:
        super().__init__(f"object reply status {status}", raw)
        self.status = status


class ObjectVersionError(ProtocolError):
    """A class-10 object came back with a version this library has no layout for.

    Raised per object: other objects on the same link are unaffected. ``raw`` holds the frame
    so the new layout can be worked out from it.
    """

    def __init__(
        self, object_type: int, expected: int, received: int, raw: bytes | None = None
    ) -> None:
        super().__init__(
            f"object type {object_type} came back as version {received}, expected {expected}",
            raw,
        )
        self.object_type = object_type
        self.expected = expected
        self.received = received


class NotSupported(GrundfosError):
    """The pump does not expose this data item or command, or this release does not support it."""


class GrundfosConnectionError(GrundfosError):
    """Connecting to or talking to the pump failed at the Bluetooth level.

    ``error_code`` holds the adapter's numeric reason when it reported one.
    """

    def __init__(self, message: str, error_code: int | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


class PairingUnsupported(GrundfosConnectionError):
    """The Bluetooth path cannot pair at all, so it can never reach the pump.

    Raised when an ESPHome proxy's firmware has no pairing support (bleak-esphome's "Pairing
    is not available in this version ESPHome"). The pump answers only an encrypted link, so
    the proxy's ESPHome has to be updated; pressing the pump's button cannot help.
    """


class PairingRequired(GrundfosConnectionError):
    """The pump refused pairing.

    The adapter or proxy holds no bond the pump accepts and the pump's Bluetooth button was
    not pressed within the pairing window. Raised for ESP-IDF SMP failure reason 82
    ("pairing not supported"), as reported through ESPHome proxies, and for BlueZ's
    ``AuthenticationFailed`` on a local Linux adapter; other backends' pairing failures
    surface as :class:`GrundfosConnectionError`. The caller has to run the pairing
    procedure again: press the button, connect within ~3 s, pair.
    """


class ConnectionLost(GrundfosConnectionError):
    """The pump dropped the connection while a request was in flight."""


class RequestTimeout(GrundfosConnectionError):
    """No reply arrived within the request timeout."""
