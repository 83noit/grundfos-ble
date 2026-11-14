# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[0.1.1]: https://github.com/83noit/grundfos-ble/releases/tag/v0.1.1
[0.1.0]: https://github.com/83noit/grundfos-ble/releases/tag/v0.1.0
