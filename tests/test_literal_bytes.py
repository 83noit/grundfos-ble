"""Literal-byte tests for the gaps the mutation pass found (review 2026-11, G2).

Each test states the expected bytes or numbers itself instead of building them from the
constants under test, so a wrong constant or operator in the library fails here.
"""

from __future__ import annotations

import asyncio

import pytest

from grundfos_ble.advertisement import parse_advertisement
from grundfos_ble.exceptions import ConnectionLost, ProtocolError, RequestTimeout
from grundfos_ble.genibus import classic
from grundfos_ble.genibus.apdu import Apdu
from grundfos_ble.genibus.objects import (
    ObjectRef,
    get_request,
    parse_get_reply,
    read_f32,
    read_f64,
    set_request,
)
from grundfos_ble.genibus.telegram import MAX_REPLY_LENGTH, FrameAssembler, Telegram
from grundfos_ble.pump import GrundfosPump, decode_fault_array, decode_measurements
from grundfos_ble.registry import alpha2go
from grundfos_ble.registry.alpha2go import ControlMode, StringId
from grundfos_ble.testing import FIXTURES, ReplayTransport

NA = "7f ff ff ff"


def test_string_ids_are_the_documented_numbers() -> None:
    assert {s.name: int(s) for s in StringId} == {
        "PRODUCT_NAME": 1,
        "PRODUCT_NUMBER": 8,
        "SERIAL_NUMBER": 9,
        "PRODUCTION_CODE": 10,
        "UNIT_NAME": 17,
        "MODEL": 19,
        "GSC_FILE": 21,
        "SOFTWARE_VERSION": 50,
        "BLE_SOFTWARE_VERSION": 58,
        "ECU_ID": 100,
    }


def test_reply_side_start_delimiters() -> None:
    error = Telegram(0x25, 0xF8, 0xE7, (Apdu(10, 0, b"\x00"),)).encode()
    assert error[0] == 0x25
    assert Telegram.decode_reply(error).is_reply
    message = Telegram(0x26, 0xF8, 0xE7, (Apdu(10, 0, b"\x00"),)).encode()
    with pytest.raises(ProtocolError, match="not a reply"):
        Telegram.decode_reply(message)
    misaddressed = Telegram(0x24, 0xF8, 0xE6, (Apdu(10, 0, b"\x00"),)).encode()
    with pytest.raises(ProtocolError, match="addressed"):
        Telegram.decode_reply(misaddressed)


def test_reply_shape_follows_the_request() -> None:
    request = Telegram.request(Apdu(2, 0, b"\x94"), Apdu(7, 0, b"\x01"))
    good = Telegram(0x24, 0xF8, 0xE7, (Apdu(2, 0, b"\x34"), Apdu(7, 0, b"A\x00")))
    swapped = Telegram(0x24, 0xF8, 0xE7, (Apdu(7, 0, b"A\x00"), Apdu(2, 0, b"\x34")))
    short = Telegram(0x24, 0xF8, 0xE7, (Apdu(2, 0, b"\x34"),))
    assert good.answers(request)
    assert not swapped.answers(request)
    assert not short.answers(request)


def test_reply_length_bound() -> None:
    assert MAX_REPLY_LENGTH == 71
    assembler = FrameAssembler(clock=lambda: 0.0, replies_only=True, max_length=71)
    too_long = bytes.fromhex("24 44 f8 e7") + bytes(68)  # LEN 68 > 67
    assert assembler.feed(too_long) == []
    assert assembler.dropped_bytes > 0


def test_flow_and_head_fall_back_to_the_si_values() -> None:
    # flow 0.000277778 m³/s (39 91 a2 b4) and 9804 Pa (46 19 30 00); m³/h and m slots N/A.
    process = bytes.fromhex(f"39 91 a2 b4 46 19 30 00 {NA} {NA} {NA} {NA} {NA} {NA} {NA}")
    motor = bytes(36)
    values = decode_measurements(process, motor)
    assert values.flow_m3h == pytest.approx(1.0, abs=1e-4)
    assert values.head_m == pytest.approx(1.0, abs=1e-6)


def test_two_alarms_and_odd_lengths() -> None:
    assert decode_fault_array(bytes.fromhex("00 33 00 28")) == (51, 40)
    assert decode_fault_array(bytes.fromhex("00 33 00 28 00 00 00 04")) == (51, 40)
    assert decode_fault_array(bytes.fromhex("00 33 00")) == (51,)
    assert decode_fault_array(b"") == ()


def test_a_bare_object_status_is_kept() -> None:
    (reply,) = parse_get_reply(Apdu(10, 0, b"\x04"))
    assert (reply.header.status, reply.header.type, reply.data) == (4, 0, b"")
    (busy,) = parse_get_reply(Apdu(10, 0, bytes.fromhex("02 00 00 00 00 00 00 aa")))
    assert (busy.header.status, busy.data) == (2, b"\xaa")


def test_set_size_boundary() -> None:
    set_request(ObjectRef(86, 16), 302, 1, bytes(54))
    with pytest.raises(ValueError, match="54 bytes"):
        set_request(ObjectRef(86, 16), 302, 1, bytes(55))


def test_info_head_bits() -> None:
    info = classic.InfoField(0x20 | 0x10 | 0x02, 0x80 | 25, 10, 254)
    assert info.value_255_legal
    assert info.low_order_byte
    assert info.scale_info == classic.ScaleInfo.SCALED
    assert info.zero_negative
    assert info.unit == ("m", 1)
    assert info.scale(127) == pytest.approx(-10 + 127)
    assert info.scale(255) == pytest.approx(-10 + 255)
    plain = classic.InfoField(0x80)
    assert not plain.value_255_legal
    assert not plain.low_order_byte
    assert plain.scale(255) is None
    assert plain.scale(7) == 7.0
    assert not classic.InfoField(0x02, 25, 0, 254).zero_negative


def test_info_scaling_this_library_cannot_do_is_refused() -> None:
    with pytest.raises(NotImplementedError, match="extended"):
        classic.InfoField(0x03, 25, 0, 254).scale(1)
    with pytest.raises(NotImplementedError, match="unit index 127"):
        classic.InfoField(0x02, 127, 0, 254).scale(1)


def test_sixteen_bit_classes() -> None:
    # The ALPHA2 GO answered class 11 GET 0F with "00 B8" (GO-app capture).
    values = classic.parse_get_reply(Apdu(11, 0, b"\x00\xb8"), 11, (0x0F,))
    assert values == {0x0F: 0xB8}
    with pytest.raises(ProtocolError, match="2 byte"):
        classic.parse_get_reply(Apdu(11, 0, b"\xb8"), 11, (0x0F,))
    assert classic.parse_get_reply(Apdu(2, 0, b"\x34\x06"), 2, (148, 149)) == {148: 52, 149: 6}


def test_non_finite_floats_are_not_values() -> None:
    assert read_f32(bytes.fromhex("7f 80 00 00"), 0) is None  # +inf
    assert read_f32(bytes.fromhex("ff 80 00 00"), 0) is None  # -inf
    assert read_f32(bytes.fromhex("3f 80 00 00"), 0) == 1.0
    assert read_f64(bytes.fromhex("7f f0 00 00 00 00 00 00"), 0) is None
    assert read_f64(bytes.fromhex("3f f0 00 00 00 00 00 00"), 0) == 1.0


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (0x02, "alarm"),
        (0x04, "warning"),
        (0x08, "led_blinking"),
        (0x01, "client_connected"),
        (0x10, "button_pressed"),
    ],
)
def test_each_advert_flag_alone(flags: int, expected: str) -> None:
    advert = parse_advertisement(bytes((3, 1, 0, 52, 6, 6, 0x30, flags, 0xAB, 0xCD)))
    assert advert is not None
    names = ("alarm", "warning", "led_blinking", "client_connected", "button_pressed")
    assert {name for name in names if getattr(advert, name)} == {expected}


def test_setpoint_units_by_mode() -> None:
    assert alpha2go.setpoint_unit(ControlMode.FLOWADAPT) == "m³/s"
    assert alpha2go.setpoint_unit(ControlMode.CONSTANT_FLOW) == "m³/s"
    assert alpha2go.setpoint_unit(ControlMode.PP_AUTOADAPT) == "Pa"
    assert alpha2go.setpoint_unit(ControlMode.CONSTANT_CURVE) == "rpm"
    assert alpha2go.setpoint_unit(ControlMode.ON_OFF) is None
    assert alpha2go.setpoint_display(ControlMode.FLOWADAPT, 0.001) == pytest.approx((3.6, "m³/h"))


# --- The replay transport's faults (G1) -----------------------------------------------------------


def _replay() -> ReplayTransport:
    return ReplayTransport.from_fixture(FIXTURES / "measured-2026-11-09-full.hex")


def _get(ref: ObjectRef) -> bytes:
    return Telegram.request(get_request(ref)).encode()


async def test_replay_lag_and_timeout() -> None:
    transport = _replay()
    transport.lag = 0.05
    request = Telegram.request(get_request(alpha2go.STATISTICS.ref))
    with pytest.raises(RequestTimeout):
        await transport.request(request, timeout=0.01)
    assert transport.closed
    with pytest.raises(ConnectionLost):
        await transport.request(request)
    await transport.connect()
    assert (await asyncio.wait_for(transport.request(request), 1)).is_reply


async def test_replay_busy_once() -> None:
    transport = _replay()
    transport.busy_once.add(_get(alpha2go.STATISTICS.ref))
    result = await GrundfosPump(transport).probe_capabilities((alpha2go.STATISTICS,))
    assert result.supported == {"statistics"}
    assert transport.sent.count(_get(alpha2go.STATISTICS.ref)) == 2
