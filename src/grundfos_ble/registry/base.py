"""Building blocks for pump profiles: typed field layouts with provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from grundfos_ble.genibus.objects import ObjectRef, read_f32, read_f64, read_uint

type Value = float | int | bool | None


class Kind(StrEnum):
    """Wire encoding of a field inside a class-10 object."""

    F32 = "f32"
    F64 = "f64"
    U8 = "u8"
    U16 = "u16"
    U32 = "u32"
    BOOL = "bool"


_SIZES = {Kind.F32: 4, Kind.F64: 8, Kind.U8: 1, Kind.U16: 2, Kind.U32: 4, Kind.BOOL: 1}


class Access(StrEnum):
    """Whether the pump accepts writes to an object."""

    READ = "read"
    READ_WRITE = "read_write"
    WRITE = "write"


class Verified(StrEnum):
    """How much trust an entry deserves."""

    MEASURED = "measured"
    """Read from a real ALPHA2 GO by this project; checked against its display or the clock."""
    ANSWERED = "answered"
    """Answered by a real ALPHA2 GO with plausible values; no display to check them against."""
    CAPTURED = "captured"
    """Seen in captured Grundfos GO traffic on an ALPHA2 GO (Birkemosen HCI snoop, 2026-10)."""
    DOCUMENTED = "documented"
    """Documented for a related Grundfos pump (ALPHA HWR, SCALA1, ALPHA3); untested here."""
    UNKNOWN = "unknown"
    """Present on the wire, meaning not established."""


@dataclass(frozen=True, slots=True)
class Field:
    """One value inside an object's data."""

    key: str
    offset: int
    kind: Kind
    unit: str | None = None
    verified: Verified = Verified.CAPTURED
    note: str = ""

    @property
    def size(self) -> int:
        """Bytes occupied on the wire."""
        return _SIZES[self.kind]

    def read(self, data: bytes) -> Value:
        """Decode this field from ``data``; None when absent or not available."""
        if self.kind is Kind.F32:
            return read_f32(data, self.offset)
        if self.kind is Kind.F64:
            return read_f64(data, self.offset)
        raw = read_uint(data, self.offset, self.size)
        if raw is None:
            return None
        return bool(raw) if self.kind is Kind.BOOL else raw


@dataclass(frozen=True, slots=True)
class ObjectSpec:
    """A class-10 object as one pump family exposes it."""

    key: str
    ref: ObjectRef
    type: int | None
    """Expected object type in replies; None accepts any (layout not yet known)."""
    description: str
    access: Access = Access.READ
    verified: Verified = Verified.CAPTURED
    source: str = ""
    min_size: int = 0
    version: int | None = None
    fields: tuple[Field, ...] = field(default_factory=tuple)

    def decode(self, data: bytes) -> dict[str, Value]:
        """Decode every field present in ``data``."""
        return {f.key: f.read(data) for f in self.fields}


@dataclass(frozen=True, slots=True)
class Profile:
    """Everything the library knows about one pump family/type."""

    name: str
    family: int
    type: int
    objects: dict[str, ObjectSpec]

    def matches(self, family: int, type_: int) -> bool:
        """Return True when the advertised or read identity belongs to this profile."""
        return self.family == family and self.type == type_
