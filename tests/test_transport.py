"""BleTransport: GENIbus on top of ble-bond, against ble-bond's fake client.

The link itself (pairing, retries, recording the path, closing on an unfinished request, the
BlueZ agent) is tested in ble-bond. These tests cover what this library adds: the pump's link
policy, GENIbus reply matching, and turning ble-bond's exceptions into this library's.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import pytest
from ble_bond import link as link_module
from ble_bond.testing import FakeClient, patched_establish
from bleak import BleakError
from bleak.backends.device import BLEDevice

from grundfos_ble import transport as transport_module
from grundfos_ble.bluez import remove_bluez_bond
from grundfos_ble.exceptions import (
    ConnectionLost,
    GrundfosConnectionError,
    PairingRequired,
    PairingUnsupported,
    ProtocolError,
    RequestTimeout,
)
from grundfos_ble.genibus.apdu import Apdu
from grundfos_ble.genibus.objects import ObjectRef, get_request
from grundfos_ble.genibus.telegram import SD_ERROR, Telegram
from grundfos_ble.testing import Exchange
from grundfos_ble.transport import POLICY, BleTransport

from .conftest import GO_SET_CP_SETPOINT_2_5M, REQ_PROCESS_VALUES

PROCESS_VALUES = Telegram.request(get_request(ObjectRef(93, 290)))


def _device(details: dict[str, Any] | None = None) -> BLEDevice:
    return BLEDevice("AA:BB:CC:DD:EE:FF", "Test pump", details or {"source": "00:11:22:33:44:55"})


@pytest.fixture(autouse=True)
def no_pauses() -> Any:
    quick = transport_module.POLICY.__class__(
        settle_after_subscribe=0, subscribe_retry_delay=0, write_chunk=20
    )
    with patch.object(transport_module, "POLICY", quick):
        yield


@pytest.fixture
def client(measured_2026_11_09: list[Exchange]) -> Any:
    client = FakeClient({e.request: e.reply for e in measured_2026_11_09})
    with patched_establish(client) as calls:
        client.calls = calls
        yield client


async def _pending(transport: BleTransport, telegram: Telegram, **kwargs: Any) -> asyncio.Task[Any]:
    task = asyncio.create_task(transport.request(telegram, **kwargs))
    for _ in range(3):
        await asyncio.sleep(0)
    return task


def test_the_pumps_link_policy() -> None:
    """Each value of the ALPHA2 GO's policy, with where it comes from in transport.py."""
    assert POLICY.settle_after_pair == 0.0
    assert POLICY.settle_after_subscribe == 0.5  # esphome#22651
    assert POLICY.write_chunk == 20
    assert not POLICY.retry_unpaired  # the pump never answers an unencrypted link
    assert not POLICY.insufficient_authentication_is_evidence
    assert POLICY.request_timeout > 8.0  # above the link supervision timeout (#3)


async def test_request_roundtrip(client: FakeClient, measured_2026_11_09: list[Exchange]) -> None:
    async with BleTransport(_device(), name="test") as transport:
        assert transport.is_connected
        reply = await transport.request(PROCESS_VALUES)
        assert reply.raw == measured_2026_11_09[0].reply
        assert transport.reply_seen
        assert transport.last_frames[0] == ("tx", REQ_PROCESS_VALUES)
        assert transport.last_source == "00:11:22:33:44:55"
        assert transport.device.address == "AA:BB:CC:DD:EE:FF"
    assert not transport.is_connected
    assert client.calls[0]["pair"] is True


async def test_long_frames_go_in_20_byte_pieces(client: FakeClient) -> None:
    client.auto_reply = False
    transport = BleTransport(_device())
    await transport.connect()
    await transport.send(Telegram.decode(GO_SET_CP_SETPOINT_2_5M))
    assert client.writes == [GO_SET_CP_SETPOINT_2_5M[:20], GO_SET_CP_SETPOINT_2_5M[20:]]


async def test_a_reply_of_another_class_is_not_taken(
    client: FakeClient, measured_2026_11_09: list[Exchange]
) -> None:
    client.auto_reply = False
    transport = BleTransport(_device(), request_timeout=1)
    await transport.connect()
    task = await _pending(transport, PROCESS_VALUES)
    client.notify(Telegram(0x24, 0xF8, 0xE7, (Apdu(7, 0, b"ALPHA\x00"),)).encode())
    assert not task.done()
    client.notify(measured_2026_11_09[0].reply)
    assert (await task).raw == measured_2026_11_09[0].reply


async def test_expect_rejects_a_frame_of_the_right_class(
    client: FakeClient, measured_2026_11_09: list[Exchange]
) -> None:
    client.auto_reply = False
    transport = BleTransport(_device(), request_timeout=0.05)
    await transport.connect()
    task = await _pending(
        transport, PROCESS_VALUES, expect=lambda reply: reply.apdu.data[1:3] == b"\x02\x35"
    )
    client.notify(measured_2026_11_09[1].reply)  # motor state
    with pytest.raises(RequestTimeout, match=r"1 frame\(s\) did not answer the request$"):
        await task
    assert not transport.is_connected


async def test_a_long_false_start_does_not_swallow_the_reply(
    client: FakeClient, measured_2026_11_09: list[Exchange]
) -> None:
    client.auto_reply = False
    transport = BleTransport(_device(), request_timeout=1)
    await transport.connect()
    task = await _pending(transport, PROCESS_VALUES)
    client.notify(bytes.fromhex("24 40 f8 e7 0a"))
    client.notify(measured_2026_11_09[0].reply)
    assert (await task).raw == measured_2026_11_09[0].reply


async def test_an_error_reply_is_a_protocol_error(client: FakeClient) -> None:
    error = Telegram(SD_ERROR, 0xF8, 0xE7, (Apdu(10, 0, b"\x00"),)).encode()
    client.replies = {PROCESS_VALUES.encode(): error}
    transport = BleTransport(_device())
    await transport.connect()
    with pytest.raises(ProtocolError, match="error reply"):
        await transport.request(PROCESS_VALUES)


async def test_a_timeout_is_this_librarys_and_closes_the_link(client: FakeClient) -> None:
    client.auto_reply = False
    transport = BleTransport(_device(), request_timeout=0.01)
    await transport.connect()
    with pytest.raises(RequestTimeout, match="nothing received") as caught:
        await transport.request(PROCESS_VALUES)
    assert isinstance(caught.value, GrundfosConnectionError)
    assert not transport.is_connected
    with pytest.raises(ConnectionLost):
        await transport.request(PROCESS_VALUES)


async def test_a_lost_link_is_connection_lost(client: FakeClient) -> None:
    client.auto_reply = False
    transport = BleTransport(_device())
    await transport.connect()
    task = await _pending(transport, PROCESS_VALUES)
    client.drop()
    with pytest.raises(ConnectionLost):
        await task


@pytest.mark.parametrize(
    ("error", "expected", "code"),
    [
        (BleakError("Pairing failed due to error: 82"), PairingRequired, 82),
        (
            BleakError("[org.bluez.Error.AuthenticationFailed] Authentication Failed"),
            PairingRequired,
            None,
        ),
        (
            NotImplementedError("Pairing is not available in this version ESPHome"),
            PairingUnsupported,
            None,
        ),
        (BleakError("Pairing failed due to error: 99"), GrundfosConnectionError, 99),
    ],
    ids=["reason-82", "bluez", "no-pairing", "transient"],
)
async def test_connection_failures_become_this_librarys(
    error: Exception, expected: type[Exception], code: int | None
) -> None:
    async def establish(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    with (
        patch.object(link_module, "establish_connection", establish),
        pytest.raises(GrundfosConnectionError) as caught,
    ):
        await BleTransport(_device(), max_attempts=1).connect()
    assert type(caught.value) is expected
    assert caught.value.error_code == code
    assert caught.value.__cause__ is not None


async def test_pairing_registers_the_bluez_agent_and_polling_does_not() -> None:
    seen: list[bool] = []

    @contextlib.asynccontextmanager
    async def agent(_device: BLEDevice, *, enabled: bool = True) -> AsyncIterator[None]:
        seen.append(enabled)
        yield

    with (
        patched_establish(FakeClient()),
        patch.object(link_module, "bluez_pairing_agent", agent),
    ):
        await BleTransport(_device(), pairing=True).connect()
        await BleTransport(_device()).connect()
    assert seen == [True, False]


def test_the_bluez_helpers_are_still_importable_from_here() -> None:
    assert remove_bluez_bond.__module__ == "ble_bond.bluez"
    assert transport_module.GENI_CHARACTERISTIC_UUID.startswith("859cffd1")
