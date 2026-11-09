"""Classic classes: commands, strings, identity, INFO."""

import pytest

from grundfos_ble.exceptions import NackError, ProtocolError
from grundfos_ble.genibus import classic
from grundfos_ble.genibus.apdu import Ack, Apdu, DataClass, Operation
from grundfos_ble.genibus.telegram import Telegram

from .conftest import (
    GO_IDENTITY_REVERSED,
    GO_START,
    GO_STOP,
    GO_STRING_PRODUCT_NAME,
    HWR_STRING_REPLY,
)


def test_commands_match_go_app_frames() -> None:
    assert Telegram.request(classic.command(6)).encode() == GO_START
    assert Telegram.request(classic.command(5)).encode() == GO_STOP


def test_string_request_and_reply() -> None:
    request = classic.get_request(DataClass.ASCII_STRINGS, (1,))
    assert Telegram.request(request).encode() == GO_STRING_PRODUCT_NAME
    reply = Telegram.decode(HWR_STRING_REPLY)
    assert classic.parse_string_reply(reply.apdu) == "ALPHA HWR"
    with pytest.raises(NackError):
        classic.parse_string_reply(Apdu(DataClass.ASCII_STRINGS, Ack.ID_UNKNOWN, b"\x63"))
    with pytest.raises(ProtocolError, match="expected class 7"):
        classic.parse_string_reply(Apdu(DataClass.MEASURED_DATA, Ack.OK, b""))


def test_identity_request_matches_go_app_order() -> None:
    request = classic.get_request(DataClass.MEASURED_DATA, (150, 149, 148))
    assert Telegram.request(request).encode() == GO_IDENTITY_REVERSED


def test_get_reply_parsing() -> None:
    values = classic.parse_get_reply(
        Apdu(DataClass.MEASURED_DATA, Ack.OK, b"\x34\x06\x00"),
        DataClass.MEASURED_DATA,
        (148, 149, 150),
    )
    assert values == {148: 52, 149: 6, 150: 0}
    with pytest.raises(ProtocolError, match="asked for 3"):
        classic.parse_get_reply(
            Apdu(DataClass.MEASURED_DATA, Ack.OK, b"\x34"), DataClass.MEASURED_DATA, (148, 149, 150)
        )


def test_set_request_and_command_reply() -> None:
    assert (
        classic.set_request(DataClass.REFERENCE_VALUES, [(1, 127)]).encode() == b"\x05\x82\x01\x7f"
    )
    classic.parse_command_reply(Apdu(DataClass.COMMANDS, Ack.OK, b""))
    with pytest.raises(NackError):
        classic.parse_command_reply(Apdu(DataClass.COMMANDS, Ack.ID_UNKNOWN, b"\x07"))


def test_info_request_matches_spec_example() -> None:
    # GENIbus Protocol Specification 2005 §4.3 example: INFO class 2 IDs 2, 16, 26 to unit 0x20
    # from master 0x01.
    telegram = Telegram.request(
        classic.info_request(DataClass.MEASURED_DATA, (2, 16, 26)), da=0x20, sa=0x01
    )
    assert telegram.encode() == bytes.fromhex("27072001 02c302101a 901c".replace(" ", ""))
    assert telegram.apdu.op == Operation.INFO


def test_info_reply_decoding_and_scaling() -> None:
    # Spec example: temperature with UNIT 21 (1 degC), ZERO 10, RANGE 90; value 163 -> 68 degC.
    reply = Apdu(
        DataClass.MEASURED_DATA, Ack.OK, bytes([0x82, 21, 10, 90, 0x80, 0x82 | 0x20, 0x81, 0, 50])
    )
    fields = classic.parse_info_reply(reply, DataClass.MEASURED_DATA, (29, 94, 30))
    temp = fields[29]
    assert temp.scale_info == classic.ScaleInfo.SCALED
    assert temp.unit == ("°C", 1)
    assert temp.scale(163) == pytest.approx(68.0, abs=0.5)  # the spec rounds to 68
    assert temp.scale(255) is None  # not available (VI = 0)
    assert fields[94].scale_info == classic.ScaleInfo.NONE
    assert fields[94].scale(7) == 7.0
    legal255 = fields[30]
    assert legal255.value_255_legal
    assert legal255.zero_negative
    assert legal255.unit == ("A", 0.1)
    assert legal255.scale(254) == pytest.approx((-0 + 254 * 50 / 254) * 0.1)
    with pytest.raises(ProtocolError, match="shorter"):
        classic.parse_info_reply(
            Apdu(DataClass.MEASURED_DATA, Ack.OK, b"\x80"), DataClass.MEASURED_DATA, (1, 2)
        )
    with pytest.raises(ProtocolError, match="truncated"):
        classic.parse_info_reply(
            Apdu(DataClass.MEASURED_DATA, Ack.OK, b"\x82\x15"), DataClass.MEASURED_DATA, (1,)
        )


def test_info_low_order_byte_flag() -> None:
    field = classic.InfoField(0x90)
    assert field.low_order_byte
    assert field.unit is None
    assert field.scale(3) == 3.0
