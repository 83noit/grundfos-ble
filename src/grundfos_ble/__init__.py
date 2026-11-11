"""grundfos-ble: Grundfos circulator pumps over GENIbus-on-Bluetooth LE.

Layers, bottom up: :mod:`grundfos_ble.genibus` (framing, transport-agnostic),
:mod:`grundfos_ble.advertisement` (the 0xFE5D advert), :mod:`grundfos_ble.registry`
(what each pump family exposes, with provenance), :mod:`grundfos_ble.transport` (bleak),
:mod:`grundfos_ble.pump` (typed reads; writes are disabled in this release).
"""

from grundfos_ble.advertisement import (
    GENI_CHARACTERISTIC_UUID,
    GRUNDFOS_SERVICE_UUID,
    GRUNDFOS_SERVICE_UUID_16,
    Advertisement,
    parse_advertisement,
)
from grundfos_ble.bluez import remove_bluez_bond
from grundfos_ble.exceptions import (
    ConnectionLost,
    CrcError,
    GrundfosConnectionError,
    GrundfosError,
    NackError,
    NotSupported,
    ObjectError,
    PairingRequired,
    ProtocolError,
    RequestTimeout,
)
from grundfos_ble.pump import (
    WRITES_ENABLED,
    FactoryLimits,
    Faults,
    FlowLimiter,
    GrundfosPump,
    Identity,
    Measurements,
    OperationState,
    ProbeResult,
    Statistics,
    Temperatures,
    UserSetpoint,
)
from grundfos_ble.registry.alpha2go import (
    ALPHA2_GO,
    Command,
    ControlMode,
    ControlSource,
    ErrorCode,
    OperationMode,
    PanelSetting,
    error_code_name,
    profile_for,
    setpoint_display,
    setpoint_unit,
)
from grundfos_ble.transport import BleTransport, Transport

__version__ = "0.1.0"

__all__ = [
    "ALPHA2_GO",
    "GENI_CHARACTERISTIC_UUID",
    "GRUNDFOS_SERVICE_UUID",
    "GRUNDFOS_SERVICE_UUID_16",
    "WRITES_ENABLED",
    "Advertisement",
    "BleTransport",
    "Command",
    "ConnectionLost",
    "ControlMode",
    "ControlSource",
    "CrcError",
    "ErrorCode",
    "FactoryLimits",
    "Faults",
    "FlowLimiter",
    "GrundfosConnectionError",
    "GrundfosError",
    "GrundfosPump",
    "Identity",
    "Measurements",
    "NackError",
    "NotSupported",
    "ObjectError",
    "OperationMode",
    "OperationState",
    "PairingRequired",
    "PanelSetting",
    "ProbeResult",
    "ProtocolError",
    "RequestTimeout",
    "Statistics",
    "Temperatures",
    "Transport",
    "UserSetpoint",
    "__version__",
    "error_code_name",
    "parse_advertisement",
    "profile_for",
    "remove_bluez_bond",
    "setpoint_display",
    "setpoint_unit",
]
