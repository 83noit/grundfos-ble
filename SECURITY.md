# Security policy

## Supported versions

Only the latest release on [PyPI](https://pypi.org/project/grundfos-ble/) gets fixes.

## Reporting a vulnerability

Please report privately through GitHub:
[Report a vulnerability](https://github.com/83noit/grundfos-ble/security/advisories/new).
Do not open a public issue. You should get an answer within a week.

This library talks to a heating circulator pump over Bluetooth, so these count as security
issues, along with the usual ones:

- anything that makes the library write to a pump when the caller did not ask it to, or
  bypasses `allow_writes`
- anything that exposes Bluetooth bond material or lets another device take over a pairing
- a dependency problem that makes an installed release run code it should not

Bugs in decoded values, connection failures and unsupported pumps are ordinary issues:
[open one here](https://github.com/83noit/grundfos-ble/issues).
