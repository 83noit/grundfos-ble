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
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bleak import BleakScanner
from bleak.exc import BleakError

from grundfos_ble.advertisement import GRUNDFOS_SERVICE_UUID, Advertisement, parse_advertisement
from grundfos_ble.exceptions import GrundfosError, NotSupported
from grundfos_ble.genibus import objects
from grundfos_ble.genibus.objects import ObjectRef
from grundfos_ble.genibus.telegram import Telegram
from grundfos_ble.pump import GrundfosPump, Identity
from grundfos_ble.registry import alpha2go
from grundfos_ble.registry.alpha2go import StringId
from grundfos_ble.transport import BleTransport, Transport

if TYPE_CHECKING:
    from collections import deque

    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData

DESCRIPTION = "Find a Grundfos pump, pair with it, read it and record what it says (read-only)."
SCAN_SECONDS = 8.0
BUTTON_WAIT_SECONDS = 60.0
WATCH_SECONDS = 60.0
EXIT_INTERRUPTED = 130

PRIVATE_STRINGS = frozenset({StringId.SERIAL_NUMBER, StringId.PRODUCTION_CODE})
"""Strings left out unless ``--include-serial`` is given, so output and fixtures can be shared."""
IDENTITY_STRINGS = (
    StringId.PRODUCT_NAME,
    StringId.PRODUCT_NUMBER,
    StringId.SERIAL_NUMBER,
    StringId.PRODUCTION_CODE,
    StringId.UNIT_NAME,
    StringId.MODEL,
    StringId.SOFTWARE_VERSION,
    StringId.BLE_SOFTWARE_VERSION,
)
BOND_WARNING = (
    "Pairing makes a new bond on the pump. It has held at least two at once (a phone and a "
    "proxy), but its maximum is not documented and it offers no way to forget one, so pair "
    "only computers you will keep using."
)


# --- Recording what goes over the link ----------------------------------------------------------


@dataclass(slots=True)
class Timing:
    """One request: when it was sent, how long its answer took, and whether one came."""

    label: str
    request: bytes
    seconds: float
    answered: bool


@dataclass(slots=True)
class Recorder:
    """A :class:`~grundfos_ble.transport.Transport` that times requests and keeps exchanges.

    Wraps the real transport. ``label`` names what is being read, for the timing report and the
    fixture's comments. It never writes: :meth:`send` refuses.
    """

    inner: Transport
    label: str = ""
    timings: list[Timing] = field(default_factory=list)
    exchanges: list[tuple[str, bytes, bytes]] = field(default_factory=list)
    last_frames: deque[tuple[str, bytes]] = field(init=False)
    """The wrapped transport's recent traffic (the same deque)."""

    def __post_init__(self) -> None:
        self.last_frames = self.inner.last_frames

    @property
    def is_connected(self) -> bool:
        """True while the wrapped transport's link is up."""
        return self.inner.is_connected

    @property
    def reply_seen(self) -> bool:
        """True once the pump has answered on the wrapped transport."""
        return self.inner.reply_seen

    async def connect(self) -> None:
        """Connect the wrapped transport."""
        await self.inner.connect()

    async def disconnect(self) -> None:
        """Disconnect the wrapped transport."""
        await self.inner.disconnect()

    async def request(
        self,
        telegram: Telegram,
        *,
        timeout: float | None = None,
        expect: Callable[[Telegram], bool] | None = None,
    ) -> Telegram:
        """Send a request through the wrapped transport, timing it and keeping the exchange."""
        frame = telegram.encode()
        started = time.perf_counter()
        answered = False
        try:
            reply = await self.inner.request(telegram, timeout=timeout, expect=expect)
            answered = True
        finally:
            self.timings.append(Timing(self.label, frame, time.perf_counter() - started, answered))
        if reply.raw is not None:
            self.exchanges.append((self.label, frame, reply.raw))
        return reply

    async def send(self, telegram: Telegram) -> None:
        """Refuse: the command line never writes to the pump."""
        del telegram
        raise NotSupported("the command line never writes to the pump")


# --- Output helpers -----------------------------------------------------------------------------


def _plain(value: Any) -> Any:  # noqa: ANN401 - any decoded value
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _plain(v) for k, v in asdict(value).items()}
    if isinstance(value, bytes):
        return value.hex(" ")
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | frozenset | set):
        return [_plain(v) for v in value]
    return value


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


async def _identity(pump: GrundfosPump, recorder: Recorder, *, include_serial: bool) -> Identity:
    recorder.label = "identity 2/148-150"
    identity = await pump.read_identity(strings=False)
    strings: dict[str, str | None] = {}
    for string_id in IDENTITY_STRINGS:
        if string_id in PRIVATE_STRINGS and not include_serial:
            continue
        recorder.label = f"string {int(string_id)} ({string_id.name.lower()})"
        strings[string_id.name.lower()] = await pump.read_string(string_id)
    return Identity(
        family=identity.family,
        type=identity.type,
        version=identity.version,
        product_name=strings.get("product_name"),
        product_number=strings.get("product_number"),
        serial_number=strings.get("serial_number"),
        production_code=strings.get("production_code"),
        unit_name=strings.get("unit_name"),
        model=strings.get("model"),
        software_version=strings.get("software_version"),
        ble_software_version=strings.get("ble_software_version"),
    )


async def _read_all(pump: GrundfosPump, recorder: Recorder) -> dict[str, Any]:
    """Read the measurements and every optional object; record why any could not be read."""
    result: dict[str, Any] = {}
    errors: dict[str, str] = {}
    steps: tuple[tuple[str, Callable[[], Awaitable[Any]]], ...] = (
        ("measurements", pump.read_measurements),
        ("operation", pump.read_operation),
        ("faults", pump.read_faults),
        ("autonight", pump.read_autonight),
        ("flow_limiters", pump.read_flow_limiters),
        ("statistics", pump.read_statistics),
        ("temperatures", pump.read_temperatures),
        ("energy_kwh", pump.read_energy_kwh),
        ("panel_setting", pump.read_panel_setting),
    )
    for key, read in steps:
        recorder.label = key
        try:
            result[key] = _plain(await read())
        except GrundfosError as err:
            errors[key] = f"{type(err).__name__}: {err}"
            if not pump.transport.is_connected:
                break  # the link is gone; what was read so far still stands
    if errors:
        result["errors"] = errors
    return result


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
        identity = await _identity(pump, recorder, include_serial=False)
    print(f"Paired in {time.monotonic() - started:.1f} s: {identity.model or '-'}")
    if identity.profile is None:
        print(
            f"Not supported yet: family {identity.family}, type {identity.type}.", file=sys.stderr
        )
    return 0


async def _info(args: argparse.Namespace) -> int:
    async with _connected(args) as (pump, recorder):
        identity = await _identity(pump, recorder, include_serial=args.include_serial)
    for key, value in asdict(identity).items():
        if value is not None or key not in ("serial_number", "production_code"):
            print(f"{key + ':':<22}{value if value is not None else '-'}")
    profile = identity.profile
    print(f"{'profile:':<22}{profile.name if profile else 'not supported yet'}")
    return 0


async def _read(args: argparse.Namespace) -> int:
    async with _connected(args) as (pump, recorder):
        result = await _read_all(pump, recorder)
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
            result = await _read_all(GrundfosPump(recorder), recorder)
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
        identity = await _identity(pump, recorder, include_serial=args.include_serial)
        await _read_all(pump, recorder)
        recorder.label = "probe"
        await pump.probe_capabilities()
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        (
            f"# Recorded from {identity.model or 'a Grundfos pump'} on {stamp} with the "
            "grundfos-ble command line, through a local adapter."
        ),
        "# One whole frame per '<' line.",
    ]
    if args.note:
        lines.append(f"# {args.note}")
    for label, request, reply in recorder.exchanges:
        lines += ["", f"# {label}", f"> {request.hex(' ')}", f"< {reply.hex(' ')}"]
    await asyncio.to_thread(path.write_text, "\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(recorder.exchanges)} exchanges to {path}")
    return 0


# --- Entry point --------------------------------------------------------------------------------


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
    add("info", _info, "read the identity").add_argument(
        "--include-serial", action="store_true", help="also read the serial and production code"
    )
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
    capture.add_argument(
        "--include-serial", action="store_true", help="also record the serial and production code"
    )
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
