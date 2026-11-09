"""Shared fixtures: recorded frames from a real ALPHA2 GO and documented GO-app frames."""

from __future__ import annotations

import pytest

from grundfos_ble.testing import FIXTURES, Exchange, ReplayTransport, load_fixture

# Requests measured on the pump (also used by the ESPHome alpha3 component).
REQ_PROCESS_VALUES = bytes.fromhex("2707e7f80a035d0122627c")
REQ_MOTOR_STATE = bytes.fromhex("2707e7f80a035700458acd")

# Frames documented in captured Grundfos GO traffic on an ALPHA2 GO 25-90 (Birkemosen,
# docs/ble-traffic.md, 2026-10-28). Byte sequences are facts; no code was reused.
GO_SET_CONTROL_MODE_14 = bytes.fromhex("2714e7f80a9056000a012f0100000700060e7fffffff87c7")
GO_SET_CP_SETPOINT_2_5M = bytes.fromhex(
    "271fe7f80a9b560010012e01000012000446bf7c003f19999a3f99999a00000000caae"
)
GO_SET_AUTONIGHT_ON = bytes.fromhex("270ee7f80a8a5a0008014d0100000101e1dd")
GO_SET_AUTONIGHT_OFF = bytes.fromhex("270ee7f80a8a5a0008014d0100000100f1fc")
GO_SET_MAX_LIMITER_3_0 = bytes.fromhex(
    "271fe7f80a9b560258037f0100001201013a5a740e3f0000004000000000000000395d"
)
GO_START = bytes.fromhex("2705e7f8038106e587")
GO_STOP = bytes.fromhex("2705e7f8038105d5e4")
GO_STRING_PRODUCT_NAME = bytes.fromhex("2705e7f807010152 38".replace(" ", ""))
GO_IDENTITY_REVERSED = bytes.fromhex("2707e7f80203969594a565")
SET_ACK_OK = bytes.fromhex("2405f8e70a0100aea2")
SET_ACK_FAILED = bytes.fromhex("2405f8e70a0104ee26")
# Class-7 reply measured on an ALPHA HWR (alpha-hwr bench notes): "ALPHA HWR".
HWR_STRING_REPLY = bytes.fromhex("240ef8e7070a414c504841204857520083 8d".replace(" ", ""))
# No-alarm reply to 88/0 captured on an ALPHA HWR (alpha-hwr packet traces).
HWR_NO_ALARM_REPLY = bytes.fromhex("240df8e70a0900023a0100000200 00dc50".replace(" ", ""))


@pytest.fixture
def measured_2026_11_07() -> list[Exchange]:
    return load_fixture(FIXTURES / "measured-2026-11-07.hex")


@pytest.fixture
def measured_2026_11_09() -> list[Exchange]:
    return load_fixture(FIXTURES / "measured-2026-11-09.hex")


@pytest.fixture
def replay_2026_11_07(measured_2026_11_07: list[Exchange]) -> ReplayTransport:
    return ReplayTransport(measured_2026_11_07)


@pytest.fixture
def replay_2026_11_09(measured_2026_11_09: list[Exchange]) -> ReplayTransport:
    return ReplayTransport(measured_2026_11_09)
