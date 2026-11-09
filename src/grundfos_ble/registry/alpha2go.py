"""The Grundfos ALPHA2 GO profile (GENIbus unit family 52, type 6).

Every entry names where it comes from. "Measured" means read from a real ALPHA2 GO by this
project and checked against the pump's display; "captured" means seen in Grundfos GO
traffic captured by the esphome-alpha2go author on an ALPHA2 GO 25-90 (facts only, no code
reused); "documented" means described for the ALPHA HWR (same family, type 7), the ALPHA3
or the SCALA1 and not yet confirmed on an ALPHA2 GO.
"""

from __future__ import annotations

from enum import IntEnum

from grundfos_ble.genibus.objects import ObjectRef
from grundfos_ble.registry.base import Access, Field, Kind, ObjectSpec, Profile, Verified

FAMILY = 52
TYPE = 6

PA_PER_METRE = 9806.65
"""Head conversion. The GO app uses 9804; the 0.03 % difference is below display resolution."""
SECONDS_PER_HOUR = 3600.0

# --- Enumerations ------------------------------------------------------------------------------


class ControlMode(IntEnum):
    """Control mode byte (object type 303 byte 2, and panel preset byte 0)."""

    CONSTANT_PRESSURE = 0
    PROPORTIONAL_PRESSURE = 1
    CONSTANT_CURVE = 2  # Grundfos: "constant speed"
    AUTOADAPT = 5  # generic; the pump refuses it as a wire value
    CONSTANT_TEMPERATURE = 6
    CLOSED_LOOP_SENSOR = 7
    CONSTANT_FLOW = 8
    CONSTANT_LEVEL = 9
    FLOWADAPT = 10
    CONSTANT_DIFFERENTIAL_PRESSURE = 11
    CONSTANT_DIFFERENTIAL_TEMPERATURE = 12
    PP_AUTOADAPT = 13  # AUTOADAPT radiator
    CP_AUTOADAPT = 14  # AUTOADAPT underfloor
    AUTOADAPT_COMBINED = 15
    CONSTANT_DOSING = 16
    PID = 20
    DHW_ON_OFF = 25
    PROPORTIONAL_DIFFERENTIAL_PRESSURE = 26
    ON_OFF = 29
    CONSTANT_VOLTAGE = 30
    AIR_VENTING = 128
    NONE = 254


PRESSURE_MODES = frozenset(
    {
        ControlMode.CONSTANT_PRESSURE,
        ControlMode.PROPORTIONAL_PRESSURE,
        ControlMode.PP_AUTOADAPT,
        ControlMode.CP_AUTOADAPT,
        ControlMode.AUTOADAPT_COMBINED,
    }
)
SPEED_MODES = frozenset({ControlMode.CONSTANT_CURVE})
FLOW_MODES = frozenset({ControlMode.CONSTANT_FLOW, ControlMode.FLOWADAPT})

MODES_SEEN_ON_ALPHA2_GO = frozenset(
    {
        ControlMode.CONSTANT_PRESSURE,
        ControlMode.PROPORTIONAL_PRESSURE,
        ControlMode.CONSTANT_CURVE,
        ControlMode.CONSTANT_FLOW,
        ControlMode.PP_AUTOADAPT,
        ControlMode.CP_AUTOADAPT,
    }
)


class OperationMode(IntEnum):
    """Operation mode byte (object type 303 byte 1)."""

    AUTO = 0
    STOP = 1
    MIN = 2
    MAX = 3
    USER_DEFINED = 4
    FORCED_START = 5
    NO_COMMAND = 6
    HAND = 7
    STANDBY = 8
    STOP_WITHOUT_DELAY = 9
    EMERGENCY_RUN = 10


class ControlSource(IntEnum):
    """Control source byte (object type 303 byte 0)."""

    NONE = 0
    LOCAL = 1
    BUS = 2
    LINK = 3
    EXTERNAL_IO = 4
    OFF_BUTTON = 5
    EVENT_ACTION = 8
    AUTONIGHT = 104
    AIR_VENTING = 111
    PWM = 123
    TEMPORARY = 127


class PanelSetting(IntEnum):
    """Selected panel setting (object type 868)."""

    CONSTANT_CURVE_1 = 0
    CONSTANT_CURVE_2 = 1
    CONSTANT_CURVE_3 = 2
    CONSTANT_PRESSURE_1 = 3
    CONSTANT_PRESSURE_2 = 4
    CONSTANT_PRESSURE_3 = 5
    PROPORTIONAL_PRESSURE_1 = 6
    PROPORTIONAL_PRESSURE_2 = 7
    PROPORTIONAL_PRESSURE_3 = 8
    CONSTANT_CURVE_AUTOADAPT = 9
    CP_AUTOADAPT = 10
    PP_AUTOADAPT = 11
    PWM_A_PROFILE_1 = 12
    PWM_A_PROFILE_2 = 13
    PWM_A_PROFILE_3 = 14
    PWM_C = 15
    ANALOGUE_0_10V = 16
    TRC1 = 17
    CONSTANT_SPEED_AUTOADAPT = 18
    TRC2 = 19
    TRC3 = 20


class Command(IntEnum):
    """Class-3 command IDs."""

    RESET_ALARM = 2  # documented for SCALA1, untested on the ALPHA2 GO
    STOP = 5
    START = 6
    REMOTE = 7
    LOCAL = 8
    CONSTANT_FLOW = 21  # ALPHA2 GO only
    CONSTANT_SPEED = 22
    PROPORTIONAL_PRESSURE = 23
    CONSTANT_PRESSURE = 24
    MIN = 25
    MAX = 26


class StringId(IntEnum):
    """Class-7 ASCII string IDs."""

    PRODUCT_NAME = 1
    PRODUCT_NUMBER = 8
    SERIAL_NUMBER = 9
    PRODUCTION_CODE = 10
    UNIT_NAME = 17  # the user-settable name, also the BLE local name
    MODEL = 19  # e.g. "ALPHA2 GO XX-90"
    GSC_FILE = 21
    SOFTWARE_VERSION = 50
    BLE_SOFTWARE_VERSION = 58
    ECU_ID = 100


class IdentityId(IntEnum):
    """Class-2 measured-data IDs holding the unit identity."""

    UNIT_FAMILY = 148
    UNIT_TYPE = 149
    UNIT_VERSION = 150


class ErrorCode(IntEnum):
    """GENIbus error codes (alarms and warnings). Names from the GENI ErrorCode enum.

    The ALPHA2 GO manual lists 51, 40, 4, 72, 76, 85, 132, 57, 25, 43 and 35.
    """

    OK = 0
    LEAKAGE_CURRENT = 1
    MOTOR_PHASE_MISSING = 2
    EXTERNAL_ALARM = 3
    OVERVOLTAGE = 4
    PUMP_COMMUNICATION = 10
    SERVICE_INTERVAL = 12
    DC_LINK_PROTECTION = 14
    OTHER = 16
    WRONG_CONFIGURATION = 25
    FORCED_PUMPING = 29
    OVERVOLTAGE_ALT = 32
    AIR_IN_MEDIA = 35
    UNDERVOLTAGE = 40
    UNDERVOLTAGE_TRANSIENT = 41
    FORCED_PUMPING_WARNING = 43
    UNDER_TEMPERATURE = 44
    MOTOR_OVERLOAD = 48
    MOTOR_OVERCURRENT = 49
    MOTOR_BLOCKED = 51
    MOTOR_UNDERLOAD = 56
    DRY_RUNNING = 57
    LOW_FLOW = 58
    NO_FLOW = 59
    OVER_TEMPERATURE = 64
    MOTOR_OVER_TEMPERATURE = 65
    ELECTRONICS_OVER_TEMPERATURE = 66
    CONVERTER_OVER_TEMPERATURE = 67
    HIGH_MEDIA_TEMPERATURE = 68
    INTERNAL_FAULT = 72
    HARDWARE_SHUTDOWN = 73
    HIGH_DC_LINK_VOLTAGE = 74
    LOW_DC_LINK_VOLTAGE = 75
    INTERNAL_FAULT_76 = 76
    PERSISTENCE_BREAKDOWN = 84
    MEMORY_FAULT = 85
    SENSOR_FAULT = 88
    FEEDBACK_SENSOR_FAULT = 89
    SPEED_SENSOR_FAULT = 90
    FLOW_TEMPERATURE_SENSOR_FAULT = 91
    FALLBACK_SENSOR_FAULT = 93
    REFERENCE_INPUT_FAULT = 96
    INVALID_SETPOINT = 97
    RECTIFIER_PROTECTION = 105
    INVERTER_PROTECTION = 106
    FROST_PROTECTION = 114
    INVALID_GSC_FILE = 132
    INRUSH_FAULT = 155
    CLOCK_FAULT = 157
    PRESSURE_SENSOR_FAULT = 168
    FLOW_SENSOR_FAULT = 169
    CAVITATION = 208
    OVERPRESSURE = 210
    MAX_RUNNING_TIME = 245
    ELECTRONICS_SHORT_CIRCUIT = 255


def error_code_name(code: int) -> str:
    """Lower-case name for an error code, or ``unknown_<code>``."""
    try:
        return ErrorCode(code).name.lower()
    except ValueError:
        return f"unknown_{code}"


# --- Object types -------------------------------------------------------------------------------

TYPE_PROCESS_VALUES = 565
TYPE_MOTOR_STATE = 256
TYPE_OPERATION_REQUEST = 303
TYPE_FACTORY_LIMITS = 301
TYPE_USER_SETPOINT = 302
TYPE_PANEL_SETTING = 868
TYPE_PANEL_PRESET = 869
TYPE_FLOW_LIMITER = 895
TYPE_FLOW_LIMITER_RANGE = 897
TYPE_AUTONIGHT = 333
TYPE_PP_CURVE = 286
TYPE_FAULT_ARRAY = 570
TYPE_TEMPERATURES = 534
TYPE_ENERGY = 232
TYPE_STATISTICS = 248

_B = "esphome-alpha2go ble-traffic.md + alpha2.h (Birkemosen, 2026-10-28)"
_M = "measured on an ALPHA2 GO, 2026-11-07/09"
_H = "alpha-hwr docs (ALPHA HWR, family 52 type 7)"
_G = "gofree docs (SCALA1)"
_A3 = "ESPHome alpha3 / Seba65439 decoders (ALPHA3)"

PROCESS_VALUES = ObjectSpec(
    key="process_values",
    ref=ObjectRef(93, 290),
    type=TYPE_PROCESS_VALUES,
    version=2,
    min_size=32,
    description="Flow, head and the water-side values",
    verified=Verified.MEASURED,
    source=f"{_M}; {_B}",
    fields=(
        Field("flow_m3s", 0, Kind.F32, "m³/s", Verified.MEASURED),
        Field("differential_pressure_pa", 4, Kind.F32, "Pa", Verified.MEASURED),
        Field("unknown_8", 8, Kind.F32, None, Verified.UNKNOWN, "not available when measured"),
        Field("unknown_12", 12, Kind.F32, None, Verified.UNKNOWN, "not available when measured"),
        Field("media_temperature_c", 16, Kind.F32, "°C", Verified.CAPTURED, "N/A without sensor"),
        Field("differential_temperature_k", 20, Kind.F32, "K", Verified.CAPTURED, "N/A w/o sensor"),
        Field("flow_m3h", 24, Kind.F32, "m³/h", Verified.MEASURED),
        Field("head_m", 28, Kind.F32, "m", Verified.MEASURED),
        Field(
            "kv", 32, Kind.F32, None, Verified.UNKNOWN, "4.78 when measured; 'kv' per Birkemosen"
        ),
    ),
)

MOTOR_STATE = ObjectSpec(
    key="motor_state",
    ref=ObjectRef(87, 69),
    type=TYPE_MOTOR_STATE,
    version=3,
    min_size=24,
    description="Electrical values, speed and electronics temperature",
    verified=Verified.MEASURED,
    source=f"{_M}; {_B}; {_A3}",
    fields=(
        Field("grid_voltage_v", 0, Kind.F32, "V", Verified.MEASURED),
        Field("dc_link_voltage_v", 4, Kind.F32, "V", Verified.MEASURED, "≈ grid × √2"),
        Field(
            "current_a",
            8,
            Kind.F32,
            "A",
            Verified.MEASURED,
            "0.038 A at 4 W; plausibility unchecked",
        ),
        Field("power_w", 12, Kind.F32, "W", Verified.MEASURED),
        Field(
            "shaft_power_w", 16, Kind.F32, "W", Verified.UNKNOWN, "'motor power, not sure' (ALPHA3)"
        ),
        Field("speed_rpm", 20, Kind.F32, "rpm", Verified.MEASURED),
        Field("unknown_24", 24, Kind.F32, None, Verified.UNKNOWN, "NaN when measured"),
        Field("unknown_28", 28, Kind.F32, None, Verified.UNKNOWN, "NaN when measured"),
        Field(
            "electronics_temperature_c",
            32,
            Kind.F32,
            "°C",
            Verified.MEASURED,
            "39–44 °C with ~20 °C water; which sensor is unconfirmed",
        ),
    ),
)

_OPERATION_FIELDS = (
    Field("control_source", 0, Kind.U8),
    Field("operation_mode", 1, Kind.U8),
    Field("control_mode", 2, Kind.U8),
    Field("setpoint", 3, Kind.F32, None, Verified.CAPTURED, "Pa / rpm / m³/s by control mode"),
)

OPERATION = ObjectSpec(
    key="operation",
    ref=ObjectRef(86, 8),
    type=TYPE_OPERATION_REQUEST,
    version=1,
    min_size=7,
    description="Realised operation: what the pump is actually running",
    verified=Verified.ANSWERED,
    source=f"{_B}; {_H}",
    fields=_OPERATION_FIELDS,
)
LOCAL_REQUEST = ObjectSpec(
    key="local_request",
    ref=ObjectRef(86, 6),
    type=TYPE_OPERATION_REQUEST,
    version=1,
    min_size=7,
    description="Local (panel/app) operation request",
    source=_B,
    fields=_OPERATION_FIELDS,
)
CONTROL_MODE_REQUEST = ObjectSpec(
    key="control_mode_request",
    ref=ObjectRef(86, 10),
    type=TYPE_OPERATION_REQUEST,
    version=1,
    min_size=7,
    description="Local control-mode request; written to change the control mode",
    access=Access.READ_WRITE,
    source=_B,
    fields=_OPERATION_FIELDS,
)

_FACTORY_FIELDS = (
    Field("default", 0, Kind.F32),
    Field("min", 4, Kind.F32),
    Field("max", 8, Kind.F32),
    Field("resulting_min", 12, Kind.F32),
    Field("kp", 16, Kind.F32),
    Field("ti", 20, Kind.F32),
    Field("td", 24, Kind.F32),
)
_USER_FIELDS = (
    Field("feedback_id", 0, Kind.U16),
    Field("setpoint", 2, Kind.F32),
    Field("kp", 6, Kind.F32),
    Field("ti", 10, Kind.F32),
    Field("td", 14, Kind.F32),
)


def _factory(key: str, sub: int, mode: str, verified: Verified = Verified.CAPTURED) -> ObjectSpec:
    return ObjectSpec(
        key=key,
        ref=ObjectRef(86, sub),
        type=TYPE_FACTORY_LIMITS,
        version=1,
        min_size=16,
        description=f"Factory limits for {mode}",
        verified=verified,
        source=_B,
        fields=_FACTORY_FIELDS,
    )


def _user(key: str, sub: int, mode: str, verified: Verified = Verified.CAPTURED) -> ObjectSpec:
    return ObjectSpec(
        key=key,
        ref=ObjectRef(86, sub),
        type=TYPE_USER_SETPOINT,
        version=1,
        min_size=18,
        description=f"User setpoint and controller gains for {mode}",
        access=Access.READ_WRITE,
        verified=verified,
        source=_B,
        fields=_USER_FIELDS,
    )


FACTORY_LIMITS: dict[ControlMode, ObjectSpec] = {
    ControlMode.CONSTANT_CURVE: _factory("factory_constant_curve", 13, "constant curve (rpm)"),
    ControlMode.CONSTANT_PRESSURE: _factory(
        "factory_constant_pressure", 15, "constant pressure (Pa)"
    ),
    ControlMode.PROPORTIONAL_PRESSURE: _factory(
        "factory_proportional_pressure", 17, "proportional pressure (Pa)"
    ),
    ControlMode.PP_AUTOADAPT: _factory("factory_pp_autoadapt", 19, "PP AUTOADAPT (Pa)"),
    ControlMode.CP_AUTOADAPT: _factory("factory_cp_autoadapt", 21, "CP AUTOADAPT (Pa)"),
    ControlMode.CONSTANT_FLOW: _factory("factory_constant_flow", 39, "constant flow (m³/s)"),
}
USER_SETPOINTS: dict[ControlMode, ObjectSpec] = {
    ControlMode.CONSTANT_CURVE: _user("setpoint_constant_curve", 14, "constant curve (rpm)"),
    ControlMode.CONSTANT_PRESSURE: _user(
        "setpoint_constant_pressure", 16, "constant pressure (Pa)"
    ),
    ControlMode.PROPORTIONAL_PRESSURE: _user(
        "setpoint_proportional_pressure", 18, "proportional pressure (Pa)"
    ),
    ControlMode.PP_AUTOADAPT: _user(
        "setpoint_pp_autoadapt", 20, "PP AUTOADAPT (Pa)", Verified.UNKNOWN
    ),
    ControlMode.CP_AUTOADAPT: _user(
        "setpoint_cp_autoadapt", 22, "CP AUTOADAPT (Pa)", Verified.UNKNOWN
    ),
    ControlMode.CONSTANT_FLOW: _user("setpoint_constant_flow", 40, "constant flow (m³/s)"),
}

PANEL_SETTING = ObjectSpec(
    key="panel_setting",
    ref=ObjectRef(86, 350),
    type=TYPE_PANEL_SETTING,
    min_size=1,
    description="Setting selected on the panel (not updated by app or bus changes)",
    verified=Verified.ANSWERED,
    source=_B,
    fields=(Field("setting", 0, Kind.U8),),
)


def panel_preset(index: int) -> ObjectSpec:
    """Return the stored setpoint object of panel setting ``index`` (0 = CC I … 8 = PP III)."""
    return ObjectSpec(
        key=f"panel_preset_{index}",
        ref=ObjectRef(86, 351 + index),
        type=TYPE_PANEL_PRESET,
        min_size=5,
        description=f"Panel preset {index}",
        source=_B,
        fields=(Field("control_mode", 0, Kind.U8), Field("setpoint", 1, Kind.F32)),
    )


_LIMITER_FIELDS = (
    Field("limiter_id", 0, Kind.U8),
    Field("enabled", 1, Kind.BOOL),
    Field("limit_m3s", 2, Kind.F32, "m³/s"),
    Field("kp", 6, Kind.F32),
    Field("ti", 10, Kind.F32),
    Field("td", 14, Kind.F32),
)
MAX_FLOW_LIMITER = ObjectSpec(
    key="max_flow_limiter",
    ref=ObjectRef(86, 600),
    type=TYPE_FLOW_LIMITER,
    version=1,
    min_size=18,
    description="Maximum flow limiter (FLOWLIMIT)",
    access=Access.READ_WRITE,
    verified=Verified.ANSWERED,
    source=_B,
    fields=_LIMITER_FIELDS,
)
MIN_FLOW_LIMITER = ObjectSpec(
    key="min_flow_limiter",
    ref=ObjectRef(86, 601),
    type=TYPE_FLOW_LIMITER,
    version=1,
    min_size=18,
    description="Minimum flow limiter",
    access=Access.READ_WRITE,
    verified=Verified.ANSWERED,
    source=_B,
    fields=_LIMITER_FIELDS,
)
_LIMITER_RANGE_FIELDS = (
    Field("limiter_id", 0, Kind.U8),
    Field("lower_m3s", 1, Kind.F32, "m³/s"),
    Field("upper_m3s", 5, Kind.F32, "m³/s"),
)
MAX_FLOW_LIMITER_RANGE = ObjectSpec(
    key="max_flow_limiter_range",
    ref=ObjectRef(86, 620),
    type=TYPE_FLOW_LIMITER_RANGE,
    min_size=9,
    description="Allowed range of the maximum flow limiter",
    source=_B,
    fields=_LIMITER_RANGE_FIELDS,
)
MIN_FLOW_LIMITER_RANGE = ObjectSpec(
    key="min_flow_limiter_range",
    ref=ObjectRef(86, 621),
    type=TYPE_FLOW_LIMITER_RANGE,
    min_size=9,
    description="Allowed range of the minimum flow limiter",
    source=_B,
    fields=_LIMITER_RANGE_FIELDS,
)

AUTONIGHT = ObjectSpec(
    key="autonight",
    ref=ObjectRef(90, 8),
    type=TYPE_AUTONIGHT,
    version=1,
    min_size=1,
    description="AutoNight (night setback)",
    access=Access.READ_WRITE,
    verified=Verified.ANSWERED,
    source=_B,
    fields=(Field("enabled", 0, Kind.BOOL),),
)

PP_CURVE = ObjectSpec(
    key="pp_curve",
    ref=ObjectRef(90, 4),
    type=TYPE_PP_CURVE,
    min_size=25,
    description="Proportional-pressure curve shape",
    source=_B,
    fields=(
        Field("quadratic", 0, Kind.U8),
        Field("foot_point", 1, Kind.F32),
        Field("unknown_5", 5, Kind.F32, None, Verified.UNKNOWN),
        Field("coefficient_0", 9, Kind.F32),
        Field("coefficient_1", 13, Kind.F32),
        Field("coefficient_2", 17, Kind.F32),
        Field("foot_point_min", 21, Kind.F32),
    ),
)

ALARMS = ObjectSpec(
    key="alarms",
    ref=ObjectRef(88, 0),
    type=TYPE_FAULT_ARRAY,
    description="Active alarm codes (uint16 array, 0-terminated)",
    verified=Verified.ANSWERED,
    source=f"{_H}; SCALA1 community thread",
)
WARNINGS = ObjectSpec(
    key="warnings",
    ref=ObjectRef(88, 11),
    type=TYPE_FAULT_ARRAY,
    description="Active warning codes (uint16 array, 0-terminated)",
    verified=Verified.ANSWERED,
    source=f"{_H}; SCALA1 community thread",
)

STATISTICS = ObjectSpec(
    key="statistics",
    ref=ObjectRef(93, 1),
    type=TYPE_STATISTICS,
    version=2,
    min_size=12,
    description="Operating statistics, cumulative since the pump was commissioned",
    verified=Verified.ANSWERED,
    source=f"{_M} (layout from {_G}; {_H})",
    fields=(
        Field("start_count", 0, Kind.U32, None, Verified.ANSWERED),
        Field("starts_last_hour", 4, Kind.U16, None, Verified.ANSWERED),
        Field("starts_last_24h", 6, Kind.U16, None, Verified.ANSWERED),
        Field("operating_time_s", 8, Kind.U32, "s", Verified.MEASURED, "tracks the wall clock"),
        Field("running_time_24h_s", 12, Kind.U32, "s", Verified.ANSWERED),
        Field("last_cycle_s", 16, Kind.U32, "s", Verified.MEASURED, "matches the last switch-on"),
        Field("power_ups", 20, Kind.U32, None, Verified.ANSWERED),
        Field("power_on_time_s", 24, Kind.U32, "s", Verified.ANSWERED),
    ),
)

TEMPERATURES = ObjectSpec(
    key="temperatures",
    ref=ObjectRef(93, 300),
    type=TYPE_TEMPERATURES,
    version=2,
    min_size=12,
    description="Media, PCB and ambient temperatures",
    verified=Verified.ANSWERED,
    source=_H,
    fields=(
        Field("media_c", 0, Kind.F32, "°C", Verified.ANSWERED),
        Field("pcb_c", 4, Kind.F32, "°C", Verified.ANSWERED),
        Field("ambient_c", 8, Kind.F32, "°C", Verified.ANSWERED),
    ),
)

ENERGY = ObjectSpec(
    key="energy",
    ref=ObjectRef(87, 1),
    type=TYPE_ENERGY,
    min_size=8,
    description="Lifetime energy (float64 joules)",
    verified=Verified.ANSWERED,
    source=_A3,
    fields=(Field("energy_j", 0, Kind.F64, "J", Verified.ANSWERED),),
)

OPTIONAL_OBJECTS: tuple[ObjectSpec, ...] = (
    OPERATION,
    ALARMS,
    WARNINGS,
    STATISTICS,
    TEMPERATURES,
    ENERGY,
    AUTONIGHT,
    MAX_FLOW_LIMITER,
    MIN_FLOW_LIMITER,
    PANEL_SETTING,
)
"""Objects a pump may or may not answer; probed once per setup."""

ALPHA2_GO = Profile(
    name="ALPHA2 GO",
    family=FAMILY,
    type=TYPE,
    objects={
        spec.key: spec
        for spec in (
            PROCESS_VALUES,
            MOTOR_STATE,
            OPERATION,
            LOCAL_REQUEST,
            CONTROL_MODE_REQUEST,
            *FACTORY_LIMITS.values(),
            *USER_SETPOINTS.values(),
            PANEL_SETTING,
            *(panel_preset(i) for i in range(9)),
            MAX_FLOW_LIMITER,
            MIN_FLOW_LIMITER,
            MAX_FLOW_LIMITER_RANGE,
            MIN_FLOW_LIMITER_RANGE,
            AUTONIGHT,
            PP_CURVE,
            ALARMS,
            WARNINGS,
            STATISTICS,
            TEMPERATURES,
            ENERGY,
        )
    },
)

PROFILES: tuple[Profile, ...] = (ALPHA2_GO,)


def profile_for(family: int, type_: int) -> Profile | None:
    """Return the profile for a pump identity, or None if this library has none."""
    return next((p for p in PROFILES if p.matches(family, type_)), None)


def setpoint_unit(mode: int) -> str | None:
    """Wire unit of a setpoint in ``mode``: 'Pa', 'rpm', 'm³/s' or None if unknown."""
    if mode in PRESSURE_MODES:
        return "Pa"
    if mode in SPEED_MODES:
        return "rpm"
    if mode in FLOW_MODES:
        return "m³/s"
    return None


def setpoint_display(mode: int, value: float) -> tuple[float, str] | None:
    """Convert a wire setpoint to display units: (metres, 'm'), (rpm, 'rpm') or (m³/h, 'm³/h')."""
    unit = setpoint_unit(mode)
    if unit == "Pa":
        return value / PA_PER_METRE, "m"
    if unit == "rpm":
        return value, "rpm"
    if unit == "m³/s":
        return value * SECONDS_PER_HOUR, "m³/h"
    return None
