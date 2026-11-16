"""Every frame and object the GO app exchanged in the capture, against the codecs and registry."""

from __future__ import annotations

import pytest

from grundfos_ble.genibus.apdu import DataClass, Operation
from grundfos_ble.genibus.objects import ObjectHeader, ObjectRef, set_request
from grundfos_ble.genibus.telegram import Telegram
from grundfos_ble.pump import (
    decode_flow_limiter,
    decode_operation,
    decode_user_setpoint,
    encode_control_mode_request,
    encode_flow_limiter,
    encode_user_setpoint,
)
from grundfos_ble.registry import alpha2go
from grundfos_ble.registry.alpha2go import ControlMode

from .go_app_capture import (
    GO_APP_IDENTITY_REQUEST,
    GO_APP_IDENTITY_VALUES,
    GO_APP_OBJECTS,
    GO_APP_SETS,
)

_BY_REF = {str(spec.ref): spec for spec in alpha2go.ALPHA2_GO.objects.values()}


def _ref(text: str) -> ObjectRef:
    object_id, sub = text.split("/")
    return ObjectRef(int(object_id), int(sub))


@pytest.mark.parametrize(
    ("ref", "note", "frame"),
    GO_APP_SETS,
    ids=[f"{r}-{i}" for i, (r, _, _) in enumerate(GO_APP_SETS)],
)
def test_every_captured_set_matches_the_codecs(ref: str, note: str, frame: str) -> None:
    raw = bytes.fromhex(frame)
    telegram = Telegram.decode(raw)
    apdu = telegram.apdu
    assert (apdu.data_class, apdu.op) == (DataClass.DATA_OBJECTS, Operation.SET)
    assert apdu.data[:3] == _ref(ref).to_bytes()
    header = ObjectHeader.decode(b"\x00" + apdu.data[3:9])
    data = apdu.data[9:]
    assert header.size == len(data)
    assert header.version == 1
    # Rebuilt from its parts, the frame comes out byte for byte.
    rebuilt = Telegram.request(set_request(_ref(ref), header.type, header.version, data))
    assert rebuilt.encode() == raw
    if (spec := _BY_REF.get(ref)) is not None:
        assert (header.type, header.version) == (spec.type, spec.version), note
        assert len(data) >= spec.min_size


@pytest.mark.parametrize(
    "frame", [f for r, _, f in GO_APP_SETS if r == "86/10"], ids=lambda f: f[51:53]
)
def test_control_mode_writes_are_what_the_encoder_builds(frame: str) -> None:
    mode = ControlMode(bytes.fromhex(frame)[17])
    data = encode_control_mode_request(mode)
    request = Telegram.request(set_request(ObjectRef(86, 10), 303, 1, data))
    assert request.encode() == bytes.fromhex(frame)


@pytest.mark.parametrize(
    ("ref", "frame"),
    [(r, f) for r, _, f in GO_APP_SETS if r in ("86/14", "86/16", "86/18", "86/40")],
    ids=lambda v: v if "/" in v else v[-5:],
)
def test_setpoint_writes_round_trip(ref: str, frame: str) -> None:
    data = Telegram.decode(bytes.fromhex(frame)).apdu.data[9:]
    current = decode_user_setpoint(data)
    assert current.setpoint is not None
    assert encode_user_setpoint(current, current.setpoint) == data


@pytest.mark.parametrize(
    ("ref", "frame"),
    [(r, f) for r, _, f in GO_APP_SETS if r in ("86/600", "86/601")],
    ids=lambda v: v if "/" in v else v[-5:],
)
def test_limiter_writes_round_trip(ref: str, frame: str) -> None:
    data = Telegram.decode(bytes.fromhex(frame)).apdu.data[9:]
    limiter = decode_flow_limiter(data)
    assert limiter.limiter_id == (1 if ref == "86/600" else 2)
    assert data.startswith(_BY_REF[ref].leading)
    assert encode_flow_limiter(data, limiter.enabled, None) == data


_KNOWN_OBJECTS = [row for row in GO_APP_OBJECTS if row[0] in _BY_REF]


@pytest.mark.parametrize(
    ("ref", "object_type", "data", "reading"),
    _KNOWN_OBJECTS,
    ids=[f"{r}-{i}" for i, (r, *_rest) in enumerate(_KNOWN_OBJECTS)],
)
def test_every_captured_object_fits_the_registry(
    ref: str, object_type: int, data: str, reading: str
) -> None:
    spec = _BY_REF[ref]
    raw = bytes.fromhex(data)
    assert spec.type == object_type, reading
    assert len(raw) >= spec.min_size
    assert raw.startswith(spec.leading)
    spec.decode(raw)


def test_panel_presets_are_whole_multiples_of_9804() -> None:
    """The factory presets read back to whole half-metres only with 9804 Pa/m (F1)."""
    heads = []
    for ref, _type, data, _reading in GO_APP_OBJECTS:
        spec = _BY_REF.get(ref)
        if spec is None or not ref.startswith("86/35") or ref == "86/350":
            continue
        values = spec.decode(bytes.fromhex(data))
        mode, setpoint = values["control_mode"], values["setpoint"]
        assert isinstance(mode, int)
        assert isinstance(setpoint, float)
        if mode in alpha2go.PRESSURE_MODES:
            display = alpha2go.setpoint_display(mode, setpoint)
            assert display is not None
            heads.append(display[0])
    assert heads == [2.5, 5.0, 7.5, 4.0, 6.0, 8.0]


def test_captured_realised_operation() -> None:
    data = next(bytes.fromhex(d) for r, _t, d, _n in GO_APP_OBJECTS if r == "86/8")
    state = decode_operation(data)
    assert (state.control_source, state.operation_mode, state.control_mode) == (1, 0, 0)
    assert state.setpoint_display == (2.5, "m")


def test_the_apps_identity_read() -> None:
    request = Telegram.decode(bytes.fromhex(GO_APP_IDENTITY_REQUEST))
    assert request.da == 0xFF  # the app asks the broadcast address
    assert request.apdu.data == bytes((148, 149, 150))
    family, unit_type, _version = bytes.fromhex(GO_APP_IDENTITY_VALUES)
    assert alpha2go.profile_for(family, unit_type) is alpha2go.ALPHA2_GO
