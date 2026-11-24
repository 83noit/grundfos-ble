"""Record a read-only session with a pump, for support requests and test fixtures.

A :class:`Recorder` wraps a transport and keeps every request with its reply. On top of it,
:func:`record_capture` reads the identity, every known object and the capability probe once,
and returns a :class:`Capture` that renders in the fixture format of :mod:`grundfos_ble.testing`.
The command line's ``capture`` and Home Assistant's diagnostics both use it, so a pump this
library does not support yet can still be described by the people who own one.

Nothing here writes to the pump: :meth:`Recorder.send` refuses.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import TYPE_CHECKING, Any

from grundfos_ble.exceptions import GrundfosError, NotSupported
from grundfos_ble.pump import GrundfosPump, Identity, ProbeResult
from grundfos_ble.registry.alpha2go import StringId

if TYPE_CHECKING:
    from collections import deque

    from grundfos_ble.genibus.telegram import Telegram
    from grundfos_ble.transport import Transport

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
"""The identity strings a capture reads, in order."""

PRIVATE_STRINGS = frozenset({StringId.SERIAL_NUMBER, StringId.PRODUCTION_CODE, StringId.UNIT_NAME})
"""Strings left out while ``private`` is True: the serial, the production code and the name the
owner gave the pump. That is the default, so a capture can be shared as it is."""


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

    Wraps the real transport. ``label`` names what is being read, for timing reports and the
    fixture's comments. While ``recording`` is False requests are timed but not kept, so a
    caller can read private values on the same link without them landing in a capture. It
    never writes: :meth:`send` refuses.
    """

    inner: Transport
    label: str = ""
    recording: bool = True
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
        if self.recording and reply.raw is not None:
            self.exchanges.append((self.label, frame, reply.raw))
        return reply

    async def send(self, telegram: Telegram) -> None:
        """Refuse: a recorder never writes to the pump."""
        del telegram
        raise NotSupported("a recorder never writes to the pump")


def plain(value: Any) -> Any:  # noqa: ANN401 - any decoded value
    """Turn decoded values (dataclasses, bytes, sets) into JSON-friendly ones."""
    if is_dataclass(value) and not isinstance(value, type):
        return {k: plain(v) for k, v in asdict(value).items()}
    if isinstance(value, bytes):
        return value.hex(" ")
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | frozenset | set):
        return [plain(v) for v in value]
    return value


async def read_identity(
    pump: GrundfosPump, recorder: Recorder, *, private: bool = True
) -> Identity:
    """Read the identity and its strings one by one; ``private`` leaves out PRIVATE_STRINGS."""
    recorder.label = "identity 2/148-150"
    identity = await pump.read_identity(strings=False)
    strings: dict[str, str | None] = {}
    for string_id in IDENTITY_STRINGS:
        if private and string_id in PRIVATE_STRINGS:
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


async def read_all(pump: GrundfosPump, recorder: Recorder) -> dict[str, Any]:
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
            result[key] = plain(await read())
        except GrundfosError as err:
            errors[key] = f"{type(err).__name__}: {err}"
            if not pump.transport.is_connected:
                break  # the link is gone; what was read so far still stands
    if errors:
        result["errors"] = errors
    return result


@dataclass(frozen=True, slots=True)
class Capture:
    """One recorded session: what was decoded, and every frame that produced it."""

    identity: Identity
    readings: dict[str, Any]
    probe: ProbeResult | None
    exchanges: tuple[tuple[str, bytes, bytes], ...]

    def fixture_lines(self, header: str, note: str | None = None) -> list[str]:
        """Render the exchanges in the fixture format, ``header`` and ``note`` as comments."""
        lines = [f"# {header}", "# One whole frame per '<' line."]
        if note:
            lines.append(f"# {note}")
        for label, request, reply in self.exchanges:
            lines += ["", f"# {label}", f"> {request.hex(' ')}", f"< {reply.hex(' ')}"]
        return lines


async def record_capture(
    pump: GrundfosPump,
    recorder: Recorder,
    *,
    private: bool = True,
    probe: bool = True,
) -> Capture:
    """Read the identity, every known object and (by default) the probe, keeping every frame.

    ``pump`` must talk through ``recorder``. Exchanges already in the recorder are kept, so a
    caller can start recording earlier. An object that fails is noted in ``readings["errors"]``
    and the rest is still read; a lost link raises from the identity or probe step.
    """
    recorder.recording = True
    identity = await read_identity(pump, recorder, private=private)
    readings = await read_all(pump, recorder)
    result = None
    if probe and pump.transport.is_connected:
        recorder.label = "probe"
        result = await pump.probe_capabilities()
    return Capture(identity, readings, result, tuple(recorder.exchanges))
