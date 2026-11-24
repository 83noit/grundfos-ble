"""Command line: find a pump, pair with it, read it, and record what it says.

Every command is read-only. There is no command that writes to the pump and none that sends
arbitrary bytes: ``get`` only builds class-10 GET requests. The pump must already be bonded
with this computer's Bluetooth adapter, or be paired with ``pair``, which needs a press of the
pump's Bluetooth button.

The command line talks to the pump through a local adapter (BlueZ on Linux, CoreBluetooth on
macOS). It cannot use an ESPHome proxy, which only Home Assistant reaches.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bleak import BleakScanner
from bleak.exc import BleakError

from grundfos_ble.advertisement import GRUNDFOS_SERVICE_UUID, Advertisement, parse_advertisement
from grundfos_ble.capture import (
    Recorder,
    read_all,
    read_identity,
    record_capture,
)
from grundfos_ble.exceptions import GrundfosError
from grundfos_ble.genibus import objects
from grundfos_ble.genibus.objects import ObjectRef
from grundfos_ble.genibus.telegram import Telegram
from grundfos_ble.pump import GrundfosPump
from grundfos_ble.registry import alpha2go
from grundfos_ble.transport import BleTransport

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData

DESCRIPTION = "Find a Grundfos pump, pair with it, read it and record what it says (read-only)."
SCAN_SECONDS = 8.0
BUTTON_WAIT_SECONDS = 60.0
WATCH_SECONDS = 60.0
EXIT_INTERRUPTED = 130

BOND_WARNING = (
    "Pairing makes a new bond on the pump. It has held at least two at once (a phone and a "
    "proxy), but its maximum is not documented and it offers no way to forget one, so pair "
    "only computers you will keep using."
)


# --- Output helpers -----------------------------------------------------------------------------


def _flags(advertisement: Advertisement) -> str:
    names = ("button_pressed", "client_connected", "alarm", "warning", "led_blinking")
    return ",".join(name for name in names if getattr(advertisement, name)) or "-"


def _advert_line(device: BLEDevice, data: AdvertisementData, advertisement: Advertisement) -> str:
    profile = alpha2go.profile_for(advertisement.family, advertisement.type)
    model = profile.name if profile else "unsupported"
    return (
        f"{device.address}  {data.local_name or device.name or '-'}  {data.rssi} dBm  "
        f"family {advertisement.family} type {advertisement.type} ({model})  "
        f"flags {advertisement.flags:#04x} [{_flags(advertisement)}]"
    )


def _measurement_line(pump_data: dict[str, Any]) -> str:
    m = pump_data.get("measurements")
    if not isinstance(m, dict):
        return "no measurements"

    def fmt(key: str, unit: str, digits: int) -> str:
        value = m.get(key)
        return "-" if value is None else f"{value:.{digits}f} {unit}"

    return "  ".join(
        (
            f"flow {fmt('flow_m3h', 'm³/h', 2)}",
            f"head {fmt('head_m', 'm', 2)}",
            f"power {fmt('power_w', 'W', 1)}",
            f"speed {fmt('speed_rpm', 'rpm', 0)}",
        )
    )


# --- Reading ------------------------------------------------------------------------------------


def _parse_ref(text: str) -> ObjectRef:
    object_id, _, sub = text.partition("/")
    try:
        return ObjectRef(int(object_id), int(sub))
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"expected ID/SUB-ID such as 88/25, got {text!r}") from err


# --- Connecting ---------------------------------------------------------------------------------


async def _find(address: str) -> BLEDevice:
    device = await BleakScanner.find_device_by_address(address, timeout=SCAN_SECONDS)
    if device is None:
        raise GrundfosError(
            f"{address} was not seen; is the pump powered, in range, and not held by the GO app?"
        )
    return device


@contextlib.asynccontextmanager
async def _connected(
    args: argparse.Namespace, *, pairing: bool = False, device: BLEDevice | None = None
) -> AsyncIterator[tuple[GrundfosPump, Recorder]]:
    """Connect to the pump named by ``args.address``; yield it with its recorder."""
    transport = BleTransport(device or await _find(args.address), pairing=pairing)
    recorder = Recorder(transport)
    try:
        await recorder.connect()
        yield GrundfosPump(recorder), recorder
    finally:
        await recorder.disconnect()


# --- Commands -----------------------------------------------------------------------------------


async def _scan(args: argparse.Namespace) -> int:
    if args.watch:
        return await _scan_watch(args.seconds)
    found = await BleakScanner.discover(timeout=args.seconds, return_adv=True)
    seen = 0
    for device, data in found.values():
        advertisement = parse_advertisement(data.service_data.get(GRUNDFOS_SERVICE_UUID, b""))
        if advertisement is not None:
            print(_advert_line(device, data, advertisement))
            seen += 1
    if not seen:
        print("No Grundfos pump found.", file=sys.stderr)
        return 1
    return 0


def _scanner(callback: Callable[[BLEDevice, AdvertisementData], None]) -> BleakScanner:
    """Return a scanner that reports every advertisement; ``callback`` picks the pump's.

    No ``service_uuids`` filter: the pump sends 0xFE5D as service data only, not in its list of
    service UUIDs, and CoreBluetooth filters on that list, so with it macOS reports nothing.
    """
    return BleakScanner(detection_callback=callback)


async def _scan_watch(seconds: float) -> int:
    """Print every change in a pump's advertisement, with the time, for ``seconds``."""
    last: dict[str, bytes] = {}

    def seen(device: BLEDevice, data: AdvertisementData) -> None:
        raw = data.service_data.get(GRUNDFOS_SERVICE_UUID)
        advertisement = None if raw is None else parse_advertisement(raw)
        if advertisement is None or last.get(device.address) == advertisement.raw:
            return
        last[device.address] = advertisement.raw
        stamp = datetime.now().astimezone().strftime("%H:%M:%S")
        print(f"{stamp}  {_advert_line(device, data, advertisement)}", flush=True)

    async with _scanner(seen):
        await asyncio.sleep(seconds)
    if not last:
        print("No Grundfos pump heard.", file=sys.stderr)
        return 1
    return 0


async def _wait_for_button(address: str, seconds: float) -> BLEDevice:
    """Wait for an advertisement from ``address`` with the button flag set."""
    pressed: asyncio.Future[BLEDevice] = asyncio.get_running_loop().create_future()

    def seen(device: BLEDevice, data: AdvertisementData) -> None:
        if device.address.upper() != address.upper() or pressed.done():
            return
        advertisement = parse_advertisement(data.service_data.get(GRUNDFOS_SERVICE_UUID, b""))
        if advertisement is not None and advertisement.button_pressed:
            pressed.set_result(device)

    async with _scanner(seen):
        try:
            async with asyncio.timeout(seconds):
                return await pressed
        except TimeoutError:
            raise GrundfosError(f"no button press from {address} within {seconds:.0f} s") from None


async def _pair(args: argparse.Namespace) -> int:
    print(BOND_WARNING, file=sys.stderr)
    print("Press the pump's Bluetooth button now.", file=sys.stderr, flush=True)
    device = await _wait_for_button(args.address, args.seconds)
    started = time.monotonic()
    async with _connected(args, pairing=True, device=device) as (pump, recorder):
        identity = await read_identity(pump, recorder)
    print(f"Paired in {time.monotonic() - started:.1f} s: {identity.model or '-'}")
    if identity.profile is None:
        print(
            f"Not supported yet: family {identity.family}, type {identity.type}.", file=sys.stderr
        )
    return 0


async def _info(args: argparse.Namespace) -> int:
    async with _connected(args) as (pump, recorder):
        identity = await read_identity(pump, recorder, private=not args.include_private)
    for key, value in asdict(identity).items():
        if value is not None or key not in ("serial_number", "production_code", "unit_name"):
            print(f"{key + ':':<22}{value if value is not None else '-'}")
    profile = identity.profile
    print(f"{'profile:':<22}{profile.name if profile else 'not supported yet'}")
    return 0


async def _read(args: argparse.Namespace) -> int:
    async with _connected(args) as (pump, recorder):
        result = await read_all(pump, recorder)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for key, value in result.items():
            print(f"{key}: {value}")
    return 1 if "measurements" not in result else 0


async def _probe(args: argparse.Namespace) -> int:
    async with _connected(args) as (pump, recorder):
        recorder.label = "probe"
        probe = await pump.probe_capabilities()
    for spec in alpha2go.OPTIONAL_OBJECTS:
        raw = probe.frames.get(spec.key)
        version = _object_version(raw)
        verdict = (
            "supported"
            if spec.key in probe.supported
            else "denied"
            if spec.key in probe.denied
            else "unknown"
        )
        line = f"{spec.key:<20} {spec.ref!s:<8} {verdict:<9} version {version}"
        if spec.version is not None and version not in ("-", str(spec.version)):
            line += f" (library expects {spec.version})"
        print(line)
        if raw is not None and args.raw:
            print(f"    {raw.hex(' ')}")
        if spec.key in probe.errors:
            print(f"    {probe.errors[spec.key]}")
    return 0


def _object_version(raw: bytes | None) -> str:
    """Return the version byte of a class-10 reply's first object header, or '-'."""
    if raw is None:
        return "-"
    try:
        (reply, *_rest) = objects.parse_get_reply(Telegram.decode(raw).apdu, raw)
    except (GrundfosError, ValueError):
        return "-"
    return str(reply.header.version) if reply.ok else "-"


async def _get(args: argparse.Namespace) -> int:
    async with _connected(args) as (pump, recorder):
        recorder.label = f"get {args.ref}"
        reply = await pump.transport.request(Telegram.request(objects.get_request(args.ref)))
    raw = reply.raw or b""
    print(raw.hex(" "))
    try:
        for item in objects.parse_get_reply(reply.apdu, raw):
            header = item.header
            print(
                f"status {header.status} type {header.type} version {header.version} "
                f"size {header.size}: {item.data.hex(' ')}"
            )
    except GrundfosError as err:
        print(f"{type(err).__name__}: {err}")
    return 0


async def _watch(args: argparse.Namespace) -> int:
    """Poll as Home Assistant does: connect, read, disconnect, every ``interval`` seconds."""
    device = await _find(args.address)
    polls = 0
    while args.count is None or polls < args.count:
        if polls:
            await asyncio.sleep(args.interval)
        polls += 1
        stamp = datetime.now().astimezone().strftime("%H:%M:%S")
        transport = BleTransport(device)
        recorder = Recorder(transport)
        started = time.perf_counter()
        try:
            await recorder.connect()
            connected = time.perf_counter() - started
            result = await read_all(GrundfosPump(recorder), recorder)
        except GrundfosError as err:
            print(f"{stamp}  failed after {time.perf_counter() - started:.2f} s: {err}")
            continue
        finally:
            await recorder.disconnect()
        answered = [t.seconds for t in recorder.timings if t.answered]
        first = f"{answered[0]:.2f}" if answered else "-"
        spread = f"{min(answered):.2f}-{max(answered):.2f}" if answered else "-"
        lost = sum(not t.answered for t in recorder.timings)
        source = getattr(transport, "last_source", None) or "-"  # a test double may lack it
        print(
            f"{stamp}  via {source}  connect {connected:.2f} s  "
            f"first reply {first} s  {len(answered)} replies {spread} s  lost {lost}  "
            f"{_measurement_line(result)}",
            flush=True,
        )
        if args.verbose_timings:
            for timing in recorder.timings:
                state = f"{timing.seconds:.3f} s" if timing.answered else "no reply"
                print(f"    {timing.label:<24} {state}")
    return 0


async def _capture(args: argparse.Namespace) -> int:
    path = Path(args.file)
    async with _connected(args) as (pump, recorder):
        capture = await record_capture(pump, recorder, private=not args.include_private)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    header = (
        f"Recorded from {capture.identity.model or 'a Grundfos pump'} on {stamp} with the "
        "grundfos-ble command line, through a local adapter."
    )
    text = "\n".join(capture.fixture_lines(header, args.note)) + "\n"
    await asyncio.to_thread(path.write_text, text, encoding="utf-8")
    print(f"Wrote {len(capture.exchanges)} exchanges to {path}")
    return 0


# --- Entry point --------------------------------------------------------------------------------


def _private_flag(parser: argparse.ArgumentParser, verb: str) -> None:
    # --include-serial is the 0.4 name, kept working.
    parser.add_argument(
        "--include-private",
        "--include-serial",
        dest="include_private",
        action="store_true",
        help=f"also {verb} the serial number, production code and the pump's own name",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(prog="grundfos-ble", description=DESCRIPTION)
    parser.add_argument("-v", "--verbose", action="store_true", help="log every frame")
    commands = parser.add_subparsers(dest="command", required=True)

    def add(
        name: str, handler: Callable[[argparse.Namespace], Awaitable[int]], text: str
    ) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=text, description=text)
        sub.set_defaults(handler=handler)
        if handler is not _scan:
            sub.add_argument("address", help="Bluetooth address (a UUID on macOS)")
        return sub

    scan = add("scan", _scan, "find nearby pumps and decode their advertisements")
    scan.add_argument("--seconds", type=float, default=SCAN_SECONDS, help="how long to listen")
    scan.add_argument(
        "--watch", action="store_true", help="print every change of advertisement as it happens"
    )
    pair = add("pair", _pair, "wait for the button press, then pair and read the identity")
    pair.add_argument(
        "--seconds", type=float, default=BUTTON_WAIT_SECONDS, help="how long to wait for it"
    )
    _private_flag(add("info", _info, "read the identity"), "read")
    add("read", _read, "read the measurements and every optional object once").add_argument(
        "--json", action="store_true", help="print JSON"
    )
    add(
        "probe", _probe, "report which optional objects the pump answers, with versions"
    ).add_argument("--raw", action="store_true", help="also print each reply frame")
    get = add("get", _get, "GET one class-10 object, e.g. 88/25, and print the reply")
    get.add_argument("ref", type=_parse_ref, help="object as ID/SUB-ID")
    watch = add("watch", _watch, "poll as Home Assistant does and time every request")
    watch.add_argument(
        "--interval", type=float, default=WATCH_SECONDS, help="seconds between polls"
    )
    watch.add_argument("--count", type=int, help="stop after this many polls")
    watch.add_argument(
        "--verbose-timings", action="store_true", help="print the time of every request"
    )
    capture = add("capture", _capture, "read everything and write it as a test fixture")
    capture.add_argument("file", help="fixture file to write")
    capture.add_argument("--note", help="a comment line to add, e.g. the pump's display")
    _private_flag(capture, "record")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command line and return its exit status."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    if args.verbose:
        # Only this package: bleak's own debug output buries the frames.
        logging.getLogger("grundfos_ble").setLevel(logging.DEBUG)
    try:
        return int(asyncio.run(args.handler(args)))
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except (GrundfosError, BleakError) as err:
        print(f"{type(err).__name__}: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
