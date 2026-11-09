"""CRC against every recorded frame."""

import pytest

from grundfos_ble.genibus.crc import crc16, verify_crc
from grundfos_ble.testing import Exchange

from .conftest import (
    GO_SET_AUTONIGHT_ON,
    GO_SET_CONTROL_MODE_14,
    GO_SET_CP_SETPOINT_2_5M,
    GO_SET_MAX_LIMITER_3_0,
    GO_START,
    GO_STOP,
    HWR_NO_ALARM_REPLY,
    HWR_STRING_REPLY,
    REQ_MOTOR_STATE,
    REQ_PROCESS_VALUES,
    SET_ACK_FAILED,
    SET_ACK_OK,
)


@pytest.mark.parametrize(
    "frame",
    [
        REQ_PROCESS_VALUES,
        REQ_MOTOR_STATE,
        GO_SET_CONTROL_MODE_14,
        GO_SET_CP_SETPOINT_2_5M,
        GO_SET_AUTONIGHT_ON,
        GO_SET_MAX_LIMITER_3_0,
        GO_START,
        GO_STOP,
        SET_ACK_OK,
        SET_ACK_FAILED,
        HWR_STRING_REPLY,
        HWR_NO_ALARM_REPLY,
    ],
)
def test_documented_frames_verify(frame: bytes) -> None:
    assert verify_crc(frame)


def test_measured_replies_verify(measured_2026_11_07: list[Exchange], measured_2026_11_09) -> None:
    for exchange in [*measured_2026_11_07, *measured_2026_11_09]:
        assert verify_crc(exchange.request)
        assert verify_crc(exchange.reply)


def test_crc_excludes_start_delimiter() -> None:
    body = REQ_PROCESS_VALUES[1:-2]
    assert crc16(body) == int.from_bytes(REQ_PROCESS_VALUES[-2:], "big")


def test_corruption_is_detected() -> None:
    bad = bytearray(REQ_PROCESS_VALUES)
    bad[5] ^= 0x01
    assert not verify_crc(bad)
    assert not verify_crc(b"\x24\x00\x00")
