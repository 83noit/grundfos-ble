# grundfos-ble

[![CI](https://github.com/83noit/grundfos-ble/actions/workflows/ci.yml/badge.svg)](https://github.com/83noit/grundfos-ble/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/grundfos-ble)](https://pypi.org/project/grundfos-ble/)
[![Python](https://img.shields.io/pypi/pyversions/grundfos-ble)](https://pypi.org/project/grundfos-ble/)
[![License: MIT](https://img.shields.io/github/license/83noit/grundfos-ble)](https://github.com/83noit/grundfos-ble/blob/main/LICENSE)

An async, typed Python library for Grundfos circulator pumps that talk **GENIbus over
Bluetooth LE**. GENIbus is Grundfos's own protocol for reading and setting up its pumps. For
now the only supported pump is the **ALPHA2 GO**.

The message layer doesn't care how the bytes travel, and each pump's list of values is plain
data. That should make related Grundfos Bluetooth pumps (ALPHA3, ALPHA HWR, SCALA1) easier to
add later, but none of them works yet.

**This is an early, read-only release.** It reads what the pump is doing and its settings. It
can't change anything on the pump yet.

It was written for the Home Assistant integration
[ha-grundfos-ble](https://github.com/83noit/ha-grundfos-ble), but it doesn't need Home
Assistant.

This project has nothing to do with Grundfos. Product names are only there to say which pumps
it works with.

**Safety.** A circulator pump is part of your heating. This release only reads, but later
releases will be able to change how the pump runs, and wrong settings can stop the water
moving. The library has been tested on one ALPHA2 GO and comes with no warranty (see the
[licence](https://github.com/83noit/grundfos-ble/blob/main/LICENSE)). Use it at your own risk.

## What it does

- Reads the pump's Bluetooth advertisement (the short broadcast it keeps sending,
  under service UUID `0xFE5D`), including the flag that says someone just pressed its
  Bluetooth button.
- Connects with bleak and bleak-retry-connector, pairs on every connection, and talks GENIbus
  over the pump's one GENI characteristic. It's been run through ESPHome Bluetooth proxies and,
  with the command line, from a Mac. On a local Linux (BlueZ) adapter it registers the pairing
  agent BlueZ needs when a new bond is expected (`BleTransport(..., pairing=True)`); nobody
  has tried that on a real pump yet. On Windows, a failed pairing isn't recognised as
  `PairingRequired` and shows up as `GrundfosConnectionError` instead.
- GENIbus replies don't say which request they answer, so the library matches each reply to
  its request by its shape. If a request doesn't get its reply, it closes the connection, so a
  late answer can't be mistaken for the next one.
- Reads flow, head, power, voltage, current, speed, electronics temperature, control and
  operation mode, setpoint, factory limits, the panel setting, flow limiters, AutoNight, alarms,
  warnings and, if the pump has them, statistics, temperatures and energy. There's no typed
  reader for the stored panel presets: fetch them with `GrundfosPump.get_object` and
  `registry.alpha2go.panel_preset`.
- **Doesn't write yet.** The write methods (control mode, setpoint, limiters, AutoNight,
  start/stop, alarm reset) raise `NotSupported`. See Known limitations below.

## Install

```sh
pip install grundfos-ble
```

You need Python 3.13 or newer. It pulls in `bleak>=3.0.2` and `bleak-retry-connector>=4.7.1`
(and `dbus-fast>=4` on Linux).

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

`BleTransport` also works as an async context manager: `async with BleTransport(device) as
transport:` connects on the way in and disconnects on the way out.

The library never scans by itself; you hand it a `BLEDevice`. Under Home Assistant, that
device comes from the Bluetooth integration, and Home Assistant picks the proxy.

## Command line

`grundfos-ble` talks to a pump through your computer's own Bluetooth adapter. (It can't use an
ESPHome proxy: only Home Assistant can reach those.) Every command only reads. None of them
writes to the pump, and none sends raw bytes you type in.

```console
$ grundfos-ble scan                      # pumps nearby, with their advertisement flags
$ grundfos-ble scan --watch --seconds 300 # every change of a pump's advertisement
$ grundfos-ble pair AA:BB:CC:DD:EE:FF    # waits for the Bluetooth button, then pairs
$ grundfos-ble read AA:BB:CC:DD:EE:FF --json
$ grundfos-ble probe AA:BB:CC:DD:EE:FF   # which optional objects answer, and their versions
$ grundfos-ble get AA:BB:CC:DD:EE:FF 88/25
$ grundfos-ble watch AA:BB:CC:DD:EE:FF --interval 60 --verbose-timings
$ grundfos-ble capture AA:BB:CC:DD:EE:FF session.hex --note "display: 1.0 m3/h"
```

`pair` adds a new bond (a saved pairing) on the pump. The pump has kept at least three at once,
but nobody knows its limit and it has no way to forget one, so only pair computers you'll keep
using. `info` and `capture` leave out the serial number, production code and the name you gave
the pump unless you add `--include-private`, so you can share what they print. `capture` writes
the same file format that `grundfos_ble.testing` reads. Put `-v` before the command to log every
frame. To ask for a pump this library doesn't support yet, see [Asking for a new
pump](https://github.com/83noit/ha-grundfos-ble/blob/main/docs/new-pump.md).

On a Mac, the address is a CoreBluetooth UUID, not a MAC address: copy it from `scan`. Your
terminal needs Bluetooth permission (System Settings → Privacy & Security → Bluetooth), and
during `pair` macOS asks whether to connect to the pump: say yes before the button window
closes. The pump only takes one connection at a time, so close the GO app and pause Home
Assistant (disable the integration entry) while you use the command line.

## Pairing, in one paragraph

The pump drops any connection that isn't encrypted within about two seconds, and it only
accepts a new pairing for about three seconds after you press its Bluetooth button. The bond
then lives on the adapter or proxy that paired. In testing, the pump kept at least three bonds
at once: a phone, an ESPHome proxy and a Mac. Every connection still has to call `pair()`;
with a bond, that's just a quick re-encrypt. You get `PairingRequired` when the pump turns the
pairing down (ESP-IDF reason 82 through an ESPHome proxy, or BlueZ `AuthenticationFailed`). If
a connection comes up but the pump never answers, `reply_seen` stays false; outside pairing
that points at the radio, not the bond, so don't re-pair because of it. Nobody has seen a pump
lose a bond yet, so we don't know what that looks like. A local BlueZ adapter would keep
reconnecting with the old bond and never pair again, so call `remove_bluez_bond(device)` before
re-pairing through one, after pressing the button. If an ESPHome proxy's firmware can't pair,
you get `PairingUnsupported`; the pump only answers encrypted connections, so that proxy needs
a newer ESPHome. `BleTransport.last_source` names the adapter or proxy the last attempt went
through, so you can tell which path a refusal came from.

## Protocol

A GENIbus telegram looks like `SD LEN DA SA APDU… CRC16` (CRC-16/CCITT, start value 0xFFFF,
inverted). Requests start `27 … E7 F8`, replies `24 … F8 E7`, and frames go out in 20-byte
pieces. Most values live in class-10 "data objects", addressed by `(id, sub-id)`. Their replies
start with a `status type version size` header and carry big-endian floats. The full list, with
where each item comes from, is in
[docs/protocol.md](https://github.com/83noit/grundfos-ble/blob/main/docs/protocol.md).

## Known limitations

**It can't write to a pump yet.** The write methods raise `NotSupported`, and this project has
never sent a write to a pump. The code that builds write frames is only tested against frames
recorded from the Grundfos GO app. Once a release does support writes, a pump will only write
if you created it with `GrundfosPump(transport, allow_writes=True)`, so upgrading never starts
writes by itself. That check lives in `GrundfosPump` only: `BleTransport.request()` and the
GENIbus encoders send whatever you give them. Before writes can be switched on, at least these
need fixing:

- `set_flow_limiter` doesn't check the limit against the pump's range, so it takes 0,
  negative, NaN and huge values. A maximum flow near zero could stop water moving through a
  circuit.
- `set_setpoint` skips its range check when the factory limits aren't available.
- `Access` and `MODES_SEEN_ON_ALPHA2_GO` aren't enforced, so objects whose meaning nobody knows
  could be written.
- `set_setpoint` writes the control mode first. If the second write then fails, the mode has
  already changed and the error doesn't tell you. The value read back is returned but not
  compared, and the pump quietly clamps what it gets.
- Read-modify-write builds the object again instead of patching the raw bytes, and doesn't
  refuse a different object version or size.
- Cancelling halfway through a write can leave part of a SET at the pump.

Reads have limits too:

- The object version isn't checked, so a firmware that changes a layout but keeps the same
  type would decode into wrong values.
- The advertisement parser only checks the length, not the `03 01 00` prefix.

## Development

```sh
nix develop          # or: uv sync --group dev && source .venv/bin/activate
ruff check . && ruff format --check .
mypy
pytest --cov
```

Measurements, operation state, statistics and the other optional objects are tested against
frames recorded from a real ALPHA2 GO (`src/grundfos_ble/testdata/`, which other projects can
use as `grundfos_ble.testing.FIXTURES`). Identity and string reads are only tested on
hand-built or ALPHA HWR frames, and the Bluetooth connection (`establish_connection`) is always
mocked. If you add recordings, use the same format (`>` request, `<` notification pieces)
rather than building frames by hand.

## Status

Alpha, read-only. The process and motor values were checked against the display of one ALPHA2
GO. That pump also gave sensible-looking answers for the operation state, panel setting, flow
limiters, AutoNight, alarms, warnings, statistics, temperatures and energy, with no fault
active. The other objects come from recorded traffic or from documentation for related pumps.
Nothing has been tried on other pump families.

## Acknowledgements

This library was written from the GENIbus specification and from frames recorded on real
pumps. The Bluetooth link, including the BlueZ pairing agent adapted from alpha-hwr, lives in
[ble-bond](https://github.com/83noit/ble-bond), which carries alpha-hwr's MIT notice. No code
from the projects below is used here, but their published protocol research made this
possible:

- [esphome-alpha2go](https://github.com/Birkemosen/esphome-alpha2go) (GPL-3.0): ALPHA2 GO traffic captures and object layouts
- [alpha-hwr](https://eman.github.io/alpha-hwr/) (MIT): ALPHA HWR protocol documentation
- [gofree](https://github.com/tatutsa888/gofree) (MIT): SCALA1 protocol documentation
- [esphome-grundfos-alpha3](https://github.com/Seba65439/esphome-grundfos-alpha3) (MIT): ALPHA3 decoders

## Licence

MIT, see [LICENSE](https://github.com/83noit/grundfos-ble/blob/main/LICENSE).
