"""APDU and class-10 object encoding against recorded frames."""

import struct

import pytest

from grundfos_ble.exceptions import NackError, ObjectError, ProtocolError
from grundfos_ble.genibus.apdu import Ack, Apdu, DataClass, Operation
from grundfos_ble.genibus.objects import (
    NAN,
    NOT_AVAILABLE,
    ObjectHeader,
    ObjectRef,
    ObjectStatus,
    encode_f32,
    expect_object,
    parse_get_reply,
    parse_set_reply,
    read_f32,
    read_f64,
    read_uint,
    set_request,
)
from grundfos_ble.genibus.telegram import Telegram
from grundfos_ble.testing import Exchange

from .conftest import (
    GO_SET_AUTONIGHT_OFF,
    GO_SET_AUTONIGHT_ON,
    GO_SET_CONTROL_MODE_14,
    SET_ACK_FAILED,
    SET_ACK_OK,
)


def test_apdu_roundtrip_and_limits() -> None:
    apdu = Apdu(DataClass.DATA_OBJECTS, Operation.GET, b"\x5d\x01\x22")
    assert apdu.encode() == b"\x0a\x03\x5d\x01\x22"
    decoded, end = Apdu.decode(b"\xff" + apdu.encode() + b"\xee", 1)
    assert decoded == apdu
    assert end == 6
    with pytest.raises(ValueError, match="nibble"):
        Apdu(16, 0)
    with pytest.raises(ValueError, match="0-3"):
        Apdu(2, 4)
    with pytest.raises(ValueError, match="limited"):
        Apdu(2, 0, bytes(64))
    with pytest.raises(ProtocolError):
        Apdu.decode(b"\x0a")
    with pytest.raises(ProtocolError):
        Apdu.decode(b"\x0a\x05\x01")


def test_object_header_roundtrip() -> None:
    header = ObjectHeader(0, 565, 2, 36)
    assert header.encode() == bytes.fromhex("00023502000024")
    assert ObjectHeader.decode(b"\xaa" + header.encode(), 1) == header
    with pytest.raises(ProtocolError):
        ObjectHeader.decode(b"\x00\x02")


def test_object_ref() -> None:
    ref = ObjectRef(93, 290)
    assert ref.to_bytes() == b"\x5d\x01\x22"
    assert str(ref) == "93/290"
    with pytest.raises(ValueError, match="invalid"):
        ObjectRef(256, 0)


def test_parse_measured_replies(measured_2026_11_09: list[Exchange]) -> None:
    process = Telegram.decode(measured_2026_11_09[0].reply)
    replies = parse_get_reply(process.apdu, process.raw)
    assert len(replies) == 1
    assert replies[0].header == ObjectHeader(0, 565, 2, 36)
    assert len(replies[0].data) == 36
    motor = Telegram.decode(measured_2026_11_09[1].reply)
    (reply,) = parse_get_reply(motor.apdu, motor.raw)
    assert reply.header == ObjectHeader(0, 256, 3, 41)
    assert reply.ok
    expect_object([reply], 256, min_size=24)
    with pytest.raises(ProtocolError, match="expected object type 565"):
        expect_object([reply], 565)
    with pytest.raises(ProtocolError, match="need 100"):
        expect_object([reply], 256, min_size=100)
    with pytest.raises(ProtocolError, match="no object"):
        expect_object([], 256)


def test_parse_get_reply_errors() -> None:
    with pytest.raises(ProtocolError, match="class-10"):
        parse_get_reply(Apdu(DataClass.COMMANDS, Ack.OK, b""))
    with pytest.raises(NackError) as excinfo:
        parse_get_reply(Apdu(DataClass.DATA_OBJECTS, Ack.ID_UNKNOWN, b"\x5d"))
    assert excinfo.value.ack == Ack.ID_UNKNOWN
    # Non-zero status stops parsing but keeps the header.
    data = bytes.fromhex("04 0235 02 000024".replace(" ", "")) + b"\x01\x02"
    replies = parse_get_reply(Apdu(DataClass.DATA_OBJECTS, Ack.OK, data))
    assert replies[0].header.status == ObjectStatus.OPERATION_FAILED
    with pytest.raises(ObjectError) as err:
        expect_object(replies, 565)
    assert err.value.status == 4
    # Size overrun.
    data = ObjectHeader(0, 565, 2, 36).encode() + b"\x00" * 10
    with pytest.raises(ProtocolError, match="claims 36"):
        parse_get_reply(Apdu(DataClass.DATA_OBJECTS, Ack.OK, data))


def test_set_request_matches_go_app_frames() -> None:
    mode_14 = set_request(ObjectRef(86, 10), 303, 1, b"\x00\x06\x0e" + NOT_AVAILABLE)
    assert Telegram.request(mode_14).encode() == GO_SET_CONTROL_MODE_14
    assert Telegram.request(set_request(ObjectRef(90, 8), 333, 1, b"\x01")).encode() == (
        GO_SET_AUTONIGHT_ON
    )
    assert Telegram.request(set_request(ObjectRef(90, 8), 333, 1, b"\x00")).encode() == (
        GO_SET_AUTONIGHT_OFF
    )
    with pytest.raises(ValueError, match="limited"):
        set_request(ObjectRef(1, 1), 1, 1, bytes(60))


def test_parse_set_reply() -> None:
    assert parse_set_reply(Telegram.decode(SET_ACK_OK).apdu) == ObjectStatus.OK
    assert parse_set_reply(Telegram.decode(SET_ACK_FAILED).apdu) == ObjectStatus.OPERATION_FAILED
    assert parse_set_reply(Apdu(10, 0, b"\x07")) == ObjectStatus.OPERATION_FAILED
    with pytest.raises(ProtocolError, match="one byte"):
        parse_set_reply(Apdu(10, 0, b"\x00\x00"))
    with pytest.raises(NackError):
        parse_set_reply(Apdu(10, Ack.ILLEGAL_OPERATION, b""))
    with pytest.raises(ProtocolError, match="class-10"):
        parse_set_reply(Apdu(3, 0, b"\x00"))


def test_field_readers_and_sentinels() -> None:
    data = struct.pack(">f", 1.5) + NOT_AVAILABLE + NAN + struct.pack(">f", float("nan"))
    assert read_f32(data, 0) == 1.5
    assert read_f32(data, 4) is None
    assert read_f32(data, 8) is None
    assert read_f32(data, 12) is None
    assert read_f32(data, 14) is None  # short
    assert read_f64(struct.pack(">d", 2.25), 0) == 2.25
    assert read_f64(struct.pack(">d", float("nan")), 0) is None
    assert read_f64(b"\x00", 0) is None
    assert read_uint(b"\x01\x02\x03", 1, 2) == 0x0203
    assert read_uint(b"\x01", 0, 2) is None
    assert encode_f32(None) == NOT_AVAILABLE
    assert encode_f32(24510.0) == bytes.fromhex("46bf7c00")
