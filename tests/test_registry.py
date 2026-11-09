"""Profile consistency and conversion helpers."""

from itertools import pairwise

import pytest

from grundfos_ble.registry import alpha2go
from grundfos_ble.registry.base import Kind, ObjectSpec, Verified


def test_profile_lookup() -> None:
    assert alpha2go.profile_for(52, 6) is alpha2go.ALPHA2_GO
    assert alpha2go.profile_for(52, 7) is None
    assert alpha2go.ALPHA2_GO.matches(52, 6)


@pytest.mark.parametrize("spec", alpha2go.ALPHA2_GO.objects.values(), ids=lambda s: s.key)
def test_field_layout_is_consistent_with_min_size(spec: ObjectSpec) -> None:
    fields = sorted(spec.fields, key=lambda f: f.offset)
    for left, right in pairwise(fields):
        assert left.offset + left.size <= right.offset, f"{left.key} overlaps {right.key}"
    if fields and spec.min_size:
        assert fields[0].offset + fields[0].size <= spec.min_size, spec.key
    values = spec.decode(bytes(spec.min_size))
    for field in fields:
        fits = field.offset + field.size <= spec.min_size
        assert (values[field.key] is not None) is fits, f"{spec.key}.{field.key}"


def test_measured_fields_are_in_measured_objects() -> None:
    measured = {k for k, s in alpha2go.ALPHA2_GO.objects.items() if s.verified is Verified.MEASURED}
    assert measured == {"process_values", "motor_state"}
    assert all(spec.source for spec in alpha2go.ALPHA2_GO.objects.values())


def test_setpoint_units() -> None:
    assert alpha2go.setpoint_unit(alpha2go.ControlMode.CONSTANT_PRESSURE) == "Pa"
    assert alpha2go.setpoint_unit(alpha2go.ControlMode.CONSTANT_CURVE) == "rpm"
    assert alpha2go.setpoint_unit(alpha2go.ControlMode.CONSTANT_FLOW) == "m³/s"
    assert alpha2go.setpoint_unit(alpha2go.ControlMode.NONE) is None
    metres, unit = alpha2go.setpoint_display(0, 24510.0) or (0.0, "")
    assert unit == "m"
    assert metres == pytest.approx(2.4993, rel=1e-3)
    assert alpha2go.setpoint_display(2, 5225.0) == (5225.0, "rpm")
    value, unit = alpha2go.setpoint_display(8, 5.556e-4) or (0.0, "")
    assert unit == "m³/h"
    assert value == pytest.approx(2.0, rel=1e-3)
    assert alpha2go.setpoint_display(254, 1.0) is None


def test_error_code_names() -> None:
    assert alpha2go.error_code_name(51) == "motor_blocked"
    assert alpha2go.error_code_name(57) == "dry_running"
    assert alpha2go.error_code_name(999) == "unknown_999"


def test_panel_presets_and_decode() -> None:
    preset = alpha2go.panel_preset(2)
    assert preset.ref.sub == 353
    values = preset.decode(b"\x02\x45\xc6\x70\x00")  # CC, 6350 rpm
    assert values == {"control_mode": 2, "setpoint": 6350.0}
    assert alpha2go.AUTONIGHT.decode(b"\x01") == {"enabled": True}
    assert alpha2go.STATISTICS.fields[0].kind is Kind.U32
