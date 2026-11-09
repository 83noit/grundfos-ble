# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

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

[0.1.0]: https://github.com/83noit/grundfos-ble/releases/tag/v0.1.0
