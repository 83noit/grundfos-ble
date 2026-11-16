# Security policy

## Supported versions

Only the latest release on [PyPI](https://pypi.org/project/grundfos-ble/) gets fixes.

## Reporting a vulnerability

Please tell me privately through GitHub:
[Report a vulnerability](https://github.com/83noit/grundfos-ble/security/advisories/new).
Please don't open a public issue. You should hear back within a week.

This library talks to a heating pump over Bluetooth, so on top of the usual kinds of security
bug, these count too:

- anything that makes the library write to a pump when you didn't ask it to, or gets around
  `allow_writes`
- anything that leaks Bluetooth bond keys, or lets another device take over a pairing
- a dependency problem that makes an installed release run code it shouldn't

Wrong values, connection failures and pumps that aren't supported are ordinary bugs:
[open an issue here](https://github.com/83noit/grundfos-ble/issues).
