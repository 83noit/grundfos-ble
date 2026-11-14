"""BleTransport against a fake bleak client."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest
from bleak import BleakError
from bleak.backends.device import BLEDevice
from bleak.exc import BleakCharacteristicNotFoundError
from bleak_retry_connector import BleakClientWithServiceCache

from grundfos_ble import transport as transport_module
from grundfos_ble.exceptions import (
    ConnectionLost,
    GrundfosConnectionError,
    PairingRequired,
    ProtocolError,
    RequestTimeout,
)
from grundfos_ble.genibus.apdu import Apdu
from grundfos_ble.genibus.objects import ObjectRef, get_request
from grundfos_ble.genibus.telegram import SD_ERROR, Telegram
from grundfos_ble.testing import Exchange
from grundfos_ble.transport import BleTransport

from .conftest import GO_SET_CP_SETPOINT_2_5M, REQ_PROCESS_VALUES


class FakeClient:
    """Enough of BleakClient for the transport: notifications, writes, disconnects."""

    def __init__(self, replies: dict[bytes, bytes] | None = None) -> None:
        self.replies = replies or {}
        self.writes: list[bytes] = []
        self.is_connected = True
        self.mtu_size = 65
        self.notify_cb: Callable[[Any, bytearray], None] | None = None
        self.disconnected_callback: Callable[[Any], None] | None = None
        self.auto_reply = True

    async def start_notify(self, _char: str, callback: Callable[[Any, bytearray], None]) -> None:
        self.notify_cb = callback

    async def write_gatt_char(self, _char: str, data: bytes, response: bool | None = None) -> None:
        assert response is False
        assert len(data) <= 20
        self.writes.append(bytes(data))
        if not self.auto_reply:
            return
        frame = b"".join(self.writes)
        if frame in self.replies and self.notify_cb is not None:
            reply = self.replies[frame]
            self.writes.clear()
            for start in range(0, len(reply), 20):
                self.notify_cb(None, bytearray(reply[start : start + 20]))

    async def disconnect(self) -> None:
        self.is_connected = False

    def drop(self) -> None:
        self.is_connected = False
        assert self.disconnected_callback is not None
        self.disconnected_callback(self)


def _device() -> BLEDevice:
    return BLEDevice("AA:BB:CC:DD:EE:FF", "Test pump", {"source": "00:11:22:33:44:55"})


@pytest.fixture
def fake_establish():
    client = FakeClient()

    async def establish(_cls: Any, _device: BLEDevice, _name: str, **kwargs: Any) -> FakeClient:
        assert kwargs["pair"] is True
        client.disconnected_callback = kwargs["disconnected_callback"]
        return client

    with patch.object(transport_module, "establish_connection", establish):
        yield client


async def test_request_roundtrip(
    fake_establish: FakeClient, measured_2026_11_09: list[Exchange]
) -> None:
    fake_establish.replies = {e.request: e.reply for e in measured_2026_11_09}
    transport = BleTransport(_device(), name="test")
    assert not transport.is_connected
    await transport.connect()
    await transport.connect()  # idempotent
    assert transport.is_connected
    reply = await transport.request(Telegram.request(get_request(ObjectRef(93, 290))))
    assert reply.raw == measured_2026_11_09[0].reply
    assert transport.last_frames[0] == ("tx", REQ_PROCESS_VALUES)
    assert transport.last_frames[-1][0] == "rx"
    await transport.disconnect()
    assert not transport.is_connected
    await transport.disconnect()  # idempotent


async def test_long_frames_are_chunked(fake_establish: FakeClient) -> None:
    fake_establish.auto_reply = False
    transport = BleTransport(_device())
    await transport.connect()
    await transport.send(Telegram.decode(GO_SET_CP_SETPOINT_2_5M))
    assert fake_establish.writes == [GO_SET_CP_SETPOINT_2_5M[:20], GO_SET_CP_SETPOINT_2_5M[20:]]


async def test_timeout(fake_establish: FakeClient) -> None:
    fake_establish.auto_reply = False
    transport = BleTransport(_device(), request_timeout=0.01)
    await transport.connect()
    with pytest.raises(RequestTimeout):
        await transport.request(Telegram.request(get_request(ObjectRef(93, 290))))


async def test_not_connected() -> None:
    transport = BleTransport(_device())
    with pytest.raises(ConnectionLost):
        await transport.request(Telegram.request(get_request(ObjectRef(93, 290))))


async def test_early_drop_is_connection_lost_not_pairing(fake_establish: FakeClient) -> None:
    fake_establish.auto_reply = False
    dropped = []
    transport = BleTransport(_device(), disconnected_callback=lambda: dropped.append(True))
    await transport.connect()
    task = asyncio.create_task(transport.request(Telegram.request(get_request(ObjectRef(93, 290)))))
    await asyncio.sleep(0)
    fake_establish.drop()
    with pytest.raises(ConnectionLost):
        await task
    assert dropped == [True]
    assert not transport.is_connected
    assert not transport.reply_seen


async def test_late_drop_is_connection_lost(
    fake_establish: FakeClient, measured_2026_11_09
) -> None:
    fake_establish.replies = {e.request: e.reply for e in measured_2026_11_09}
    transport = BleTransport(_device())
    await transport.connect()
    await transport.request(Telegram.request(get_request(ObjectRef(93, 290))))  # a reply was seen
    fake_establish.auto_reply = False
    task = asyncio.create_task(transport.request(Telegram.request(get_request(ObjectRef(87, 69)))))
    await asyncio.sleep(0)
    fake_establish.drop()
    with pytest.raises(ConnectionLost):
        await task


async def test_pairing_error_code_is_extracted() -> None:
    async def establish(*_args: Any, **_kwargs: Any) -> FakeClient:
        raise BleakError("Pairing failed due to error: 82")

    with (
        patch.object(transport_module, "establish_connection", establish),
        pytest.raises(PairingRequired) as excinfo,
    ):
        await BleTransport(_device()).connect()
    assert excinfo.value.error_code == 82


async def test_other_connect_errors() -> None:
    async def establish(*_args: Any, **_kwargs: Any) -> FakeClient:
        raise BleakError("No backend with an available connection slot")

    with (
        patch.object(transport_module, "establish_connection", establish),
        pytest.raises(GrundfosConnectionError, match="connection slot"),
    ):
        await BleTransport(_device()).connect()

    async def establish_timeout(*_args: Any, **_kwargs: Any) -> FakeClient:
        raise TimeoutError

    with (
        patch.object(transport_module, "establish_connection", establish_timeout),
        pytest.raises(GrundfosConnectionError, match="timed out"),
    ):
        await BleTransport(_device()).connect()


async def test_notify_failure_disconnects(fake_establish: FakeClient) -> None:
    async def failing_notify(*_args: Any) -> None:
        raise BleakError("GATT error")

    fake_establish.start_notify = failing_notify  # type: ignore[method-assign]
    transport = BleTransport(_device())
    with pytest.raises(GrundfosConnectionError, match="GATT"):
        await transport.connect()
    assert not transport.is_connected


async def test_write_error_is_translated(fake_establish: FakeClient) -> None:
    async def failing_write(*_args: Any, **_kwargs: Any) -> None:
        raise BleakError("write failed")

    fake_establish.write_gatt_char = failing_write  # type: ignore[method-assign]
    transport = BleTransport(_device())
    await transport.connect()
    with pytest.raises(GrundfosConnectionError, match="write failed"):
        await transport.request(Telegram.request(get_request(ObjectRef(93, 290))))
    with pytest.raises(GrundfosConnectionError, match="write failed"):
        await transport.send(Telegram.request(get_request(ObjectRef(93, 290))))


async def test_unsolicited_frame_is_ignored(
    fake_establish: FakeClient, measured_2026_11_09
) -> None:
    transport = BleTransport(_device())
    await transport.connect()
    assert fake_establish.notify_cb is not None
    fake_establish.notify_cb(None, bytearray(measured_2026_11_09[0].reply))
    assert transport.last_frames[-1] == ("rx", measured_2026_11_09[0].reply)
    assert transport.device.address == "AA:BB:CC:DD:EE:FF"


def _establish_sequence(*outcomes: Any):
    remaining = list(outcomes)
    calls: list[dict[str, Any]] = []

    async def establish(_cls: Any, _device: BLEDevice, _name: str, **kwargs: Any) -> Any:
        calls.append(kwargs)
        outcome = remaining.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return establish, calls


@pytest.mark.parametrize("code", [99, 102, 96, 92, -1])
async def test_transient_smp_reasons_are_not_pairing_required(code: int) -> None:
    establish, _ = _establish_sequence(BleakError(f"Pairing failed due to error: {code}"))
    with (
        patch.object(transport_module, "establish_connection", establish),
        pytest.raises(GrundfosConnectionError) as excinfo,
    ):
        await BleTransport(_device(), max_attempts=1).connect()
    assert not isinstance(excinfo.value, PairingRequired)
    assert excinfo.value.error_code == code


async def test_refusal_on_the_first_attempt_is_not_lost_to_a_retry() -> None:
    establish, calls = _establish_sequence(
        BleakError("Pairing failed due to error: 82"), BleakError("Disconnected during connect")
    )
    with (
        patch.object(transport_module, "establish_connection", establish),
        pytest.raises(PairingRequired),
    ):
        await BleTransport(_device(), max_attempts=2).connect()
    assert len(calls) == 1
    assert calls[0]["max_attempts"] == 1


async def test_retry_after_an_ordinary_failure() -> None:
    client = FakeClient()
    establish, calls = _establish_sequence(BleakError("no slot"), client)
    with (
        patch.object(transport_module, "establish_connection", establish),
        patch.object(transport_module, "RETRY_DELAY", 0),
    ):
        transport = BleTransport(_device(), max_attempts=2)
        await transport.connect()
    assert transport.is_connected
    assert len(calls) == 2


async def test_connect_timeout_is_enforced() -> None:
    async def establish(*_args: Any, **_kwargs: Any) -> FakeClient:
        await asyncio.sleep(30)
        raise AssertionError

    with (
        patch.object(transport_module, "establish_connection", establish),
        pytest.raises(GrundfosConnectionError, match="timed out"),
    ):
        await BleTransport(_device(), max_attempts=1, connect_timeout=0.01).connect()


@pytest.mark.parametrize("error", [EOFError("x"), BrokenPipeError("x"), NotImplementedError("x")])
async def test_foreign_link_errors_are_translated(error: Exception) -> None:
    establish, _ = _establish_sequence(error)
    with (
        patch.object(transport_module, "establish_connection", establish),
        pytest.raises(GrundfosConnectionError),
    ):
        await BleTransport(_device(), max_attempts=1).connect()


async def test_timeout_closes_the_link_so_a_late_reply_cannot_answer_the_next_request(
    fake_establish: FakeClient,
) -> None:
    fake_establish.auto_reply = False
    transport = BleTransport(_device(), request_timeout=0.01)
    await transport.connect()
    with pytest.raises(RequestTimeout):
        await transport.request(Telegram.request(get_request(ObjectRef(93, 290))))
    assert not transport.is_connected
    with pytest.raises(ConnectionLost):
        await transport.request(Telegram.request(get_request(ObjectRef(87, 69))))


async def test_timeout_covers_a_stalled_write(fake_establish: FakeClient) -> None:
    async def stalled(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.sleep(30)

    fake_establish.write_gatt_char = stalled  # type: ignore[method-assign]
    transport = BleTransport(_device(), request_timeout=0.01)
    await transport.connect()
    with pytest.raises(RequestTimeout):
        await transport.request(Telegram.request(get_request(ObjectRef(93, 290))))


async def test_failed_subscribe_with_timeout_closes_the_link(fake_establish: FakeClient) -> None:
    async def failing_notify(*_args: Any) -> None:
        raise TimeoutError

    fake_establish.start_notify = failing_notify  # type: ignore[method-assign]
    transport = BleTransport(_device())
    with pytest.raises(GrundfosConnectionError):
        await transport.connect()
    assert not transport.is_connected


async def test_late_callback_from_an_old_link_is_ignored(fake_establish: FakeClient) -> None:
    transport = BleTransport(_device())
    await transport.connect()
    transport._on_disconnect(FakeClient())  # type: ignore[arg-type]
    assert transport.is_connected


async def test_error_reply_is_a_protocol_error(fake_establish: FakeClient) -> None:
    request = Telegram.request(get_request(ObjectRef(93, 290)))
    payload = Telegram(SD_ERROR, 0xF8, 0xE7, (Apdu(10, 0, b"\x00"),)).encode()
    fake_establish.replies = {request.encode(): payload}
    transport = BleTransport(_device())
    await transport.connect()
    with pytest.raises(ProtocolError, match="error reply"):
        await transport.request(request)


def test_a_reply_outlasts_the_link_supervision_timeout() -> None:
    """A stalled link that recovers within ~6 s must still answer (ha-grundfos-ble#3)."""
    assert transport_module.DEFAULT_REQUEST_TIMEOUT > 6.0


async def test_each_attempt_asks_for_the_current_device() -> None:
    first = _device()
    second = BLEDevice("AA:BB:CC:DD:EE:FF", "Test pump", {"source": "66:77:88:99:AA:BB"})
    devices = iter([first, None, second])
    seen: list[BLEDevice] = []

    async def establish(_cls: Any, device: BLEDevice, _name: str, **_kwargs: Any) -> FakeClient:
        seen.append(device)
        if len(seen) < 3:
            raise BleakError("no slot")
        return FakeClient()

    with (
        patch.object(transport_module, "establish_connection", establish),
        patch.object(transport_module, "RETRY_DELAY", 0),
    ):
        transport = BleTransport(first, max_attempts=3, device_callback=lambda: next(devices))
        await transport.connect()
    assert seen == [first, first, second]  # None keeps the last device
    assert transport.device is second


async def test_a_missing_characteristic_clears_the_service_cache() -> None:
    class CachedClient(BleakClientWithServiceCache):
        def __init__(self) -> None:
            self.cleared = False
            self.connected = True

        @property
        def is_connected(self) -> bool:
            return self.connected

        async def start_notify(self, *_args: Any, **_kwargs: Any) -> None:
            raise BleakCharacteristicNotFoundError("geni")

        async def clear_cache(self) -> bool:
            self.cleared = True
            return True

        async def disconnect(self) -> None:
            self.connected = False

    client = CachedClient()
    establish, _ = _establish_sequence(client)
    with (
        patch.object(transport_module, "establish_connection", establish),
        pytest.raises(GrundfosConnectionError, match="not found"),
    ):
        await BleTransport(_device(), max_attempts=1).connect()
    assert client.cleared
    assert not client.connected
