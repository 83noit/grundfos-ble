"""The 0xFE5D advertisement as measured on a real pump, with the address bytes replaced."""

from grundfos_ble.advertisement import GRUNDFOS_SERVICE_UUID, parse_advertisement

IDLE = bytes.fromhex("0301003406063000abcd")
BUTTON = bytes.fromhex("0301003406063010abcd")


def test_uuid_constant() -> None:
    assert GRUNDFOS_SERVICE_UUID == "0000fe5d-0000-1000-8000-00805f9b34fb"


def test_parse_idle_and_button() -> None:
    idle = parse_advertisement(IDLE)
    assert idle is not None
    assert (idle.family, idle.type, idle.version, idle.mode) == (52, 6, 6, 0x30)
    assert idle.address_tail == b"\xab\xcd"
    assert not idle.button_pressed
    assert not idle.alarm
    assert not idle.warning
    assert not idle.client_connected
    assert not idle.led_blinking
    pressed = parse_advertisement(BUTTON)
    assert pressed is not None
    assert pressed.button_pressed
    assert pressed.raw == BUTTON


def test_flag_bits() -> None:
    adv = parse_advertisement(bytes.fromhex("0301003406063007abcd"))
    assert adv is not None
    assert adv.client_connected
    assert adv.alarm
    assert adv.warning
    assert not adv.led_blinking


def test_wrong_length_is_rejected() -> None:
    assert parse_advertisement(IDLE[:9]) is None
    assert parse_advertisement(IDLE + b"\x00") is None
