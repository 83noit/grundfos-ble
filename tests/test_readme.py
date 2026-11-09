"""The README's usage example, run against the real API."""

import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from bleak import BleakScanner
from bleak.backends.device import BLEDevice

from grundfos_ble import BleTransport
from grundfos_ble.genibus import classic
from grundfos_ble.genibus.apdu import DataClass
from grundfos_ble.genibus.telegram import Telegram
from grundfos_ble.registry import alpha2go
from grundfos_ble.testing import FIXTURES, Exchange, ReplayTransport, load_fixture

README = Path(__file__).parent.parent / "README.md"


def _replay() -> ReplayTransport:
    ids = (148, 149, 150)
    exchanges = [
        *load_fixture(FIXTURES / "measured-2026-11-07.hex"),
        Exchange(
            Telegram.request(classic.get_request(DataClass.MEASURED_DATA, ids)).encode(),
            Telegram.request(
                classic.Apdu(DataClass.MEASURED_DATA, 0, b"\x34\x06\x00"), da=0xF8, sa=0xE7, sd=0x24
            ).encode(),
        ),
    ]
    for string_id in alpha2go.StringId:
        request = Telegram.request(classic.get_request(DataClass.ASCII_STRINGS, (string_id,)))
        reply = Telegram.request(
            classic.Apdu(DataClass.ASCII_STRINGS, 0, b"ALPHA2 GO\x00"), da=0xF8, sa=0xE7, sd=0x24
        )
        exchanges.append(Exchange(request.encode(), reply.encode()))
    return ReplayTransport(exchanges)


def test_usage_example_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    match = re.search(r"```python\n(.*?)```", README.read_text(encoding="utf-8"), re.DOTALL)
    assert match is not None
    device = BLEDevice("AA:BB:CC:DD:EE:FF", "Test pump", {})
    monkeypatch.setattr(BleakScanner, "find_device_by_address", AsyncMock(return_value=device))
    replay = _replay()
    monkeypatch.setattr(BleTransport, "connect", replay.connect)
    monkeypatch.setattr(BleTransport, "disconnect", replay.disconnect)
    monkeypatch.setattr(BleTransport, "request", replay.request)

    exec(compile(match.group(1), str(README), "exec"), {"__name__": "readme"})

    output = capsys.readouterr().out
    assert output.startswith("ALPHA2 GO 1.42")
    assert "m³/h" in output
    assert not replay.is_connected
