"""GrundfosPump against recorded frames via the replay transport."""

from unittest.mock import MagicMock

import pytest

from grundfos_ble.exceptions import NotSupported, ObjectError, ProtocolError, RequestTimeout
from grundfos_ble.genibus.objects import ObjectRef
from grundfos_ble.genibus.telegram import Telegram
from grundfos_ble.pump import (
    GrundfosPump,
    UserSetpoint,
    decode_fault_array,
    decode_flow_limiter,
    decode_operation,
    decode_statistics,
    decode_user_setpoint,
    encode_control_mode_request,
    encode_flow_limiter,
    encode_user_setpoint,
)
from grundfos_ble.registry import alpha2go
from grundfos_ble.registry.alpha2go import ControlMode
from grundfos_ble.registry.base import ObjectSpec
from grundfos_ble.testing import Exchange, ReplayTransport

from .conftest import (
    FIXTURES,
    GO_SET_CONTROL_MODE_14,
    GO_SET_CP_SETPOINT_2_5M,
    GO_SET_MAX_LIMITER_3_0,
    GO_START,
    GO_STOP,
    HWR_NO_ALARM_REPLY,
    HWR_STRING_REPLY,
    SET_ACK_FAILED,
    SET_ACK_OK,
)


async def test_measurements_match_display_2026_11_07(replay_2026_11_07: ReplayTransport) -> None:
    pump = GrundfosPump(replay_2026_11_07)
    await replay_2026_11_07.connect()
    values = await pump.read_measurements()
    # The panel showed 1.4 m³/h, 0.1 m, 4 W and 1640 rpm ; the pump sends 235 V.
    assert values.flow_m3h == pytest.approx(1.427, abs=0.01)
    assert values.head_m == pytest.approx(0.148, abs=0.005)
    assert values.differential_pressure_pa == pytest.approx(1454, abs=5)
    assert values.grid_voltage_v == pytest.approx(235.2, abs=0.1)
    assert values.dc_link_voltage_v == pytest.approx(332.7, abs=0.1)
    assert values.current_a == pytest.approx(0.0382, abs=1e-4)
    assert values.power_w == pytest.approx(4.01, abs=0.01)
    assert values.shaft_power_w == pytest.approx(3.98, abs=0.01)
    assert values.speed_rpm == pytest.approx(1640.5, abs=0.5)
    assert values.electronics_temperature_c == pytest.approx(44.0)
    assert values.media_temperature_c is None
    assert values.differential_temperature_k is None
    assert values.raw_process_values
    assert values.raw_motor_state


async def test_measurements_2026_11_09(replay_2026_11_09: ReplayTransport) -> None:
    pump = GrundfosPump(replay_2026_11_09)
    values = await pump.read_measurements()
    assert values.flow_m3h == pytest.approx(0.995, abs=0.005)
    assert values.head_m == pytest.approx(0.4345, abs=0.001)
    assert values.grid_voltage_v == pytest.approx(230.6, abs=0.1)
    assert values.speed_rpm == pytest.approx(1658.5, abs=0.5)
    assert values.electronics_temperature_c == pytest.approx(39.0)
    assert values.kv == pytest.approx(4.775, abs=0.01)


async def test_unknown_request_looks_like_a_silent_pump(replay_2026_11_09: ReplayTransport) -> None:
    pump = GrundfosPump(replay_2026_11_09)
    with pytest.raises(RequestTimeout):
        await pump.read_operation()


def _reply(request: bytes, reply: bytes) -> Exchange:
    return Exchange(request, reply)


def _get_frame(ref: ObjectRef) -> bytes:
    from grundfos_ble.genibus.objects import get_request

    return Telegram.request(get_request(ref)).encode()


async def test_faults_from_hwr_reply() -> None:
    transport = ReplayTransport(
        [
            _reply(_get_frame(ObjectRef(88, 0)), HWR_NO_ALARM_REPLY),
            _reply(_get_frame(ObjectRef(88, 11)), HWR_NO_ALARM_REPLY),
        ]
    )
    faults = await GrundfosPump(transport).read_faults()
    assert faults.alarms == ()
    assert faults.warnings == ()
    assert decode_fault_array(b"\x00\x33\x00\x28\x00\x00\x00\x04") == (51, 40)


async def test_strings_and_identity() -> None:
    from grundfos_ble.genibus import classic
    from grundfos_ble.genibus.apdu import DataClass

    ids = (148, 149, 150)
    identity_request = Telegram.request(classic.get_request(DataClass.MEASURED_DATA, ids)).encode()
    identity_reply = Telegram.request(
        classic.Apdu(DataClass.MEASURED_DATA, 0, b"\x34\x06\x00"), da=0xF8, sa=0xE7, sd=0x24
    ).encode()
    exchanges = [_reply(identity_request, identity_reply)]
    for string_id in alpha2go.StringId:
        request = Telegram.request(
            classic.get_request(DataClass.ASCII_STRINGS, (string_id,))
        ).encode()
        exchanges.append(_reply(request, HWR_STRING_REPLY))
    pump = GrundfosPump(ReplayTransport(exchanges))
    identity = await pump.read_identity()
    assert (identity.family, identity.type, identity.version) == (52, 6, 0)
    assert identity.model == "ALPHA HWR"
    assert identity.profile is alpha2go.ALPHA2_GO
    short = await pump.read_identity(strings=False)
    assert short.model is None
    # A rejected string id yields None.
    nack = Telegram.request(
        classic.Apdu(DataClass.ASCII_STRINGS, 2, b"\x63"), da=0xF8, sa=0xE7, sd=0x24
    ).encode()
    pump2 = GrundfosPump(
        ReplayTransport(
            [
                _reply(
                    Telegram.request(classic.get_request(DataClass.ASCII_STRINGS, (99,))).encode(),
                    nack,
                )
            ]
        )
    )
    assert await pump2.read_string(99) is None


async def test_probe_capabilities_records_frames_and_errors() -> None:
    status_failed = Telegram.request(
        classic_apdu(10, 0, bytes.fromhex("04000000000000")), da=0xF8, sa=0xE7, sd=0x24
    ).encode()
    exchanges = [
        _reply(_get_frame(alpha2go.ALARMS.ref), HWR_NO_ALARM_REPLY),
        _reply(_get_frame(alpha2go.STATISTICS.ref), status_failed),
    ]
    transport = ReplayTransport(exchanges)
    pump = GrundfosPump(transport)
    result = await pump.probe_capabilities((alpha2go.ALARMS, alpha2go.STATISTICS))
    assert result.supported == {"alarms"}
    assert result.frames["alarms"] == HWR_NO_ALARM_REPLY
    assert "ObjectError" in result.errors["statistics"]
    with pytest.raises(RequestTimeout):
        await pump.probe_capabilities((alpha2go.ENERGY,))


def classic_apdu(data_class: int, op: int, data: bytes):
    from grundfos_ble.genibus.apdu import Apdu

    return Apdu(data_class, op, data)


async def test_get_object_maps_statuses_to_not_supported() -> None:
    failed = Telegram.request(classic_apdu(10, 0, b"\x04"), da=0xF8, sa=0xE7, sd=0x24).encode()
    nack = Telegram.request(classic_apdu(10, 2, b"\x58"), da=0xF8, sa=0xE7, sd=0x24).encode()
    transport = ReplayTransport(
        [
            _reply(_get_frame(alpha2go.ALARMS.ref), failed),
            _reply(_get_frame(alpha2go.WARNINGS.ref), nack),
        ]
    )
    pump = GrundfosPump(transport)
    with pytest.raises(NotSupported):
        await pump.get_object(alpha2go.ALARMS)
    with pytest.raises(NotSupported):
        await pump.get_object(alpha2go.WARNINGS)
    unknown_mode = ObjectSpec(key="x", ref=ObjectRef(1, 1), type=None, description="", min_size=4)
    transport2 = ReplayTransport(
        [
            _reply(
                _get_frame(ObjectRef(1, 1)),
                Telegram.request(
                    classic_apdu(10, 0, bytes.fromhex("00000101000001aa")),
                    da=0xF8,
                    sa=0xE7,
                    sd=0x24,
                ).encode(),
            )
        ]
    )
    with pytest.raises(ProtocolError, match="need 4"):
        await GrundfosPump(transport2).get_object(unknown_mode)


def test_decoders_on_captured_object_data() -> None:
    # 86/8 data captured from the GO app: source 1 (local), Auto, constant pressure, 2.50 m.
    state = decode_operation(bytes.fromhex("01000046bf7c00"))
    assert (state.control_source, state.operation_mode, state.control_mode) == (1, 0, 0)
    assert state.setpoint == pytest.approx(24510.0)
    display = state.setpoint_display
    assert display is not None
    assert display[1] == "m"
    assert display[0] == pytest.approx(2.499, abs=0.002)
    assert decode_operation(bytes.fromhex("0006027fffffff")).setpoint_display is None
    # 86/16 user setpoint object as captured (feedback 4, 2.5 m, gains 0.6/1.2/0).
    user = decode_user_setpoint(bytes.fromhex("000446bf7c003f19999a3f99999a00000000"))
    assert user.feedback_id == 4
    assert user.setpoint == pytest.approx(24510.0)
    assert user.kp == pytest.approx(0.6)
    # Flow limiter as captured: id 1, off, 2.00 m³/h.
    limiter = decode_flow_limiter(bytes.fromhex("01003a11a5af3f0000004000000000000000"))
    assert limiter.limiter_id == 1
    assert not limiter.enabled
    assert limiter.limit_m3h == pytest.approx(2.0, abs=0.01)
    stats = decode_statistics(bytes.fromhex("00000010000100030000012c"))
    assert (
        stats.start_count,
        stats.starts_last_hour,
        stats.starts_last_24h,
        stats.operating_time_s,
    ) == (
        16,
        1,
        3,
        300,
    )


def test_encoders_match_go_app_frames() -> None:
    from grundfos_ble.genibus.objects import set_request

    assert (
        Telegram.request(
            set_request(
                alpha2go.CONTROL_MODE_REQUEST.ref,
                303,
                1,
                encode_control_mode_request(ControlMode.CP_AUTOADAPT),
            )
        ).encode()
        == GO_SET_CONTROL_MODE_14
    )
    current = UserSetpoint(feedback_id=4, setpoint=40000.0, kp=0.6, ti=1.2, td=0.0)
    data = encode_user_setpoint(current, 24510.0)
    assert (
        Telegram.request(set_request(ObjectRef(86, 16), 302, 1, data)).encode()
        == GO_SET_CP_SETPOINT_2_5M
    )
    # Max limiter: captured "off, 2.00 m³/h" written back as "on, 3.0 m³/h".
    before = bytes.fromhex("01003a11a5af3f0000004000000000000000")
    data = encode_flow_limiter(before, True, 3.0)
    assert (
        Telegram.request(set_request(ObjectRef(86, 600), 895, 1, data)).encode()
        == GO_SET_MAX_LIMITER_3_0
    )
    assert encode_flow_limiter(before, True, None)[1] == 1
    assert encode_flow_limiter(before, True, None)[2:6] == before[2:6]


@pytest.mark.parametrize(
    "write",
    [
        lambda p: p.set_control_mode(ControlMode.CONSTANT_FLOW),
        lambda p: p.set_setpoint(ControlMode.CONSTANT_FLOW, 1e-4),
        lambda p: p.set_flow_limiter(maximum=True, enabled=True, limit_m3h=1.0),
        lambda p: p.set_autonight(True),
        lambda p: p.start(),
        lambda p: p.stop(),
        lambda p: p.reset_alarm(),
        lambda p: p.command(alpha2go.Command.START),
        lambda p: p.set_object(alpha2go.AUTONIGHT, b"\x01"),
    ],
)
async def test_writes_are_refused_before_anything_is_sent(write) -> None:
    transport = ReplayTransport([])
    with pytest.raises(NotSupported, match="not supported in this release"):
        await write(GrundfosPump(transport))
    assert transport.sent == []


async def test_write_path_sends_go_app_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grundfos_ble.pump.WRITES_ENABLED", True)
    transport = ReplayTransport(
        [
            _reply(GO_SET_CONTROL_MODE_14, SET_ACK_OK),
            _reply(
                GO_START,
                Telegram.request(classic_apdu(3, 0, b""), da=0xF8, sa=0xE7, sd=0x24).encode(),
            ),
            _reply(
                GO_STOP,
                Telegram.request(classic_apdu(3, 0, b""), da=0xF8, sa=0xE7, sd=0x24).encode(),
            ),
            _reply(
                Telegram.request(
                    __import__(
                        "grundfos_ble.genibus.objects", fromlist=["set_request"]
                    ).set_request(ObjectRef(90, 8), 333, 1, b"\x01")
                ).encode(),
                SET_ACK_FAILED,
            ),
        ]
    )
    pump = GrundfosPump(transport)
    await pump.set_control_mode(ControlMode.CP_AUTOADAPT)
    await pump.start()
    await pump.stop()
    with pytest.raises(ObjectError):
        await pump.set_autonight(True)
    assert transport.sent[0] == GO_SET_CONTROL_MODE_14
    with pytest.raises(ValueError, match="no known object type"):
        await pump.set_object(
            ObjectSpec(key="x", ref=ObjectRef(1, 1), type=None, description=""), b""
        )
    with pytest.raises(NotSupported):
        await pump.read_factory_limits(ControlMode.NONE)  # type: ignore[arg-type]


async def test_every_optional_object_as_answered_by_the_real_pump() -> None:
    pump = GrundfosPump(ReplayTransport.from_fixture(FIXTURES / "measured-2026-11-09-full.hex"))
    stats = await pump.read_statistics()
    assert stats.start_count == 5
    assert stats.operating_time_s == 16585
    assert stats.running_time_24h_s == 6385
    assert stats.last_cycle_s == 6385
    assert stats.power_ups == 6
    assert stats.power_on_time_s == 16585
    faults = await pump.read_faults()
    assert (faults.alarms, faults.warnings) == ((), ())
    operation = await pump.read_operation()
    assert operation.control_mode == ControlMode.CONSTANT_FLOW
    display = operation.setpoint_display
    assert display is not None
    assert display[0] == pytest.approx(1.0, abs=1e-3)
    assert display[1] == "m³/h"
    temperatures = await pump.read_temperatures()
    assert temperatures.media_c == pytest.approx(16.05, abs=0.01)
    assert temperatures.pcb_c == pytest.approx(36.84, abs=0.01)
    assert temperatures.ambient_c == pytest.approx(25.23, abs=0.01)
    assert await pump.read_energy_kwh() == pytest.approx(0.03407, abs=1e-4)
    assert await pump.read_autonight() is False
    assert await pump.read_panel_setting() == 1
    maximum, minimum = await pump.read_flow_limiters()
    assert maximum.limit_m3h == pytest.approx(2.0, abs=0.01)
    assert not maximum.enabled
    assert minimum.limit_m3h == pytest.approx(1.0, abs=0.01)
    result = await pump.probe_capabilities()
    assert result.errors == {}


async def test_busy_object_is_tried_again_and_a_bad_frame_does_not_abort_the_probe() -> None:
    def reply(payload: bytes) -> bytes:
        return Telegram.request(classic_apdu(10, 0, payload), da=0xF8, sa=0xE7, sd=0x24).encode()

    busy = reply(bytes.fromhex("02000000000000"))
    transport = ReplayTransport(
        [
            _reply(_get_frame(alpha2go.ALARMS.ref), HWR_NO_ALARM_REPLY),
            _reply(_get_frame(alpha2go.STATISTICS.ref), busy),
        ]
    )
    calls = []
    original = transport.request

    async def counting(telegram: Telegram, *, timeout: float | None = None) -> Telegram:
        calls.append(telegram.encode())
        return await original(telegram, timeout=timeout)

    pump = GrundfosPump(transport)
    pump._transport = MagicMock(request=counting)
    result = await pump.probe_capabilities((alpha2go.ALARMS, alpha2go.STATISTICS))
    assert result.supported == {"alarms"}
    assert len(calls) == 3
    assert "ObjectError" in result.errors["statistics"]
    with pytest.raises(ObjectError):
        await pump.get_object(alpha2go.STATISTICS)
