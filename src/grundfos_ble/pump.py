"""High-level pump model: typed reads and capability probing. Writes are disabled."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final

from grundfos_ble.exceptions import (
    NackError,
    NotSupported,
    ObjectError,
    ProtocolError,
)
from grundfos_ble.genibus import classic, objects
from grundfos_ble.genibus.apdu import Ack, Apdu, DataClass
from grundfos_ble.genibus.objects import ObjectReply, ObjectStatus
from grundfos_ble.genibus.telegram import Telegram
from grundfos_ble.registry import alpha2go
from grundfos_ble.registry.alpha2go import (
    ALPHA2_GO,
    PA_PER_METRE,
    SECONDS_PER_HOUR,
    Command,
    ControlMode,
    IdentityId,
    StringId,
)
from grundfos_ble.registry.base import ObjectSpec, Profile
from grundfos_ble.transport import Transport

_LOGGER = logging.getLogger(__name__)

ABSENT_OBJECT_STATUSES: Final = frozenset({int(ObjectStatus.OPERATION_FAILED), 0x81})
"""Object statuses that mean the pump has no such object (0x81 is the ALPHA HWR's)."""

WRITES_ENABLED: Final = False
"""Whether this release lets :class:`GrundfosPump` write to a pump. It does not: writes are
implemented from captured traffic and unit-tested against it, but have never been sent to a
real pump. While this is False every write method raises :class:`NotSupported`."""


# --- Results ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Identity:
    """Who the pump says it is."""

    family: int
    type: int
    version: int
    product_name: str | None = None
    product_number: str | None = None
    serial_number: str | None = None
    production_code: str | None = None
    unit_name: str | None = None
    model: str | None = None
    software_version: str | None = None
    ble_software_version: str | None = None

    @property
    def profile(self) -> Profile | None:
        """The profile this library has for the pump, if any."""
        return alpha2go.profile_for(self.family, self.type)


@dataclass(frozen=True, slots=True)
class Measurements:
    """Process and motor values from one poll. None = not provided by the pump."""

    flow_m3h: float | None
    head_m: float | None
    differential_pressure_pa: float | None
    media_temperature_c: float | None
    differential_temperature_k: float | None
    kv: float | None
    grid_voltage_v: float | None
    dc_link_voltage_v: float | None
    current_a: float | None
    power_w: float | None
    shaft_power_w: float | None
    speed_rpm: float | None
    electronics_temperature_c: float | None
    raw_process_values: bytes = field(repr=False, default=b"")
    raw_motor_state: bytes = field(repr=False, default=b"")


@dataclass(frozen=True, slots=True)
class OperationState:
    """Realised operation (object 86/8) or a request object of the same layout."""

    control_source: int
    operation_mode: int
    control_mode: int
    setpoint: float | None
    """Wire units: Pa for pressure modes, rpm for constant curve, m³/s for constant flow."""

    @property
    def setpoint_display(self) -> tuple[float, str] | None:
        """Setpoint in display units with its unit symbol (m, rpm, m³/h)."""
        if self.setpoint is None:
            return None
        return alpha2go.setpoint_display(self.control_mode, self.setpoint)


@dataclass(frozen=True, slots=True)
class FactoryLimits:
    """Factory default and range of a mode's setpoint (object type 301), wire units."""

    default: float | None
    min: float | None
    max: float | None
    resulting_min: float | None


@dataclass(frozen=True, slots=True)
class UserSetpoint:
    """A mode's user setpoint object (type 302), wire units."""

    feedback_id: int
    setpoint: float | None
    kp: float | None
    ti: float | None
    td: float | None
    raw: bytes = field(repr=False, default=b"")


@dataclass(frozen=True, slots=True)
class FlowLimiter:
    """One flow limiter (object type 895)."""

    limiter_id: int
    enabled: bool
    limit_m3h: float | None
    raw: bytes = field(repr=False, default=b"")


@dataclass(frozen=True, slots=True)
class Faults:
    """Active alarm and warning codes."""

    alarms: tuple[int, ...]
    warnings: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Statistics:
    """Operating statistics, cumulative since commissioning (object 93/1)."""

    start_count: int | None
    starts_last_hour: int | None
    starts_last_24h: int | None
    operating_time_s: int | None
    running_time_24h_s: int | None = None
    last_cycle_s: int | None = None
    power_ups: int | None = None
    power_on_time_s: int | None = None
    raw: bytes = field(repr=False, default=b"")


@dataclass(frozen=True, slots=True)
class Temperatures:
    """Media, PCB and ambient temperatures (object 93/300)."""

    media_c: float | None
    pcb_c: float | None
    ambient_c: float | None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Which optional objects the pump answered, with the raw frames for the record."""

    supported: frozenset[str]
    frames: dict[str, bytes]
    """Raw reply frame per object key, including the failures' frames when a reply came."""
    errors: dict[str, str]
    """Why each unsupported object was judged so."""


# --- Pump ---------------------------------------------------------------------------------------


class GrundfosPump:
    """Typed access to one pump over a :class:`~grundfos_ble.transport.Transport`."""

    def __init__(self, transport: Transport, profile: Profile = ALPHA2_GO) -> None:
        self._transport = transport
        self.profile = profile

    @property
    def transport(self) -> Transport:
        """The transport in use."""
        return self._transport

    # -- low level ---------------------------------------------------------------------------

    async def _request(self, *apdus: Apdu) -> Telegram:
        return await self._transport.request(Telegram.request(*apdus))

    async def get_object(self, spec: ObjectSpec) -> ObjectReply:
        """GET one object and validate it against ``spec``.

        Raises :class:`NotSupported` when the pump says it has no such object.
        """
        reply = await self._request(objects.get_request(spec.ref))
        raw = reply.raw
        try:
            replies = objects.parse_get_reply(reply.apdu, raw)
        except NackError as err:
            if err.ack in (Ack.ID_UNKNOWN, Ack.CLASS_UNKNOWN):
                raise NotSupported(f"{spec.key} ({spec.ref}): {err}") from err
            raise
        try:
            return (
                objects.expect_object(replies, spec.type, min_size=spec.min_size, raw=raw)
                if spec.type is not None
                else _first_ok(replies, spec, raw)
            )
        except ObjectError as err:
            if err.status in ABSENT_OBJECT_STATUSES:
                raise NotSupported(f"{spec.key} ({spec.ref}): status {err.status}") from err
            raise

    async def set_object(self, spec: ObjectSpec, data: bytes) -> None:
        """SET one object and check the acknowledgement (disabled in this release)."""
        _refuse_writes()
        if spec.type is None:
            raise ValueError(f"{spec.key} has no known object type; cannot write it")
        apdu = objects.set_request(spec.ref, spec.type, spec.version or 1, data)
        reply = await self._request(apdu)
        status = objects.parse_set_reply(reply.apdu, reply.raw)
        if status != ObjectStatus.OK:
            raise ObjectError(status, reply.raw)

    async def command(self, command_id: int) -> None:
        """Send a class-3 command and check it was accepted (disabled in this release)."""
        _refuse_writes()
        reply = await self._request(classic.command(command_id))
        classic.parse_command_reply(reply.apdu, reply.raw)

    async def read_string(self, string_id: int) -> str | None:
        """Read a class-7 string; None if the pump rejects the ID."""
        reply = await self._request(classic.get_request(DataClass.ASCII_STRINGS, (string_id,)))
        try:
            return classic.parse_string_reply(reply.apdu, reply.raw)
        except NackError:
            return None

    # -- reads -------------------------------------------------------------------------------

    async def read_identity(self, *, strings: bool = True) -> Identity:
        """Read family/type/version and, optionally, the identifying strings."""
        ids: Sequence[int] = (IdentityId.UNIT_FAMILY, IdentityId.UNIT_TYPE, IdentityId.UNIT_VERSION)
        reply = await self._request(classic.get_request(DataClass.MEASURED_DATA, ids))
        values = classic.parse_get_reply(reply.apdu, DataClass.MEASURED_DATA, ids, reply.raw)
        identity = Identity(
            family=values[IdentityId.UNIT_FAMILY],
            type=values[IdentityId.UNIT_TYPE],
            version=values[IdentityId.UNIT_VERSION],
        )
        if not strings:
            return identity
        return Identity(
            family=identity.family,
            type=identity.type,
            version=identity.version,
            product_name=await self.read_string(StringId.PRODUCT_NAME),
            product_number=await self.read_string(StringId.PRODUCT_NUMBER),
            serial_number=await self.read_string(StringId.SERIAL_NUMBER),
            production_code=await self.read_string(StringId.PRODUCTION_CODE),
            unit_name=await self.read_string(StringId.UNIT_NAME),
            model=await self.read_string(StringId.MODEL),
            software_version=await self.read_string(StringId.SOFTWARE_VERSION),
            ble_software_version=await self.read_string(StringId.BLE_SOFTWARE_VERSION),
        )

    async def read_measurements(self) -> Measurements:
        """Read the process-values and motor-state objects."""
        process = await self.get_object(alpha2go.PROCESS_VALUES)
        motor = await self.get_object(alpha2go.MOTOR_STATE)
        return decode_measurements(process.data, motor.data)

    async def read_operation(self) -> OperationState:
        """Read the realised operation (control source, modes, setpoint)."""
        reply = await self.get_object(alpha2go.OPERATION)
        return decode_operation(reply.data)

    async def read_factory_limits(self, mode: ControlMode) -> FactoryLimits:
        """Read the factory limits of ``mode``'s setpoint."""
        spec = _spec_for_mode(alpha2go.FACTORY_LIMITS, mode)
        values = spec.decode((await self.get_object(spec)).data)
        return FactoryLimits(
            default=_f(values["default"]),
            min=_f(values["min"]),
            max=_f(values["max"]),
            resulting_min=_f(values["resulting_min"]),
        )

    async def read_user_setpoint(self, mode: ControlMode) -> UserSetpoint:
        """Read ``mode``'s user setpoint object."""
        spec = _spec_for_mode(alpha2go.USER_SETPOINTS, mode)
        reply = await self.get_object(spec)
        return decode_user_setpoint(reply.data)

    async def read_panel_setting(self) -> int:
        """Read the panel's selected setting (``PanelSetting`` value)."""
        reply = await self.get_object(alpha2go.PANEL_SETTING)
        return reply.data[0]

    async def read_flow_limiters(self) -> tuple[FlowLimiter, FlowLimiter]:
        """Read the maximum and minimum flow limiters."""
        maximum = await self.get_object(alpha2go.MAX_FLOW_LIMITER)
        minimum = await self.get_object(alpha2go.MIN_FLOW_LIMITER)
        return decode_flow_limiter(maximum.data), decode_flow_limiter(minimum.data)

    async def read_autonight(self) -> bool:
        """Read whether AutoNight is enabled."""
        reply = await self.get_object(alpha2go.AUTONIGHT)
        return bool(reply.data[0])

    async def read_faults(self) -> Faults:
        """Read active alarms and warnings (answered on an ALPHA2 GO, never with a fault active)."""
        alarms = await self.get_object(alpha2go.ALARMS)
        warnings = await self.get_object(alpha2go.WARNINGS)
        return Faults(decode_fault_array(alarms.data), decode_fault_array(warnings.data))

    async def read_statistics(self) -> Statistics:
        """Read operating statistics, cumulative since commissioning."""
        reply = await self.get_object(alpha2go.STATISTICS)
        return decode_statistics(reply.data)

    async def read_temperatures(self) -> Temperatures:
        """Read media/PCB/ambient temperatures (answered on an ALPHA2 GO)."""
        values = alpha2go.TEMPERATURES.decode((await self.get_object(alpha2go.TEMPERATURES)).data)
        return Temperatures(_f(values["media_c"]), _f(values["pcb_c"]), _f(values["ambient_c"]))

    async def read_energy_kwh(self) -> float | None:
        """Read lifetime energy in kWh (answered on an ALPHA2 GO)."""
        values = alpha2go.ENERGY.decode((await self.get_object(alpha2go.ENERGY)).data)
        joules = _f(values["energy_j"])
        return None if joules is None else joules / 3_600_000.0

    async def probe_capabilities(
        self, specs: Iterable[ObjectSpec] = alpha2go.OPTIONAL_OBJECTS
    ) -> ProbeResult:
        """Try each optional object once and record which ones this pump answers.

        An object that reports busy is tried once more before it is judged absent.
        """
        supported: set[str] = set()
        frames: dict[str, bytes] = {}
        errors: dict[str, str] = {}
        for spec in specs:
            for final_try in (False, True):
                outcome = await self._probe_one(spec, frames)
                busy = isinstance(outcome, ObjectError) and outcome.status == ObjectStatus.BUSY
                if not busy or final_try:
                    break
            if outcome is None:
                supported.add(spec.key)
            else:
                errors[spec.key] = f"{type(outcome).__name__}: {outcome}"
        return ProbeResult(frozenset(supported), frames, errors)

    async def _probe_one(
        self, spec: ObjectSpec, frames: dict[str, bytes]
    ) -> NackError | ObjectError | ProtocolError | None:
        try:
            reply = await self._request(objects.get_request(spec.ref))
            if reply.raw is not None:
                frames[spec.key] = reply.raw
            replies = objects.parse_get_reply(reply.apdu, reply.raw)
            if spec.type is not None:
                objects.expect_object(replies, spec.type, min_size=spec.min_size, raw=reply.raw)
            else:
                _first_ok(replies, spec, reply.raw)
        except (NackError, ObjectError, ProtocolError) as err:
            return err
        return None

    # -- writes (disabled in this release) ---------------------------------------------------

    async def set_control_mode(self, mode: ControlMode) -> None:
        """Change the control mode (disabled in this release).

        Mirrors the Grundfos GO app: write 86/10 with operation mode "no command" and a
        "keep stored setpoint" sentinel. Read :meth:`read_operation` back to confirm.
        """
        await self.set_object(alpha2go.CONTROL_MODE_REQUEST, encode_control_mode_request(mode))

    async def set_setpoint(self, mode: ControlMode, value: float) -> UserSetpoint:
        """Write ``mode``'s setpoint in wire units and read it back (disabled in this release).

        Follows the app: checks the factory limits, writes 86/10 for the mode, then the mode's
        user-setpoint object with only the setpoint replaced. The pump clamps silently, so the
        returned read-back is the truth.
        """
        _refuse_writes()
        limits = await self.read_factory_limits(mode)
        if (
            limits.min is not None
            and limits.max is not None
            and not limits.min <= value <= limits.max
        ):
            raise ValueError(f"setpoint {value} outside factory range {limits.min}..{limits.max}")
        current = await self.read_user_setpoint(mode)
        await self.set_control_mode(mode)
        spec = _spec_for_mode(alpha2go.USER_SETPOINTS, mode)
        await self.set_object(spec, encode_user_setpoint(current, value))
        return await self.read_user_setpoint(mode)

    async def set_flow_limiter(
        self, *, maximum: bool, enabled: bool, limit_m3h: float | None
    ) -> None:
        """Switch a flow limiter and optionally set its limit (disabled in this release)."""
        _refuse_writes()
        spec = alpha2go.MAX_FLOW_LIMITER if maximum else alpha2go.MIN_FLOW_LIMITER
        current = await self.get_object(spec)
        await self.set_object(spec, encode_flow_limiter(current.data, enabled, limit_m3h))

    async def set_autonight(self, enabled: bool) -> None:
        """Switch AutoNight (disabled in this release)."""
        await self.set_object(alpha2go.AUTONIGHT, bytes((int(enabled),)))

    async def start(self) -> None:
        """Class-3 START (disabled in this release)."""
        await self.command(Command.START)

    async def stop(self) -> None:
        """Class-3 STOP (disabled in this release)."""
        await self.command(Command.STOP)

    async def reset_alarm(self) -> None:
        """Class-3 RESET ALARM (disabled in this release; documented for SCALA1 only)."""
        await self.command(Command.RESET_ALARM)


# --- Decoders and encoders (pure functions, unit-tested against recorded frames) ----------------


def _refuse_writes() -> None:
    if not WRITES_ENABLED:
        raise NotSupported("writing to a pump is not supported in this release")


def _f(value: float | int | bool | None) -> float | None:
    return None if value is None else float(value)


def _first_ok(replies: Sequence[ObjectReply], spec: ObjectSpec, raw: bytes | None) -> ObjectReply:
    for reply in replies:
        if not reply.ok:
            raise ObjectError(reply.header.status, raw)
        if len(reply.data) < spec.min_size:
            raise ProtocolError(f"{spec.key}: {len(reply.data)} bytes, need {spec.min_size}", raw)
        return reply
    raise ProtocolError(f"{spec.key}: reply carried no object", raw)


def _spec_for_mode(table: dict[ControlMode, ObjectSpec], mode: ControlMode) -> ObjectSpec:
    try:
        return table[ControlMode(mode)]
    except (KeyError, ValueError) as err:
        raise NotSupported(f"no setpoint object known for control mode {mode}") from err


def decode_measurements(process: bytes, motor: bytes) -> Measurements:
    """Build :class:`Measurements` from the two objects' data."""
    p = alpha2go.PROCESS_VALUES.decode(process)
    m = alpha2go.MOTOR_STATE.decode(motor)
    flow_m3h = _f(p["flow_m3h"])
    if flow_m3h is None and (flow_m3s := _f(p["flow_m3s"])) is not None:
        flow_m3h = flow_m3s * SECONDS_PER_HOUR
    head_m = _f(p["head_m"])
    if head_m is None and (dp := _f(p["differential_pressure_pa"])) is not None:
        head_m = dp / PA_PER_METRE
    return Measurements(
        flow_m3h=flow_m3h,
        head_m=head_m,
        differential_pressure_pa=_f(p["differential_pressure_pa"]),
        media_temperature_c=_f(p["media_temperature_c"]),
        differential_temperature_k=_f(p["differential_temperature_k"]),
        kv=_f(p["kv"]),
        grid_voltage_v=_f(m["grid_voltage_v"]),
        dc_link_voltage_v=_f(m["dc_link_voltage_v"]),
        current_a=_f(m["current_a"]),
        power_w=_f(m["power_w"]),
        shaft_power_w=_f(m["shaft_power_w"]),
        speed_rpm=_f(m["speed_rpm"]),
        electronics_temperature_c=_f(m["electronics_temperature_c"]),
        raw_process_values=process,
        raw_motor_state=motor,
    )


def decode_operation(data: bytes) -> OperationState:
    """Decode an object of type 303."""
    v = alpha2go.OPERATION.decode(data)
    return OperationState(
        control_source=int(v["control_source"] or 0),
        operation_mode=int(v["operation_mode"] or 0),
        control_mode=int(v["control_mode"] or 0),
        setpoint=_f(v["setpoint"]),
    )


def decode_user_setpoint(data: bytes) -> UserSetpoint:
    """Decode an object of type 302."""
    v = alpha2go.USER_SETPOINTS[ControlMode.CONSTANT_PRESSURE].decode(data)
    return UserSetpoint(
        feedback_id=int(v["feedback_id"] or 0),
        setpoint=_f(v["setpoint"]),
        kp=_f(v["kp"]),
        ti=_f(v["ti"]),
        td=_f(v["td"]),
        raw=data,
    )


def decode_flow_limiter(data: bytes) -> FlowLimiter:
    """Decode an object of type 895."""
    v = alpha2go.MAX_FLOW_LIMITER.decode(data)
    limit = _f(v["limit_m3s"])
    return FlowLimiter(
        limiter_id=int(v["limiter_id"] or 0),
        enabled=bool(v["enabled"]),
        limit_m3h=None if limit is None else limit * SECONDS_PER_HOUR,
        raw=data,
    )


def decode_fault_array(data: bytes) -> tuple[int, ...]:
    """Decode a type-570 array of uint16 codes, stopping at the first zero."""
    codes: list[int] = []
    for offset in range(0, len(data) - 1, 2):
        code = int.from_bytes(data[offset : offset + 2], "big")
        if code == 0:
            break
        codes.append(code)
    return tuple(codes)


def decode_statistics(data: bytes) -> Statistics:
    """Decode the operating-statistics object (type 248)."""
    v = alpha2go.STATISTICS.decode(data)
    return Statistics(
        start_count=_i(v["start_count"]),
        starts_last_hour=_i(v["starts_last_hour"]),
        starts_last_24h=_i(v["starts_last_24h"]),
        operating_time_s=_i(v["operating_time_s"]),
        running_time_24h_s=_i(v["running_time_24h_s"]),
        last_cycle_s=_i(v["last_cycle_s"]),
        power_ups=_i(v["power_ups"]),
        power_on_time_s=_i(v["power_on_time_s"]),
        raw=data,
    )


def _i(value: float | int | bool | None) -> int | None:
    return None if value is None else int(value)


def encode_control_mode_request(mode: ControlMode) -> bytes:
    """Build the data of a type-303 SET that changes only the control mode (GO-app style)."""
    return bytes((0, alpha2go.OperationMode.NO_COMMAND, int(mode))) + objects.NOT_AVAILABLE


def encode_user_setpoint(current: UserSetpoint, setpoint: float) -> bytes:
    """Build the data of a type-302 SET: ``current`` with only the setpoint replaced."""
    return (
        current.feedback_id.to_bytes(2, "big")
        + objects.encode_f32(setpoint)
        + objects.encode_f32(current.kp)
        + objects.encode_f32(current.ti)
        + objects.encode_f32(current.td)
    )


def encode_flow_limiter(current: bytes, enabled: bool, limit_m3h: float | None) -> bytes:
    """Build the data of a type-895 SET: ``current`` with the flag and, optionally, the limit."""
    data = bytearray(current[:18].ljust(18, b"\x00"))
    data[1] = int(enabled)
    if limit_m3h is not None:
        data[2:6] = objects.encode_f32(limit_m3h / SECONDS_PER_HOUR)
    return bytes(data)
