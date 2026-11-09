"""Telegram encode/decode and reassembly of notification pieces."""

import pytest

from grundfos_ble.exceptions import CrcError, ProtocolError
from grundfos_ble.genibus.apdu import Ack, Apdu, DataClass, Operation
from grundfos_ble.genibus.objects import ObjectRef, get_request
from grundfos_ble.genibus.telegram import (
    BLE_MASTER_ADDRESS,
    BLE_PUMP_ADDRESS,
    SD_REPLY,
    SD_REQUEST,
    FrameAssembler,
    Telegram,
    frame_length,
)
from grundfos_ble.testing import Exchange

from .conftest import REQ_MOTOR_STATE, REQ_PROCESS_VALUES


def test_encode_matches_measured_requests() -> None:
    assert Telegram.request(get_request(ObjectRef(93, 290))).encode() == REQ_PROCESS_VALUES
    assert Telegram.request(get_request(ObjectRef(87, 69))).encode() == REQ_MOTOR_STATE


def test_decode_measured_reply(measured_2026_11_09: list[Exchange]) -> None:
    reply = Telegram.decode(measured_2026_11_09[0].reply)
    assert reply.sd == SD_REPLY
    assert reply.da == BLE_MASTER_ADDRESS
    assert reply.sa == BLE_PUMP_ADDRESS
    assert reply.is_reply
    assert reply.raw == measured_2026_11_09[0].reply
    apdu = reply.apdu
    assert apdu.data_class == DataClass.DATA_OBJECTS
    assert apdu.ack == Ack.OK
    assert len(apdu.data) == 43


def test_roundtrip_multi_apdu() -> None:
    telegram = Telegram.request(
        Apdu(DataClass.MEASURED_DATA, Operation.GET, bytes([148, 149, 150])),
        Apdu(DataClass.COMMANDS, Operation.SET, bytes([6])),
    )
    decoded = Telegram.decode(telegram.encode())
    assert decoded == telegram
    assert decoded.sd == SD_REQUEST
    with pytest.raises(ProtocolError):
        _ = decoded.apdu  # two APDUs


def test_decode_rejects_bad_length_and_crc() -> None:
    with pytest.raises(ProtocolError):
        Telegram.decode(REQ_PROCESS_VALUES[:-1])
    corrupt = bytearray(REQ_PROCESS_VALUES)
    corrupt[-1] ^= 0xFF
    with pytest.raises(CrcError):
        Telegram.decode(corrupt)
    with pytest.raises(ProtocolError):
        Telegram.decode(b"\x00\x05\xe7\xf8\x03\x81\x06\xe5\x87")


def test_frame_length() -> None:
    assert frame_length(b"\x24") is None
    assert frame_length(b"\x24\x2f") == 51


def test_assembler_joins_three_pieces(measured_2026_11_09: list[Exchange]) -> None:
    assembler = FrameAssembler(clock=lambda: 0.0)
    reply = measured_2026_11_09[1].reply
    pieces = [reply[0:20], reply[20:40], reply[40:]]
    assert assembler.feed(pieces[0]) == []
    assert assembler.feed(pieces[1]) == []
    assert assembler.feed(pieces[2]) == [reply]
    assert assembler.feed(reply) == [reply]  # a whole frame in one piece


def test_assembler_splits_back_to_back_frames() -> None:
    assembler = FrameAssembler(clock=lambda: 0.0)
    two = REQ_PROCESS_VALUES + REQ_MOTOR_STATE
    assert assembler.feed(two[:15]) == [REQ_PROCESS_VALUES]
    assert assembler.feed(two[15:]) == [REQ_MOTOR_STATE]


def test_assembler_discards_stale_partial() -> None:
    now = [0.0]
    assembler = FrameAssembler(chunk_timeout=0.5, clock=lambda: now[0])
    assert assembler.feed(REQ_MOTOR_STATE[:5]) == []
    now[0] = 1.0
    assert assembler.feed(REQ_PROCESS_VALUES) == [REQ_PROCESS_VALUES]
    assert assembler.discarded_partials == 1


def test_assembler_resyncs_on_garbage() -> None:
    assembler = FrameAssembler(clock=lambda: 0.0)
    assert assembler.feed(b"\x00\x01" + REQ_PROCESS_VALUES) == [REQ_PROCESS_VALUES]
    assert assembler.dropped_bytes == 2
    assert assembler.feed(b"\x99\x98") == []
    assert assembler.dropped_bytes == 4
    assembler.reset()
    assert assembler.feed(REQ_MOTOR_STATE) == [REQ_MOTOR_STATE]


def test_payload_limit() -> None:
    apdus = [Apdu(DataClass.MEASURED_DATA, Operation.GET, bytes(63)) for _ in range(4)]
    with pytest.raises(ValueError, match="exceeds 255"):
        Telegram.request(*apdus).encode()


def test_replies_only_assembler_rejects_false_starts(measured_2026_11_09: list[Exchange]) -> None:
    assembler = FrameAssembler(clock=lambda: 0.0, replies_only=True)
    good = measured_2026_11_09[0].reply
    assert assembler.feed(b"\x27\xff" + good) == [good]  # a request-like start cannot hold it up
    assert assembler.feed(b"\x24\x00\xf8\xe7" + good) == [good]  # LEN too small
    assert assembler.dropped_bytes == 6


def test_replies_only_assembler_rescans_after_a_bad_crc(
    measured_2026_11_09: list[Exchange],
) -> None:
    assembler = FrameAssembler(clock=lambda: 0.0, replies_only=True)
    good = measured_2026_11_09[0].reply
    false_start = bytes((0x24, 0x05, 0xF8, 0xE7, 0x00, 0x00, 0x00, 0x00, 0x00))
    assert assembler.feed(false_start + good) == [good]


def test_invalid_class_byte_is_a_protocol_error() -> None:
    from grundfos_ble.genibus.crc import crc16

    body = bytes((0x04, 0xF8, 0xE7, 0x1A, 0x00))
    frame = bytes((0x24,)) + body + crc16(body).to_bytes(2, "big")
    with pytest.raises(ProtocolError, match="class byte"):
        Telegram.decode(frame)
