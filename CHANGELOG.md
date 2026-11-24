# Changelog

Notable changes, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). Before 1.0, a minor release (0.3 to 0.4) may break
the API or change defaults; a patch release only fixes bugs.

## [Unreleased]

### Added

- `grundfos_ble.capture`: `Recorder`, `record_capture()` and `Capture`, which read the identity,
  every known object and the probe once and keep every frame, in the fixture format. The command
  line's `capture` now uses them, and so can Home Assistant, so the owner of a pump this library
  doesn't support yet can describe it without a computer. A recorder never writes. With
  `private=True`, the default, it leaves out the serial number, the production code and the
  name the owner gave the pump.

### Changed

- `info` and `capture` take `--include-private` in place of `--include-serial`, which still
  works. Without it they now also leave out the name you gave the pump.
- The README is rewritten in plainer English, along with this changelog, the security policy
  and the prose in `docs/protocol.md`. On the way, the docs now say the library has also run
  from a Mac, that the pump has kept at least three bonds at once, and that it stops
  advertising while a client is connected (so flag 0x01 can't tell you someone else is).

## [0.4.0] - 2026-11-16

### Added

- A read-only command line, `grundfos-ble`: `scan` (with `--watch`), `pair`, `info`, `read`,
  `probe`, `get`, `watch` and `capture`. It goes through your computer's own Bluetooth
  adapter, never writes to the pump, and never sends raw bytes you type in. `capture` saves a
  session in the test fixture format. `info` and `capture` leave out the serial number and
  production code unless you ask for them.

## [0.3.0] - 2026-11-16

### Changed

- The Bluetooth link now comes from [ble-bond](https://github.com/83noit/ble-bond) 1.x, which
  grundfos-ble and mira-ble share. That covers pairing, noting which proxy each attempt used,
  the BlueZ pairing agent, and sending one request at a time. `BleTransport`, its exceptions
  and `grundfos_ble.bluez` keep their names and work as before; the pump's link settings are
  in `grundfos_ble.transport.POLICY`. alpha-hwr's notice moves to ble-bond along with the
  agent.

## [0.2.0] - 2026-11-16

### Added

- Python 3.15 support, tested in CI.
- `GrundfosPump(..., allow_writes=True)`. Writes are still off in this release. Once a release
  turns them on, a pump only writes if you created it with `allow_writes=True`, so upgrading
  the library never starts writing by itself.
- `BleTransport` works as an async context manager: `async with` connects, then disconnects.
- `PairingUnsupported`, raised when an ESPHome proxy's firmware can't pair at all. Retrying or
  pressing the pump's button won't help: the proxy needs a newer ESPHome.
- `ObjectVersionError`, raised for an object when the pump answers with a version this library
  has no layout for. Every object the probe reads now has a version, taken from the real pump.
- `BleTransport.last_source`: the adapter or proxy the last connection attempt went through,
  taken from Home Assistant's choice when there is one.
- `BleTransport(..., pairing=True)`, and `Transport.request(..., expect=...)` for a check the
  reply has to pass.
- `ProbeResult.denied` and `ProbeResult.unknown`, which tell apart objects the pump says it
  doesn't have and objects that got no usable answer.
- `setpoint_wire`, the reverse of `setpoint_display`.
- `ReplayTransport` can now lag, ignore chosen requests, send stray or duplicate frames and
  answer BUSY once, and it closes the link on a timeout just like `BleTransport`.
- `THIRD_PARTY_NOTICES.md`, shipped in the wheel and the sdist, with the copyright and MIT
  notice of alpha-hwr, which the BlueZ pairing agent is adapted from. The README said no code
  from the credited projects was used; that has been wrong since 0.1.1.

### Changed

- **Breaking:** `Measurements.shaft_power_w` is now `dc_power_w`. The value at motor-state
  offset 16 is probably the DC power the GO app shows, not shaft power, but that's not
  confirmed.
- **Breaking:** `Identity.family`, `type` and `version` are `None` when the pump reports 255
  ("not available"), and an identity like that matches no profile.
- **Breaking:** the AUTOADAPT modes have no entry in `USER_SETPOINTS`. The GO app picks them
  with the control mode alone.
- Every connection now waits 0.5 s after subscribing before its first write, through a proxy
  too. ESPHome found on an ALPHA3 that requests sent within 200 ms of subscribing got lost in
  about half of all connections (esphome/esphome#22651). 0.1.3 only paused on local adapters.
- A request that's cancelled or fails to write now closes the link, as a timed-out one already
  did. A frame only counts as the reply if its data classes match the request (and, for
  objects, the type too, and for the flow limiters the limiter id). Other frames are logged and
  the wait carries on; a `RequestTimeout` says how many there were.
- If subscribing fails, it's tried once more on the same link, and it's never treated as the
  pump refusing.
- `probe_capabilities` keeps going after an object that gets no answer: it notes it as
  unknown, reconnects and probes the rest, instead of raising.
- The BlueZ pairing agent is only registered when `pairing=True`, one at a time, under its own
  path per device. An ordinary poll no longer makes it the system's default agent.
- `classic.parse_get_reply` reads two bytes per id for classes 11 to 13. INFO scaling the
  library can't do (extended precision, units missing from the table) now raises
  `NotImplementedError` instead of returning a wrongly scaled value.
- The frame assembler caps LEN at the longest reply a one-APDU request can get (71 bytes), and
  a later complete frame beats a false start, so a stray byte can't hide the reply.
- The "connected and subscribed" debug line no longer shows the MTU (the largest packet the
  link allows). On a local BlueZ adapter, reading it made bleak warn `Using default MTU value`
  and report its default of 23, not the real value. The transport writes 20-byte pieces
  whatever the MTU is.
- The README's links are absolute, so they work on the PyPI page too, and the README has a
  safety note. The package summary says the release is read-only, and the project links include
  the protocol notes.
- `bluez_pairing_agent` is annotated as returning an `AsyncGenerator`, and bleak's BlueZ types
  are only imported on Linux, so pyright and ty check the package cleanly on macOS and Windows.

### Removed

- The upper limits on `bleak` (`<4`) and `bleak-retry-connector` (`<5`). Home Assistant installs
  its own versions, and a limit would stop the integration loading when Home Assistant moves to
  a new major version. If bleak ever drops the private bus the pairing agent uses, pairing
  through a local BlueZ adapter goes ahead without the agent instead of failing.

### Fixed

- Head used 9806.65 Pa/m (g × 1000), but the pump's own factor is 9804: its frames carry both
  units, and its factory presets are whole multiples of it. Heads read about 0.03 % low, so the
  CP III preset showed as 7.498 m.
- Object status 0x81 no longer counts as "no such object". It came from reading an APDU header
  as an object status.
- Infinite floats and missing enum bytes are no longer turned into values or into zero.
- Installing the declared dependencies now gives you a library that works.
  `bleak-retry-connector` 4.0 to 4.6 leaves out `bluetooth-adapters` on Python 3.14 but still
  imports it, so the minimum is now 4.7.1 (and `bleak` 3.0.2, which it needs). `dbus-fast`,
  which the BlueZ pairing agent imports, is now a declared dependency on Linux, at 4.0 or later;
  older versions don't have `dbus_fast.annotations`.
- `BleTransport.request(..., timeout=0)` waited for the transport's default timeout instead of
  timing out straight away.

## [0.1.3] - 2026-11-15

### Changed

- On a local BlueZ adapter, the transport now waits for BlueZ to finish discovering the pump's
  services before subscribing, instead of using bleak's own cache. With the cache, the
  connection was handed over before bluetoothd had finished setting up GATT (the list of what
  the pump offers over Bluetooth). Proxies still use their cache.
- On a local BlueZ adapter, the transport waits 0.5 s after subscribing before its first write.
  Home Assistant re-tunes the link just as the connection completes, and a first request sent
  in the middle of that went unanswered in most connections. Proxies don't need the pause.
- A `RequestTimeout` now says what came back for the request: nothing, or the pieces of a
  reply that never finished. That tells a silent pump apart from lost notifications.
- Requests wait up to 10 s for a reply instead of 8 s. The link supervision timeout (how long
  the link waits before it gives up on a quiet partner) on Home Assistant and ESPHome proxies
  is 8 s, not the ~6 s we'd assumed, so 8 s didn't stay above it.

## [0.1.2] - 2026-11-14

### Added

- `BleTransport(device_callback=...)` asks for the device again before every connection
  attempt, so a retry can go through whichever adapter or proxy hears the pump best right then.

### Changed

- Requests wait up to 8 s for a reply instead of 2 s, longer than the link supervision timeout.
  A link that stalls and recovers now answers late instead of being dropped, and a dead link
  shows up as `ConnectionLost`.
- When the pump's characteristic is missing from the cached services, the cache is cleared, so
  the next connection reads them fresh.
- A link that connects but never answers is no longer described as a sign of a lost bond:
  flaky adapters do that with a perfectly good bond.

## [0.1.1] - 2026-11-11

### Added

- `remove_bluez_bond` makes BlueZ forget a bond, so a pump that has lost its side of it can be
  paired again through a local adapter. Before this, BlueZ kept reconnecting with the old keys.

### Fixed

- Pairing through a local BlueZ adapter failed with `org.bluez.Error.AuthenticationFailed`:
  BlueZ turns down the pump's Just Works (no code to type) request when no pairing agent is
  registered. `BleTransport` now registers a `NoInputNoOutput` agent while it pairs a device
  BlueZ has no bond for, and BlueZ's `AuthenticationFailed` raises `PairingRequired`.

## [0.1.0] - 2026-11-09

### Added

- GENIbus framing: telegrams, APDUs, CRC-16/CCITT, putting notification-sized pieces back
  together, classic classes 2/3/4/5/7 (GET, SET, INFO, strings, the Grundfos unit table) and
  class-10 data objects (GET, SET, headers, float and integer fields with the "not available"
  and NaN markers).
- An advertisement parser for the 0xFE5D service data, including the Bluetooth-button flag
  that marks the pairing window.
- An ALPHA2 GO profile: process values, motor state, operation state, setpoints and factory
  limits per mode, panel setting and presets, flow limiters, AutoNight, the
  proportional-pressure curve, alarms and warnings, statistics, temperatures and energy. Each
  entry says how it was checked and where it comes from.
- `BleTransport` on bleak 3 and bleak-retry-connector 4: pairs on every connection, writes in
  pieces, one request at a time. `PairingRequired` means the pump turned pairing down (SMP
  reason 82), and `reply_seen` tells a silent link from one that answered. Only tested through
  ESPHome proxies.
- `GrundfosPump`: typed reads and capability probing, with the raw frames kept. This release
  only reads: the write methods raise `NotSupported`, and no write has ever been sent to a real
  pump.
- `grundfos_ble.testing`: a replay transport, a file format for recorded frames, and the
  recorded ALPHA2 GO frames as `FIXTURES`.

[Unreleased]: https://github.com/83noit/grundfos-ble/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/83noit/grundfos-ble/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/83noit/grundfos-ble/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/83noit/grundfos-ble/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/83noit/grundfos-ble/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/83noit/grundfos-ble/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/83noit/grundfos-ble/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/83noit/grundfos-ble/releases/tag/v0.1.0
