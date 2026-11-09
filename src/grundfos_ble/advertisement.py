"""Parsing of the Bluetooth advertisement Grundfos pumps send under service UUID 0xFE5D.

Layout (measured on an ALPHA2 GO, flag bits per the SCALA1 community documentation):

    03 01 00  family  type  version  mode  flags  address-tail(2)

``family``/``type`` equal the pump's GENIbus ``unit_family``/``unit_type`` (class 2 IDs 148/149).
The flag byte is the interesting part: bit 0x10 is set for about four seconds after the
pump's Bluetooth button is pressed, which is the only time it accepts pairing.
"""

from __future__ import annotations

from dataclasses import dataclass

GRUNDFOS_SERVICE_UUID_16 = 0xFE5D
"""Bluetooth SIG member UUID assigned to Grundfos A/S."""
GRUNDFOS_SERVICE_UUID = "0000fe5d-0000-1000-8000-00805f9b34fb"
"""The same, as a 128-bit UUID string (the key HA uses in ``service_data``)."""

GENI_CHARACTERISTIC_UUID = "859cffd1-036e-432a-aa28-1a0085b87ba9"
"""The single characteristic carrying GENIbus requests (write) and replies (notify)."""

ADVERTISEMENT_LENGTH = 10

FLAG_CLIENT_CONNECTED = 0x01
FLAG_ALARM = 0x02
FLAG_WARNING = 0x04
FLAG_LED_BLINKING = 0x08
FLAG_BUTTON_PRESSED = 0x10


@dataclass(frozen=True, slots=True)
class Advertisement:
    """Decoded 0xFE5D service data."""

    family: int
    type: int
    version: int
    mode: int
    flags: int
    address_tail: bytes
    raw: bytes

    @property
    def button_pressed(self) -> bool:
        """The Bluetooth button was pressed within the last few seconds (pairing window)."""
        return bool(self.flags & FLAG_BUTTON_PRESSED)

    @property
    def client_connected(self) -> bool:
        """A client holds the GATT connection (unverified on the ALPHA2 GO)."""
        return bool(self.flags & FLAG_CLIENT_CONNECTED)

    @property
    def alarm(self) -> bool:
        """An alarm is active (unverified on the ALPHA2 GO)."""
        return bool(self.flags & FLAG_ALARM)

    @property
    def warning(self) -> bool:
        """A warning is active (unverified on the ALPHA2 GO)."""
        return bool(self.flags & FLAG_WARNING)

    @property
    def led_blinking(self) -> bool:
        """The Bluetooth LED is blinking (identify / pairing request; unverified)."""
        return bool(self.flags & FLAG_LED_BLINKING)


def parse_advertisement(service_data: bytes | bytearray | memoryview) -> Advertisement | None:
    """Decode the 0xFE5D service data; None when it is not the expected length."""
    raw = bytes(service_data)
    if len(raw) != ADVERTISEMENT_LENGTH:
        return None
    return Advertisement(
        family=raw[3],
        type=raw[4],
        version=raw[5],
        mode=raw[6],
        flags=raw[7],
        address_tail=raw[8:10],
        raw=raw,
    )
