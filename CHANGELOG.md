# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- The "connected and subscribed" debug line no longer reports the MTU. On a local BlueZ adapter
  reading it made bleak warn `Using default MTU value` and report its default of 23, not the
  negotiated value. The transport writes 20-byte pieces whatever the MTU.
- The README's links are absolute, so they also work on the PyPI page, and the README carries a
  safety statement. The package summary says the release is read-only, and the project links
  include the protocol documentation.

### Added

- `BleTransport` is an async context manager: `async with` connects and then disconnects.
- `THIRD_PARTY_NOTICES.md`, shipped in the wheel and the sdist, with the copyright and MIT
  notice of alpha-hwr, from which the BlueZ pairing agent is adapted. The README said no code
  from the acknowledged projects was used; that has not been true since 0.1.1.

### Removed

- The upper caps on `bleak` (`<4`) and `bleak-retry-connector` (`<5`). Home Assistant installs
  its own pins, and a cap would stop the integration loading when it moves to a new major. If
  bleak drops the private bus the pairing agent uses, pairing through a local BlueZ adapter
  goes ahead without the agent instead of failing.

### Fixed

- The declared dependencies now install a working library. `bleak-retry-connector` 4.0 to 4.6
  leaves out `bluetooth-adapters` on Python 3.14 but still imports it, so the floor is now
  4.7.1 (and `bleak` 3.0.2, which it needs). `dbus-fast`, which the BlueZ pairing agent imports,
  is now declared on Linux at 4.0 or later; older versions lack `dbus_fast.annotations`.
- `BleTransport.request(..., timeout=0)` waited for the transport's default
  timeout instead of timing out at once.

## [0.1.3] - 2026-11-15

### Changed

- On a local BlueZ adapter the transport waits for BlueZ to resolve the pump's services before
  subscribing, instead of using bleak's own cache. With the cache, a connection was handed over
  before bluetoothd had finished setting up GATT. Proxies keep using their cache.
- On a local BlueZ adapter the transport waits 0.5 s after subscribing before its first write.
  Home Assistant re-tunes such a link as the connection completes, and a first request written
  into that went unanswered in most connections. Proxies need no pause.
- A `RequestTimeout` now says what arrived for the request: nothing, or the notification pieces
  of a reply that never completed. This tells a silent pump apart from lost notifications.
- Requests wait up to 10 s for a reply instead of 8 s. The link supervision timeout on Home
  Assistant and ESPHome proxies is 8 s, not the ~6 s assumed before, so 8 s did not stay above it.

## [0.1.2] - 2026-11-14

### Added

- `BleTransport(device_callback=...)` asks for the device again before every connection attempt,
  so a retry can go through whichever adapter or proxy now hears the pump best.

### Changed

- Requests wait up to 8 s for a reply instead of 2 s, above the link supervision timeout. A link
  that stalls and recovers now answers late instead of being dropped, and a dead link reads as
  `ConnectionLost`.
- When the pump's characteristic is missing from the cached services, the cache is cleared so
  that the next connection reads them afresh.
- A link that connects but never answers is no longer described as a sign of a lost bond:
  unreliable adapters do that with a sound bond.

## [0.1.1] - 2026-11-11

### Added

- `remove_bluez_bond` makes BlueZ forget a bond, so a pump that has lost its half can be paired
  again through a local adapter; until now BlueZ kept reconnecting with the old keys.

### Fixed

- Pairing through a local BlueZ adapter failed with `org.bluez.Error.AuthenticationFailed`:
  BlueZ refuses the pump's Just Works request when no pairing agent is registered. `BleTransport`
  now registers a `NoInputNoOutput` agent while it pairs a device BlueZ holds no bond for, and
  BlueZ's `AuthenticationFailed` raises `PairingRequired`.

## [0.1.0] - 2026-11-09

### Added

- GENIbus framing: telegrams, APDUs, CRC-16/CCITT, reassembly of notification-sized pieces,
  classic classes 2/3/4/5/7 (GET, SET, INFO, strings, the Grundfos unit table) and class-10
  data objects (GET, SET, headers, float/integer fields with the "not available" and NaN
  sentinels).
- Advertisement parser for the 0xFE5D service data, including the Bluetooth-button flag that
  marks the pairing window.
- ALPHA2 GO profile: process values, motor state, operation state, per-mode setpoints and
  factory limits, panel setting and presets, flow limiters, AutoNight, proportional-pressure
  curve, alarms and warnings, statistics, temperatures and energy, each entry tagged with how
  it was verified and where it comes from.
- `BleTransport` on bleak 3 and bleak-retry-connector 4: pairs on every connection, chunked
  writes, one request in flight. `PairingRequired` means the pump refused pairing (SMP reason 82),
  and `reply_seen` tells a silent link from an answered one. Only ESPHome proxies have been tested.
- `GrundfosPump`: typed reads and capability probing with raw-frame capture. This is a read-only
  release: the write methods raise `NotSupported`, and no write has ever been sent to a real pump.
- `grundfos_ble.testing`: a replay transport, a fixture format for recorded frames, and the
  recorded ALPHA2 GO frames as `FIXTURES`.

[Unreleased]: https://github.com/83noit/grundfos-ble/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/83noit/grundfos-ble/releases/tag/v0.1.3
[0.1.2]: https://github.com/83noit/grundfos-ble/releases/tag/v0.1.2
[0.1.1]: https://github.com/83noit/grundfos-ble/releases/tag/v0.1.1
[0.1.0]: https://github.com/83noit/grundfos-ble/releases/tag/v0.1.0
