"""GrundfosPump against recorded frames via the replay transport."""

from typing import Any
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
from grundfos_ble.testing import Exchange, ReplayTransport, load_fixture

from .conftest import (
    CONSTRUCTED_SET_ACK_FAILED,
    CONSTRUCTED_SET_ACK_OK,
    CONSTRUCTED_START,
    CONSTRUCTED_STOP,
    FIXTURES,
    GO_SET_CONTROL_MODE_14,
    GO_SET_CP_SETPOINT_2_5M,
    GO_SET_MAX_LIMITER_3_0,
    HWR_NO_ALARM_REPLY,
    HWR_STRING_REPLY,
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
    assert values.dc_power_w == pytest.approx(3.98, abs=0.01)
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
    assert result.denied == {"statistics"}
    assert result.frames["alarms"] == HWR_NO_ALARM_REPLY
    assert "ObjectError" in result.errors["statistics"]
    silent = await pump.probe_capabilities((alpha2go.ENERGY,))
    assert silent.unknown == {"energy"}
    assert "RequestTimeout" in silent.errors["energy"]


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


WRITES = [
    pytest.param(lambda p: p.set_control_mode(ControlMode.CONSTANT_FLOW), id="control-mode"),
    pytest.param(lambda p: p.set_setpoint(ControlMode.CONSTANT_FLOW, 1e-4), id="setpoint"),
    pytest.param(
        lambda p: p.set_flow_limiter(maximum=True, enabled=True, limit_m3h=1.0), id="flow-limiter"
    ),
    pytest.param(lambda p: p.set_autonight(True), id="autonight"),
    pytest.param(lambda p: p.start(), id="start"),
    pytest.param(lambda p: p.stop(), id="stop"),
    pytest.param(lambda p: p.reset_alarm(), id="reset-alarm"),
    pytest.param(lambda p: p.command(alpha2go.Command.START), id="command"),
    pytest.param(lambda p: p.set_object(alpha2go.AUTONIGHT, b"\x01"), id="set-object"),
]


@pytest.mark.parametrize("write", WRITES)
async def test_writes_are_refused_before_anything_is_sent(write) -> None:
    transport = ReplayTransport([])
    with pytest.raises(NotSupported, match="not supported in this release"):
        await write(GrundfosPump(transport, allow_writes=True))
    assert transport.sent == []


@pytest.mark.parametrize("write", WRITES)
async def test_a_release_with_writes_still_needs_allow_writes(
    write, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("grundfos_ble.pump.WRITES_ENABLED", True)
    transport = ReplayTransport([])
    pump = GrundfosPump(transport)
    assert not pump.allow_writes
    with pytest.raises(NotSupported, match="allow_writes=True"):
        await write(pump)
    assert transport.sent == []


async def test_write_path_sends_go_app_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grundfos_ble.pump.WRITES_ENABLED", True)
    transport = ReplayTransport(
        [
            _reply(GO_SET_CONTROL_MODE_14, CONSTRUCTED_SET_ACK_OK),
            _reply(
                CONSTRUCTED_START,
                Telegram.request(classic_apdu(3, 0, b""), da=0xF8, sa=0xE7, sd=0x24).encode(),
            ),
            _reply(
                CONSTRUCTED_STOP,
                Telegram.request(classic_apdu(3, 0, b""), da=0xF8, sa=0xE7, sd=0x24).encode(),
            ),
            _reply(
                Telegram.request(
                    __import__(
                        "grundfos_ble.genibus.objects", fromlist=["set_request"]
                    ).set_request(ObjectRef(90, 8), 333, 1, b"\x01")
                ).encode(),
                CONSTRUCTED_SET_ACK_FAILED,
            ),
        ]
    )
    pump = GrundfosPump(transport, allow_writes=True)
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

    async def counting(telegram: Telegram, **kwargs: Any) -> Telegram:
        calls.append(telegram.encode())
        return await original(telegram, **kwargs)

    pump = GrundfosPump(transport)
    pump._transport = MagicMock(request=counting)
    result = await pump.probe_capabilities((alpha2go.ALARMS, alpha2go.STATISTICS))
    assert result.supported == {"alarms"}
    assert result.unknown == {"statistics"}  # busy twice says nothing about support
    assert len(calls) == 3
    assert "ObjectError" in result.errors["statistics"]
    with pytest.raises(ObjectError):
        await pump.get_object(alpha2go.STATISTICS)


# --- Review fixes (2026-11): probing, shapes, versions, decoders ----------------------------------

FULL = FIXTURES / "measured-2026-11-09-full.hex"


def _full() -> ReplayTransport:
    return ReplayTransport.from_fixture(FULL)


async def test_a_silent_object_does_not_abort_the_probe() -> None:
    """One ignored object must not cost the user another press of the button (D2)."""
    transport = _full()
    transport.silent_on.add(_get_frame(alpha2go.ALARMS.ref))
    result = await GrundfosPump(transport).probe_capabilities()
    assert result.unknown == {"alarms"}
    assert result.denied == frozenset()
    assert result.supported == {s.key for s in alpha2go.OPTIONAL_OBJECTS} - {"alarms"}
    assert transport.connects == 1  # the link closed on the timeout and was brought back


async def test_a_probe_that_cannot_reconnect_marks_the_rest_unknown() -> None:
    from grundfos_ble.exceptions import PairingRequired

    class Refusing(ReplayTransport):
        async def connect(self) -> None:
            raise PairingRequired("refused", 82)

    transport = Refusing(load_fixture(FULL))
    transport.silent_on.add(_get_frame(alpha2go.OPERATION.ref))
    result = await GrundfosPump(transport).probe_capabilities()
    assert result.supported == frozenset()
    assert result.unknown == {s.key for s in alpha2go.OPTIONAL_OBJECTS}
    assert result.errors["alarms"].startswith("not probed: PairingRequired")


async def test_a_corrupt_reply_is_unknown_not_denied() -> None:
    """A CRC or layout error says nothing about support, so the caller keeps the object (D3)."""
    transport = ReplayTransport(
        [_reply(_get_frame(alpha2go.STATISTICS.ref), HWR_NO_ALARM_REPLY)]  # wrong type
    )
    result = await GrundfosPump(transport).probe_capabilities((alpha2go.STATISTICS,))
    assert result.unknown == {"statistics"}
    assert result.denied == frozenset()


async def test_a_new_object_version_is_refused_per_object() -> None:
    from grundfos_ble.exceptions import ObjectVersionError

    reply = bytearray(Telegram.decode(HWR_NO_ALARM_REPLY).apdu.data)
    reply[3] = 2  # version byte of the object header
    frame = Telegram.request(classic_apdu(10, 0, bytes(reply)), da=0xF8, sa=0xE7, sd=0x24).encode()
    transport = ReplayTransport([_reply(_get_frame(alpha2go.ALARMS.ref), frame)])
    with pytest.raises(ObjectVersionError) as caught:
        await GrundfosPump(transport).get_object(alpha2go.ALARMS)
    assert (caught.value.expected, caught.value.received) == (1, 2)
    assert caught.value.raw == frame
    assert transport.is_connected or not transport.closed  # the link is not given up for it


@pytest.mark.parametrize(
    "spec",
    [s for s in alpha2go.OPTIONAL_OBJECTS if s.version is not None]
    + [alpha2go.PROCESS_VALUES, alpha2go.MOTOR_STATE],
    ids=lambda spec: spec.key,
)
def test_registry_versions_match_the_real_pump(spec: ObjectSpec) -> None:
    exchange = next(e for e in load_fixture(FULL) if e.request == _get_frame(spec.ref))
    header = Telegram.decode(exchange.reply).apdu.data[:7]
    assert header[0] == 0
    assert int.from_bytes(header[1:3], "big") == spec.type
    assert header[3] == spec.version


def test_every_optional_object_has_a_version() -> None:
    assert all(spec.version is not None for spec in alpha2go.OPTIONAL_OBJECTS)


async def test_a_sibling_limiter_reply_is_not_taken() -> None:
    """The two limiters share a type; only the leading id byte tells them apart (A2)."""
    exchanges = load_fixture(FULL)
    minimum = next(e.reply for e in exchanges if e.request == _get_frame(ObjectRef(86, 601)))
    transport = ReplayTransport([_reply(_get_frame(alpha2go.MAX_FLOW_LIMITER.ref), minimum)])
    with pytest.raises(RequestTimeout):
        await GrundfosPump(transport).get_object(alpha2go.MAX_FLOW_LIMITER)


async def test_a_stray_frame_before_the_reply_is_skipped() -> None:
    exchanges = load_fixture(FULL)
    transport = ReplayTransport(exchanges)
    transport.stray.append(exchanges[1].reply)  # motor state, while asking for process values
    values = await GrundfosPump(transport).read_measurements()
    assert values.flow_m3h == pytest.approx(1.004, abs=0.001)


async def test_a_duplicated_reply_is_skipped() -> None:
    transport = _full()
    transport.duplicate = True
    pump = GrundfosPump(transport)
    await pump.read_statistics()
    assert (await pump.read_temperatures()).media_c == pytest.approx(16.05, abs=0.01)


async def test_alarms_and_warnings_cannot_be_told_apart_by_shape() -> None:
    """Byte-identical sibling replies: only closing the link on a lost request protects them."""
    exchanges = load_fixture(FULL)
    alarms = next(e.reply for e in exchanges if e.request == _get_frame(ObjectRef(88, 0)))
    warnings = next(e.reply for e in exchanges if e.request == _get_frame(ObjectRef(88, 11)))
    assert alarms == warnings


def test_identity_of_255_is_not_available() -> None:
    from grundfos_ble.pump import Identity

    identity = Identity(family=None, type=6, version=None)
    assert identity.profile is None


async def test_identity_read_maps_255_to_none() -> None:
    from grundfos_ble.genibus import classic
    from grundfos_ble.genibus.apdu import DataClass

    request = Telegram.request(
        classic.get_request(DataClass.MEASURED_DATA, (148, 149, 150))
    ).encode()
    reply = Telegram.request(
        classic.Apdu(DataClass.MEASURED_DATA, 0, b"\xff\x06\x00"), da=0xF8, sa=0xE7, sd=0x24
    ).encode()
    identity = await GrundfosPump(ReplayTransport([_reply(request, reply)])).read_identity(
        strings=False
    )
    assert (identity.family, identity.type, identity.version) == (None, 6, 0)
    assert identity.profile is None


@pytest.mark.parametrize(
    ("decoder", "data"),
    [
        pytest.param(decode_operation, b"\x01\x00", id="operation"),
        pytest.param(decode_user_setpoint, b"\x00", id="user-setpoint"),
        pytest.param(decode_flow_limiter, b"\x01", id="limiter"),
    ],
)
def test_missing_enum_bytes_are_an_error_not_zero(decoder: Any, data: bytes) -> None:
    with pytest.raises(ProtocolError, match="too short"):
        decoder(data)


@pytest.mark.parametrize(
    ("frame", "metres"),
    [
        ("27 1F E7 F8 0A 9B 56 00 12 01 2E 01 00 00 12 00 04 47 5A 4A 5A", 5.7),
        ("27 1F E7 F8 0A 9B 56 00 10 01 2E 01 00 00 12 00 04 47 8F 9C B5", 7.5),
        ("27 1F E7 F8 0A 9B 56 00 10 01 2E 01 00 00 12 00 04 47 19 2F B0", 4.0),
        ("27 1F E7 F8 0A 9B 56 00 12 01 2E 01 00 00 12 00 04 47 A0 D8 78", 8.4),
        ("27 1F E7 F8 0A 9B 56 00 10 01 2E 01 00 00 12 00 04 47 3F 7B 9C", 5.0),
    ],
    ids=["5.7m", "7.5m", "4.0m", "8.4m", "5.0m"],
)
def test_app_head_setpoints_read_back_to_the_centimetre(frame: str, metres: float) -> None:
    """The GO app writes heads with 1e5/10.2; read with 9804 they land on the same cm (F1).

    Frame heads from the GO-app capture (esphome-alpha2go docs/ble-traffic.md, 86/16 and 86/18).
    """
    from grundfos_ble.genibus.objects import read_f32

    pa = read_f32(bytes.fromhex(frame), 17)
    assert pa is not None
    display = alpha2go.setpoint_display(ControlMode.CONSTANT_PRESSURE, pa)
    assert display is not None
    assert round(display[0], 2) == metres


def test_head_factor_is_the_pumps_own() -> None:
    """Ben's 93/290 frame: 4589.0498 Pa and 0.46808 m; the factory presets are 9804 × n."""
    from grundfos_ble.genibus.objects import read_f32

    data = Telegram.decode(load_fixture(FULL)[0].reply).apdu.data[7:]
    pa, metres = read_f32(data, 4), read_f32(data, 28)
    assert pa is not None
    assert metres is not None
    assert pa / metres == pytest.approx(9804.0, abs=0.01)
    assert alpha2go.PA_PER_METRE == 9804.0
    # The PP maximum (47 A2 C3 00) is exactly 8.5 m, and CP III's preset exactly 7.5 m.
    assert alpha2go.setpoint_wire(ControlMode.PROPORTIONAL_PRESSURE, 8.5) == 83334.0
    assert alpha2go.setpoint_display(ControlMode.CONSTANT_PRESSURE, 73530.0) == (7.5, "m")
    assert alpha2go.setpoint_wire(ControlMode.CONSTANT_CURVE, 2000) == 2000
    assert alpha2go.setpoint_wire(ControlMode.CONSTANT_FLOW, 3.6) == pytest.approx(0.001)
    assert alpha2go.setpoint_wire(ControlMode.NONE, 1.0) is None


def test_autoadapt_has_no_user_setpoint() -> None:
    assert ControlMode.PP_AUTOADAPT not in alpha2go.USER_SETPOINTS
    assert ControlMode.CP_AUTOADAPT not in alpha2go.USER_SETPOINTS
    assert ControlMode.CP_AUTOADAPT in alpha2go.FACTORY_LIMITS
