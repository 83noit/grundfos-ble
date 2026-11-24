"""An ALPHA HWR (ALPHA 15-58F), from a real pump's recording, against the GO app's values."""

from __future__ import annotations

import pytest

from grundfos_ble import ALPHA_HWR, ControlMode, GrundfosPump, NotSupported
from grundfos_ble.testing import FIXTURES, ReplayTransport, load_fixture

RECORDING = FIXTURES.parent / "alpha_hwr" / "captured-2026-11-24.hex"


@pytest.fixture
async def pump() -> GrundfosPump:
    transport = ReplayTransport(load_fixture(RECORDING))
    await transport.connect()
    return GrundfosPump(transport, ALPHA_HWR)


async def test_identity(pump: GrundfosPump) -> None:
    identity = await pump.read_identity(strings=False)
    assert (identity.family, identity.type) == (52, 7)
    assert identity.profile is ALPHA_HWR


async def test_measurements_match_the_go_app(pump: GrundfosPump) -> None:
    m = await pump.read_measurements()
    assert m.flow_m3h == pytest.approx(0.1, abs=0.05)
    assert m.head_m == pytest.approx(1.8, abs=0.05)
    assert m.power_w == pytest.approx(5, abs=1)
    assert m.speed_rpm == pytest.approx(2871, abs=20)
    assert m.grid_voltage_v == pytest.approx(116, abs=1)
    # The pump's own head factor is the ALPHA2 GO's, 9804 Pa per metre.
    assert m.differential_pressure_pa / m.head_m == pytest.approx(9804, abs=1)
    # 87/69 @32 is zero bytes on this pump: not 0 °C.
    assert m.electronics_temperature_c is None


async def test_the_rest_matches_the_go_app(pump: GrundfosPump) -> None:
    operation = await pump.read_operation()
    assert operation.control_mode == ControlMode.CP_AUTOADAPT
    faults = await pump.read_faults()
    assert not faults.alarms
    assert not faults.warnings
    with pytest.raises(NotSupported):
        await pump.read_autonight()
    await pump.read_flow_limiters()
    statistics = await pump.read_statistics()
    assert statistics.start_count == 5
    assert statistics.operating_time_s // 3600 == 88 * 24 + 19
    temperatures = await pump.read_temperatures()
    assert temperatures.media_c == pytest.approx(30, abs=1)
    assert await pump.read_energy_kwh() == pytest.approx(13.67, abs=0.01)
