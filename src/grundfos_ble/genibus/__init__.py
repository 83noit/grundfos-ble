"""GENIbus framing, transport-agnostic.

The layer knows telegrams, APDUs, the CRC, the classic data classes (2/3/4/5/7) and the
class-10 data-object format used by Grundfos' Bluetooth pumps. It knows nothing about BLE,
so an RS-485 transport could reuse it unchanged.
"""

from grundfos_ble.genibus.apdu import Ack, Apdu, DataClass, Operation
from grundfos_ble.genibus.crc import crc16, verify_crc
from grundfos_ble.genibus.telegram import (
    BLE_MASTER_ADDRESS,
    BLE_PUMP_ADDRESS,
    SD_ERROR,
    SD_MESSAGE,
    SD_REPLY,
    SD_REQUEST,
    FrameAssembler,
    Telegram,
    frame_length,
)

__all__ = [
    "BLE_MASTER_ADDRESS",
    "BLE_PUMP_ADDRESS",
    "SD_ERROR",
    "SD_MESSAGE",
    "SD_REPLY",
    "SD_REQUEST",
    "Ack",
    "Apdu",
    "DataClass",
    "FrameAssembler",
    "Operation",
    "Telegram",
    "crc16",
    "frame_length",
    "verify_crc",
]
