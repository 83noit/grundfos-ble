"""A temporary BlueZ pairing agent, so a local Linux adapter can bond with the pump.

The pump asks for Just Works pairing itself, with a security request as soon as the link is
up. BlueZ hands that request to the default ``org.bluez.Agent1`` for confirmation, and with no
agent registered (Home Assistant registers none) it refuses, so bleak's ``Pair`` fails with
``org.bluez.Error.AuthenticationFailed``. ``bluetoothctl`` registers an agent on startup, which
is why pairing from it works. While the pump is being paired, this module registers a
``NoInputNoOutput`` agent as the default, accepts requests for that pump and rejects the rest.
It can also remove BlueZ's half of a bond the pump no longer honours, so pairing runs again.

Adapted from alpha-hwr's ``core/pairing_agent.py`` (MIT, https://github.com/eman/alpha-hwr).

This module does not use ``from __future__ import annotations``: dbus_fast's ``@method()``
reads the D-Bus signature from the annotations when the class is defined.
"""

import contextlib
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from bleak.backends.bluezdbus.manager import BlueZManager
    from bleak.backends.device import BLEDevice
    from dbus_fast.aio import MessageBus

_LOGGER = logging.getLogger(__name__)

AGENT_PATH: Final = "/org/grundfos_ble/agent"
BLUEZ_SERVICE: Final = "org.bluez"
AGENT_MANAGER_INTERFACE: Final = "org.bluez.AgentManager1"
ADAPTER_INTERFACE: Final = "org.bluez.Adapter1"


def bluez_device_path(device: "BLEDevice") -> str | None:
    """Return the BlueZ object path of a device seen by a local adapter, else None."""
    details = device.details
    if isinstance(details, dict) and isinstance(path := details.get("path"), str):
        return path
    return None


async def _bluez_bus() -> "tuple[BlueZManager, MessageBus] | None":
    """Return bleak's BlueZ manager and its system bus, or None where there is no BlueZ."""
    try:
        from bleak.backends.bluezdbus.manager import get_global_bluez_manager

        manager = await get_global_bluez_manager()
    except Exception as err:  # no BlueZ here
        _LOGGER.debug("BlueZ unavailable: %s", err)
        return None
    bus = manager._bus  # noqa: SLF001 - bleak's own system bus; BlueZ ties agents to it
    return None if bus is None else (manager, bus)


async def _call(
    bus: "MessageBus", path: str, interface: str, member: str, signature: str, body: list[object]
) -> None:
    from dbus_fast import Message, MessageType
    from dbus_fast.errors import DBusError

    reply = await bus.call(
        Message(
            destination=BLUEZ_SERVICE,
            path=path,
            interface=interface,
            member=member,
            signature=signature,
            body=body,
        )
    )
    if reply is not None and reply.message_type == MessageType.ERROR:
        raise DBusError(reply.error_name or "unknown", str(reply.body))


async def remove_bluez_bond(device: "BLEDevice") -> bool:
    """Make BlueZ forget its bond with ``device``, so the next connection pairs afresh.

    A pump that has lost its half of a bond refuses the old keys, and while BlueZ still holds
    them bleak only reconnects and never pairs, so pressing the button cannot help. Call this
    when a new bond is wanted anyway, before waiting for the button: BlueZ drops the device
    object with the bond and recreates it from the next advertisement. Returns True when a
    bond was removed; does nothing for a device not seen by a local BlueZ adapter.
    """
    path = bluez_device_path(device)
    if path is None or (bluez := await _bluez_bus()) is None:
        return False
    manager, bus = bluez
    if not manager.is_paired(path):
        return False
    try:
        await _call(bus, path.rsplit("/", 1)[0], ADAPTER_INTERFACE, "RemoveDevice", "o", [path])
    except Exception as err:  # pairing will report what is left
        _LOGGER.debug("Could not remove the BlueZ bond for %s: %s", path, err)
        return False
    _LOGGER.debug("Removed the BlueZ bond for %s", path)
    return True


@contextlib.asynccontextmanager
async def bluez_pairing_agent(device: "BLEDevice") -> AsyncIterator[None]:
    """Hold a Just Works agent while ``device`` is connected and paired through BlueZ.

    Does nothing when the device was not seen by a local BlueZ adapter (an ESPHome proxy, or
    another platform), when BlueZ already holds a bond, or when the agent cannot be
    registered; pairing then goes ahead as it would have without one.
    """
    path = bluez_device_path(device)
    bluez = None if path is None else await _bluez_bus()
    if path is None or bluez is None or bluez[0].is_paired(path):
        yield
        return
    bus = bluez[1]
    from dbus_fast.annotations import DBusObjectPath, DBusStr, DBusUInt32
    from dbus_fast.errors import DBusError
    from dbus_fast.service import ServiceInterface, method

    def check(requested: str) -> None:
        if requested != path:
            raise DBusError("org.bluez.Error.Rejected", "not the device being paired")

    class Agent(ServiceInterface):
        """Accept Just Works pairing for the one device being paired."""

        def __init__(self) -> None:
            super().__init__("org.bluez.Agent1")

        @method()
        def Release(self) -> None:  # noqa: N802 - D-Bus method name
            """Unregistered by BlueZ."""

        @method()
        def Cancel(self) -> None:  # noqa: N802
            """Drop a cancelled request."""

        @method()
        def RequestConfirmation(self, device: DBusObjectPath, passkey: DBusUInt32) -> None:  # noqa: N802, ARG002
            """Confirm Just Works pairing."""
            check(device)

        @method()
        def RequestAuthorization(self, device: DBusObjectPath) -> None:  # noqa: N802
            """Authorize pairing the remote side started."""
            check(device)

        @method()
        def AuthorizeService(self, device: DBusObjectPath, uuid: DBusStr) -> None:  # noqa: N802, ARG002
            """Authorize a service on the paired device."""
            check(device)

    async def call(member: str, signature: str, body: list[object]) -> None:
        await _call(bus, "/org/bluez", AGENT_MANAGER_INTERFACE, member, signature, body)

    exported = registered = False
    try:
        bus.export(AGENT_PATH, Agent())
        exported = True
        await call("RegisterAgent", "os", [AGENT_PATH, "NoInputNoOutput"])
        registered = True
        await call("RequestDefaultAgent", "o", [AGENT_PATH])
    except Exception as err:  # try the pairing anyway
        _LOGGER.debug("Could not register the BlueZ pairing agent: %s", err)
    else:
        _LOGGER.debug("BlueZ pairing agent registered for %s", path)
    try:
        yield
    finally:
        if registered:
            with contextlib.suppress(Exception):
                await call("UnregisterAgent", "o", [AGENT_PATH])
        if exported:
            bus.unexport(AGENT_PATH)
