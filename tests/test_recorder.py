"""Recording a session with grundfos_ble.capture: what lands in a capture, and what stays out."""

from __future__ import annotations

import pytest

from grundfos_ble.capture import Recorder, record_capture
from grundfos_ble.exceptions import NotSupported
from grundfos_ble.genibus.apdu import Apdu
from grundfos_ble.genibus.telegram import Telegram
from grundfos_ble.pump import GrundfosPump
from grundfos_ble.testing import FIXTURES, Exchange, ReplayTransport, load_fixture, parse_fixture


def _string(string_id: int, text: str) -> Exchange:
    request = Telegram.request(Apdu(7, 0, bytes((string_id,)))).encode()
    reply = Telegram(0x24, 0xF8, 0xE7, (Apdu(7, 0, text.encode() + b"\x00"),)).encode()
    return Exchange(request, reply)


IDENTITY = Exchange(
    Telegram.request(Apdu(2, 0, bytes((148, 149, 150)))).encode(),
    Telegram(0x24, 0xF8, 0xE7, (Apdu(2, 0, b"\x34\x07\x02"),)).encode(),
)
STRINGS = [
    _string(1, "ALPHA"),
    _string(8, "99999999"),
    _string(9, "SERIAL-SECRET"),
    _string(10, "PRODUCTION-SECRET"),
    _string(17, "my pump"),
    _string(19, "ALPHA 15-58F"),
    _string(50, "SW"),
    _string(58, "BLE-SW"),
]


async def _recorder() -> Recorder:
    recorder = Recorder(
        ReplayTransport(
            [*load_fixture(FIXTURES / "measured-2026-11-09-full.hex"), IDENTITY, *STRINGS]
        )
    )
    await recorder.connect()
    return recorder


def _replies(lines: list[str]) -> str:
    hexes = "".join(line[2:] for line in lines if line.startswith("<")).replace(" ", "")
    return bytes.fromhex(hexes).decode("latin-1")


async def test_capture_reads_everything_and_leaves_out_private_strings() -> None:
    recorder = await _recorder()
    capture = await record_capture(GrundfosPump(recorder), recorder)
    assert (capture.identity.family, capture.identity.type) == (52, 7)
    assert capture.identity.model == "ALPHA 15-58F"
    assert capture.identity.serial_number is None
    assert "measurements" in capture.readings
    assert capture.probe is not None
    lines = capture.fixture_lines("Recorded in a test.", "display: 1.0 m3/h")
    assert lines[0] == "# Recorded in a test."
    assert "# display: 1.0 m3/h" in lines
    assert "SECRET" not in _replies(lines)
    replayed = parse_fixture("\n".join(lines))
    assert len(replayed) == len(capture.exchanges) > 20


async def test_private_is_on_by_default_and_covers_the_pumps_name() -> None:
    recorder = await _recorder()
    capture = await record_capture(GrundfosPump(recorder), recorder, probe=False)
    assert capture.identity.unit_name is None
    assert capture.probe is None
    assert "my pump" not in _replies(capture.fixture_lines("x"))


async def test_private_off_records_everything() -> None:
    recorder = await _recorder()
    capture = await record_capture(GrundfosPump(recorder), recorder, private=False, probe=False)
    assert capture.identity.serial_number == "SERIAL-SECRET"
    assert capture.identity.unit_name == "my pump"


async def test_requests_made_while_not_recording_are_not_kept() -> None:
    recorder = await _recorder()
    pump = GrundfosPump(recorder)
    recorder.recording = False
    identity = await pump.read_identity()
    assert identity.serial_number == "SERIAL-SECRET"
    assert recorder.exchanges == []
    assert recorder.timings  # still timed
    capture = await record_capture(pump, recorder, probe=False)
    assert "SECRET" not in _replies(capture.fixture_lines("x"))


async def test_the_recorder_never_writes() -> None:
    with pytest.raises(NotSupported):
        await (await _recorder()).send(Telegram.request(Apdu(3, 2, b"\x05")))
