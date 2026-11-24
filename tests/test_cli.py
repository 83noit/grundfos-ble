"""The command line, against the real pump's recorded frames."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from bleak.backends.device import BLEDevice

from grundfos_ble import cli
from grundfos_ble.exceptions import NotSupported
from grundfos_ble.genibus.apdu import Apdu
from grundfos_ble.genibus.telegram import Telegram
from grundfos_ble.testing import FIXTURES, Exchange, ReplayTransport, load_fixture

ADDRESS = "AA:BB:CC:DD:EE:FF"
GRUNDFOS = "0000fe5d-0000-1000-8000-00805f9b34fb"


def _string(string_id: int, text: str) -> Exchange:
    request = Telegram.request(Apdu(7, 0, bytes((string_id,)))).encode()
    reply = Telegram(0x24, 0xF8, 0xE7, (Apdu(7, 0, text.encode() + b"\x00"),)).encode()
    return Exchange(request, reply)


IDENTITY = Exchange(
    Telegram.request(Apdu(2, 0, bytes((148, 149, 150)))).encode(),
    Telegram(0x24, 0xF8, 0xE7, (Apdu(2, 0, b"\x34\x06\x00"),)).encode(),
)
STRINGS = [
    _string(1, "ALPHA2 GO"),
    _string(8, "93074208"),
    _string(9, "SERIAL-SECRET"),
    _string(10, "PRODUCTION-SECRET"),
    _string(17, "alpha2go"),
    _string(19, "ALPHA2 GO XX-75"),
    _string(50, "92975974V01.05.00.00888"),
    _string(58, "93127901V06.00.03.00001"),
]


@pytest.fixture
def pump() -> Any:
    replay = ReplayTransport(
        [*load_fixture(FIXTURES / "measured-2026-11-09-full.hex"), IDENTITY, *STRINGS]
    )
    device = BLEDevice(ADDRESS, "alpha2go", {})
    made: list[dict[str, Any]] = []

    def transport(_device: BLEDevice, **kwargs: Any) -> ReplayTransport:
        made.append(kwargs)
        return replay

    with (
        patch.object(cli, "BleTransport", transport),
        patch.object(cli.BleakScanner, "find_device_by_address", AsyncMock(return_value=device)),
    ):
        yield SimpleNamespace(replay=replay, made=made)


def test_info_leaves_out_private_strings(pump: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["info", ADDRESS]) == 0
    out = capsys.readouterr().out
    assert "ALPHA2 GO XX-75" in out
    assert "profile:              ALPHA2 GO" in out
    assert "SECRET" not in out
    assert "alpha2go" not in out
    assert all(
        frame[4:7] not in (b"\x07\x01\x09", b"\x07\x01\x0a", b"\x07\x01\x11")
        for frame in pump.replay.sent
    )


def test_info_can_include_them(pump: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["info", ADDRESS, "--include-private"]) == 0
    out = capsys.readouterr().out
    assert "SERIAL-SECRET" in out
    assert "unit_name:            alpha2go" in out


def test_include_serial_still_works(pump: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["info", ADDRESS, "--include-serial"]) == 0
    assert "SERIAL-SECRET" in capsys.readouterr().out


def test_read_json(pump: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["read", ADDRESS, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["measurements"]["flow_m3h"] == pytest.approx(1.004, abs=0.001)
    assert result["statistics"]["start_count"] == 5
    assert "errors" not in result
    assert not pump.replay.is_connected
    assert pump.made == [{"pairing": False}]


def test_probe_reports_versions(pump: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["probe", ADDRESS]) == 0
    out = capsys.readouterr().out
    assert "statistics           93/1     supported version 2" in out
    assert "expects" not in out


def test_get_one_object(pump: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["get", ADDRESS, "93/300"]) == 0
    out = capsys.readouterr().out
    assert "status 0 type 534 version 2 size 13" in out


def test_get_rejects_a_bad_reference(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["get", ADDRESS, "88"])


def test_watch_times_each_request(pump: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["watch", ADDRESS, "--count", "1", "--verbose-timings"]) == 0
    out = capsys.readouterr().out
    assert "first reply" in out
    assert "lost 0" in out
    assert "flow 1.00 m³/h" in out
    assert "statistics" in out


def test_capture_writes_a_fixture_that_replays(
    pump: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "capture.hex"
    assert cli.main(["capture", ADDRESS, str(path), "--note", "display: 1.0 m3/h"]) == 0
    text = path.read_text()
    assert "SECRET" not in bytes.fromhex(
        "".join(line[2:] for line in text.splitlines() if line.startswith("<")).replace(" ", "")
    ).decode("latin-1")
    assert "# display: 1.0 m3/h" in text
    exchanges = load_fixture(path)
    assert len(exchanges) > 20
    assert all(Telegram.decode_reply(e.reply) for e in exchanges)


def test_pair_waits_for_the_button_then_pairs(
    pump: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    device = BLEDevice(ADDRESS, "alpha2go", {})
    with patch.object(cli, "_wait_for_button", AsyncMock(return_value=device)):
        assert cli.main(["pair", ADDRESS]) == 0
    captured = capsys.readouterr()
    assert "new bond" in captured.err
    assert "Paired in" in captured.out
    assert pump.made == [{"pairing": True}]


def test_scan_decodes_adverts(capsys: pytest.CaptureFixture[str]) -> None:
    device = BLEDevice(ADDRESS, "alpha2go", {})
    advert = SimpleNamespace(
        local_name="alpha2go",
        rssi=-60,
        service_data={GRUNDFOS: bytes.fromhex("0301003406063011abcd")},
    )
    other = SimpleNamespace(local_name="tv", rssi=-70, service_data={})
    found = {ADDRESS: (device, advert), "11:22:33:44:55:66": (device, other)}
    with patch.object(cli.BleakScanner, "discover", AsyncMock(return_value=found)):
        assert cli.main(["scan"]) == 0
    out = capsys.readouterr().out
    assert "family 52 type 6 (ALPHA2 GO)" in out
    assert "[button_pressed,client_connected]" in out
    assert out.count("\n") == 1


def test_scan_without_pumps(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(cli.BleakScanner, "discover", AsyncMock(return_value={})):
        assert cli.main(["scan"]) == 1
    assert "No Grundfos pump found" in capsys.readouterr().err


def test_an_unseen_pump_is_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(cli.BleakScanner, "find_device_by_address", AsyncMock(return_value=None)):
        assert cli.main(["read", ADDRESS]) == 1
    assert "was not seen" in capsys.readouterr().err


async def test_the_recorder_never_writes() -> None:
    recorder = cli.Recorder(ReplayTransport([]))
    with pytest.raises(NotSupported):
        await recorder.send(Telegram.request(Apdu(3, 2, b"\x05")))


def test_no_command_writes() -> None:
    """Guard: the command line offers reads only (no start, stop, set or raw send)."""
    parser = cli.build_parser()
    commands = parser._subparsers._group_actions[0].choices  # type: ignore[union-attr]
    assert set(commands) == {"scan", "pair", "info", "read", "probe", "get", "watch", "capture"}


def test_advertisement_scans_pass_no_service_filter() -> None:
    """CoreBluetooth filters on advertised service UUIDs; the pump sends 0xFE5D as data only."""
    with patch.object(cli, "BleakScanner") as scanner:
        cli._scanner(lambda _device, _data: None)
    assert "service_uuids" not in scanner.call_args.kwargs
