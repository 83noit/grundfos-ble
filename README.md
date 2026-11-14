# grundfos-ble

[![CI](https://github.com/83noit/grundfos-ble/actions/workflows/ci.yml/badge.svg)](https://github.com/83noit/grundfos-ble/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/grundfos-ble)](https://pypi.org/project/grundfos-ble/)
[![Python](https://img.shields.io/pypi/pyversions/grundfos-ble)](https://pypi.org/project/grundfos-ble/)
[![License: MIT](https://img.shields.io/github/license/83noit/grundfos-ble)](LICENSE)

Async, typed Python library for Grundfos circulator pumps that speak **GENIbus over
Bluetooth LE**. The only supported pump today is the **ALPHA2 GO**. The framing layer is
transport-agnostic and the pump profile is data, which should make related Grundfos Bluetooth
pumps (ALPHA3, ALPHA HWR, SCALA1) easier to add, but none of them is supported yet.

**This is a preliminary, read-only release.** It reads a pump's state and values. Writing to a
pump is not supported yet.

The library was written for use under Home Assistant, but it does not depend on it.

This project is not affiliated with, endorsed by or supported by Grundfos. Product names are
used only to identify compatible pumps.

## What it does

- Decodes the pump's advertisement (service UUID `0xFE5D`), including the flag that says the
  Bluetooth button was just pressed.
- Connects through bleak and bleak-retry-connector, pairs on every connection, and talks GENIbus
  over the single GENI characteristic. Only ESPHome Bluetooth proxies have been tested. On a
  local BlueZ adapter it registers the pairing agent BlueZ needs while it pairs; that path is
  untested on hardware here. WinRT pairing failures are not recognised as `PairingRequired` and
  surface as `GrundfosConnectionError`.
- Reads flow, head, power, voltage, current, speed, electronics temperature, control and
  operation mode, setpoint, factory limits, the panel setting, flow limiters, AutoNight, alarms,
  warnings and, where the pump has them, statistics, temperatures and energy. The stored panel
  presets have no typed reader. Fetch them with `GrundfosPump.get_object` and
  `registry.alpha2go.panel_preset`.
- Does **not** support writes yet. The write methods (control mode, setpoint, limiters, AutoNight,
  start/stop, alarm reset) raise `NotSupported`. See [Known limitations](#known-limitations).

## Install

```sh
pip install grundfos-ble
```

Python 3.13 or newer; depends on `bleak>=3` and `bleak-retry-connector>=4`.

## Usage

```python
import asyncio

from bleak import BleakScanner

from grundfos_ble import BleTransport, GrundfosPump, PairingRequired


async def main() -> None:
    device = await BleakScanner.find_device_by_address("AA:BB:CC:DD:EE:FF")
    if device is None:
        raise SystemExit("Pump not found")

    transport = BleTransport(device, name="My pump")
    pump = GrundfosPump(transport)

    try:
        await transport.connect()  # pairs (encrypts) every time
    except PairingRequired:
        print("Press the pump's Bluetooth button and connect again within 3 seconds")
        raise

    try:
        identity = await pump.read_identity()
        values = await pump.read_measurements()
        print(identity.model, values.flow_m3h, "m³/h", values.head_m, "m", values.power_w, "W")
    finally:
        await transport.disconnect()


asyncio.run(main())
```

The library never scans; you hand it a `BLEDevice`. Under Home Assistant that device comes
from the Bluetooth integration and HA chooses the proxy.

## Pairing, in one paragraph

The pump drops any link that is not encrypted within about two seconds, and accepts pairing
only for about three seconds after its Bluetooth button is pressed. Once paired, the bond lives
on the adapter or proxy that paired. When tested, the pump held at least two bonds at once, a
phone and an ESPHome proxy. Every connection must still call `pair()`; with a bond that is a quick
re-encrypt. `PairingRequired` is raised when the pump refuses pairing (ESP-IDF reason 82, seen
through ESPHome proxies, or BlueZ `AuthenticationFailed`). A link that connects but never answers
shows up as `reply_seen` staying false; outside pairing that points at the radio, not the bond,
so do not treat it as a reason to re-pair. A pump losing a bond has never been observed, so how
that looks is unknown. A local BlueZ adapter would keep reconnecting with the dropped bond and never
pair again, so call `remove_bluez_bond(device)` before re-pairing through one.

## Protocol

GENIbus telegram `SD LEN DA SA APDU… CRC16` (CRC-16/CCITT, init 0xFFFF, inverted), requests
`27 … E7 F8`, replies `24 … F8 E7`, frames written in 20-byte pieces. Most values live in
class-10 "data objects" addressed by `(id, sub-id)` whose replies carry a
`status type version size` header and big-endian floats. See [docs/protocol.md](https://github.com/83noit/grundfos-ble/blob/main/docs/protocol.md)
for the full inventory with the provenance of every item.

## Known limitations

**Writing to a pump is not supported yet.** The write methods raise `NotSupported`, and this
project has never sent a write to a pump. The encoders behind them are tested against frames
captured from the Grundfos GO app, and that is all. Before writes can be enabled, at least these
need fixing:

- `set_flow_limiter` does not check the limit against the pump's range, so it accepts 0, negative,
  NaN and huge values. A near-zero maximum flow could starve a circuit of circulation.
- `set_setpoint` skips its range check when the factory limits are not available.
- `Access` and `MODES_SEEN_ON_ALPHA2_GO` are not enforced, so objects whose meaning is unknown
  could be written.
- `set_setpoint` writes the control mode first. If the second write fails, the mode has changed
  and the error does not say so. The read-back is returned but not compared, and the pump clamps
  silently.
- Read-modify-write rebuilds objects instead of patching the raw bytes, and does not refuse a
  different object version or size.
- Cancelling mid-write can leave part of a SET at the pump.

Reads have limitations too:

- The object version is not checked, so a firmware that changes a layout under the same type
  would decode into wrong values.
- The advertisement parser checks only the length, not the `03 01 00` prefix.

## Development

```sh
nix develop          # or: uv sync --group dev && source .venv/bin/activate
ruff check . && ruff format --check .
mypy
pytest --cov
```

Measurements, operation state, statistics and the other optional objects are tested against frames
recorded from a real ALPHA2 GO (`src/grundfos_ble/testdata/`, available to dependants as
`grundfos_ble.testing.FIXTURES`). Identity and string reads are tested only on hand-built or ALPHA
HWR frames, and the Bluetooth connection (`establish_connection`) is always mocked. Add new captures
in the same format (`>` request, `<` notification pieces) rather than hand-built frames.

## Status

Alpha, read-only. Process and motor values were checked against the panel of one ALPHA2 GO. That pump also
answered the operation state, panel setting, flow limiters, AutoNight, alarms, warnings, statistics,
temperatures and energy with plausible values, with no fault active. The remaining objects come from
captured traffic or documentation for related pumps. Other pump families are unverified.

## Acknowledgements

This library was written from the GENIbus specification and from frames captured on real pumps.
No code from these projects is used, but their published protocol research made the work possible:

- [esphome-alpha2go](https://github.com/Birkemosen/esphome-alpha2go) (GPL-3.0): ALPHA2 GO traffic captures and object layouts
- [alpha-hwr](https://eman.github.io/alpha-hwr/) (MIT): ALPHA HWR protocol documentation
- [gofree](https://github.com/tatutsa888/gofree) (MIT): SCALA1 protocol documentation
- [esphome-grundfos-alpha3](https://github.com/Seba65439/esphome-grundfos-alpha3) (MIT): ALPHA3 decoders

## Licence

MIT, see [LICENSE](LICENSE).
