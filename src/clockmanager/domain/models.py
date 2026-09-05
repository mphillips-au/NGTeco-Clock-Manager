"""Stable domain models shared by every layer.

Only device behaviour verified against the real NG-MB1 (``PROTOCOL.md``) is
encoded here. Unverified values are preserved as raw integers rather than being
guessed at, so the GUI can show "Unknown (7)" instead of inventing a meaning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum

__all__ = [
    "AttendanceEvent",
    "DeviceIdentity",
    "DeviceInfo",
    "DeviceOption",
    "DeviceStorage",
    "DeviceUser",
    "FingerprintSlot",
    "OperationLogEntry",
    "Privilege",
    "PunchDirection",
    "StorageCounter",
    "describe_privilege",
    "describe_punch",
]


class Privilege(IntEnum):
    """User privilege values verified on the real NG-MB1."""

    EMPLOYEE = 0
    ADMIN = 14

    @classmethod
    def from_raw(cls, value: int) -> Privilege | None:
        """Return the known privilege for ``value``, or ``None`` if unverified."""
        try:
            return cls(value)
        except ValueError:
            return None


class PunchDirection(IntEnum):
    """Attendance punch direction.

    Derived from the ``punch`` field only. ``status`` is independent raw
    metadata and must never be used to infer direction.
    """

    IN = 0
    OUT = 1

    @classmethod
    def from_raw(cls, value: int) -> PunchDirection | None:
        """Return the known direction for ``value``, or ``None`` if unverified."""
        try:
            return cls(value)
        except ValueError:
            return None


def describe_privilege(value: int) -> str:
    """Human-readable privilege label that never invents a meaning."""
    privilege = Privilege.from_raw(value)
    if privilege is Privilege.EMPLOYEE:
        return "Employee"
    if privilege is Privilege.ADMIN:
        return "Admin"
    return f"Unknown ({value})"


def describe_punch(value: int) -> str:
    """Human-readable punch label that never invents a meaning."""
    direction = PunchDirection.from_raw(value)
    if direction is PunchDirection.IN:
        return "IN"
    if direction is PunchDirection.OUT:
        return "OUT"
    return f"Unknown ({value})"


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """Identity of an attendance device as reported by the device itself."""

    name: str
    serial_number: str | None = None
    model: str | None = None
    platform: str | None = None
    firmware_version: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Device name must not be empty")


@dataclass(frozen=True, slots=True)
class DeviceUser:
    """A user record as stored on a device.

    The device credential/PIN region is deliberately absent: ``SECURITY.md``
    forbids carrying it through ordinary application data structures.
    """

    device_uid: int
    user_id: str
    first_name: str = ""
    last_name: str = ""
    privilege: int = int(Privilege.EMPLOYEE)
    #: Whether the device record's credential region holds any non-zero bytes.
    #: The contents are never read, returned or logged. Whether a populated
    #: region always means "a PIN is set" is UNVERIFIED on real hardware, so
    #: this is presented as an indicator, not as fact.
    has_credential_data: bool = False

    def __post_init__(self) -> None:
        if self.device_uid < 0:
            raise ValueError("device_uid must not be negative")
        if not self.user_id.strip():
            raise ValueError("user_id must not be empty")

    @property
    def display_name(self) -> str:
        """Full name, falling back to the user ID when no name is stored."""
        full_name = f"{self.first_name} {self.last_name}".strip()
        return full_name or self.user_id

    @property
    def privilege_label(self) -> str:
        return describe_privilege(self.privilege)

    @property
    def is_admin(self) -> bool:
        return Privilege.from_raw(self.privilege) is Privilege.ADMIN


@dataclass(frozen=True, slots=True)
class AttendanceEvent:
    """A single attendance punch read from a device.

    ``status`` is preserved verbatim as raw device metadata.
    """

    user_id: str
    occurred_at: datetime
    punch: int
    status: int = 0
    device_uid: int | None = None

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("user_id must not be empty")

    @property
    def direction(self) -> PunchDirection | None:
        """Verified direction, or ``None`` when the punch value is unknown."""
        return PunchDirection.from_raw(self.punch)

    @property
    def direction_label(self) -> str:
        return describe_punch(self.punch)


@dataclass(frozen=True, slots=True)
class StorageCounter:
    """How much of one device store is used, and how much it holds.

    Every field is optional because the device may not report it. A missing
    number stays missing: "Not reported" is honest, ``0`` would claim the store
    is empty.
    """

    label: str
    used: int | None = None
    capacity: int | None = None
    available: int | None = None

    @property
    def free(self) -> int | None:
        """Free slots, from the device's own figure or from the difference."""
        if self.available is not None:
            return self.available
        if self.capacity is None or self.used is None:
            return None
        return max(self.capacity - self.used, 0)

    @property
    def percent_used(self) -> float | None:
        """Fraction used, 0-100, or ``None`` when it cannot be worked out."""
        if self.used is None or not self.capacity:
            return None
        return min(self.used / self.capacity * 100.0, 100.0)

    def describe(self) -> str:
        """One line an operator can read, inventing nothing it was not told."""
        if self.used is None and self.capacity is None:
            return "Not reported"
        if self.capacity is None:
            return f"{self.used:,} used (capacity not reported)"
        if self.used is None:
            return f"Capacity {self.capacity:,} (usage not reported)"
        free = self.free
        suffix = "" if free is None else f" — {free:,} free"
        return f"{self.used:,} of {self.capacity:,} used{suffix}"


@dataclass(frozen=True, slots=True)
class DeviceStorage:
    """What the device reports about its own capacity and usage.

    Read from ``CMD_GET_FREE_SIZES``, which the application already called for
    its record counts and then threw most of away. Nothing here is inferred:
    each counter is a field the device sent.

    ``cards`` is deliberately absent. The response carries a field ``pyzk``
    labels ``cards``, but PHASE 15 showed it did not change when a user was
    added and nothing establishes what it counts, so it is not shown rather
    than shown wrongly.
    """

    users: StorageCounter = field(default_factory=lambda: StorageCounter("Users"))
    fingerprints: StorageCounter = field(default_factory=lambda: StorageCounter("Fingerprints"))
    attendance: StorageCounter = field(default_factory=lambda: StorageCounter("Attendance records"))
    faces: StorageCounter = field(default_factory=lambda: StorageCounter("Faces"))
    #: The device's own operation-log record count, when it reported one.
    operation_log_records: int | None = None

    @property
    def counters(self) -> tuple[StorageCounter, ...]:
        return (self.users, self.fingerprints, self.attendance, self.faces)

    def as_rows(self) -> list[tuple[str, str]]:
        """Label/value pairs for display. Contains no personal data."""
        rows = [(counter.label, counter.describe()) for counter in self.counters]
        if self.operation_log_records is not None:
            rows.append(("Device operation log", f"{self.operation_log_records:,} records"))
        return rows


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """A point-in-time snapshot of a device, as read from it.

    ``device_time`` is the device's own clock. The device transmits no timezone,
    so it is naive by construction and must not be assumed to be UTC.

    Counts are reported as ``None`` when the device did not supply them, rather
    than defaulting to zero and implying an empty device.
    """

    identity: DeviceIdentity
    device_time: datetime | None = None
    user_count: int | None = None
    attendance_count: int | None = None
    fingerprint_count: int | None = None
    face_count: int | None = None
    #: Capacity and usage as the device reported them, when it was asked. The
    #: connection snapshot fills this in; a device or mock that does not
    #: report capacities leaves it ``None`` rather than showing zeroes.
    storage: DeviceStorage | None = None

    def as_rows(self) -> list[tuple[str, str]]:
        """Label/value pairs for diagnostics display.

        Contains no credential or secret, so it is safe to log or show. When
        the device reported its capacities, each count is shown against the
        capacity it belongs to ("6 of 30,000 used") rather than on its own,
        because a bare count says nothing about how close the device is to
        being full.
        """

        def _count(value: int | None, counter: StorageCounter | None) -> str:
            if counter is not None and counter.capacity is not None:
                return StorageCounter(
                    counter.label, value, counter.capacity, counter.free
                ).describe()
            return "Not reported" if value is None else f"{value:,}"

        storage = self.storage
        rows = [
            ("Device name", self.identity.name),
            ("Model", self.identity.model or "Unknown"),
            ("Platform", self.identity.platform or "Unknown"),
            ("Firmware", self.identity.firmware_version or "Unknown"),
            ("Serial number", self.identity.serial_number or "Unknown"),
            (
                "Device time",
                "Unknown" if self.device_time is None else self.device_time.isoformat(),
            ),
            (
                "Users on device",
                _count(self.user_count, None if storage is None else storage.users),
            ),
            (
                "Attendance records",
                _count(self.attendance_count, None if storage is None else storage.attendance),
            ),
            (
                "Fingerprint templates",
                _count(self.fingerprint_count, None if storage is None else storage.fingerprints),
            ),
            ("Face templates", _count(self.face_count, None if storage is None else storage.faces)),
        ]
        if storage is not None and storage.operation_log_records is not None:
            rows.append(("Device operation log", f"{storage.operation_log_records:,} records"))
        return rows


@dataclass(frozen=True, slots=True)
class FingerprintSlot:
    """One enrolled fingerprint, described but never disclosed.

    The device's fingerprint store can be enumerated (``PROTOCOL.md``,
    "Fingerprint enumeration"), which answers the operationally useful
    questions -- who has a finger enrolled, how many, and which slot -- without
    the template itself.

    ``template_bytes`` is the **length** of the stored template. The template
    contents are never carried on this type, never returned from the protocol
    layer and never logged, exported or persisted: they are biometric data and
    ``SECURITY.md`` forbids it.
    """

    device_uid: int
    finger_index: int
    valid: int
    template_bytes: int

    @property
    def is_valid(self) -> bool:
        return self.valid != 0


@dataclass(frozen=True, slots=True)
class DeviceOption:
    """One named setting read back from the device.

    ``value`` is ``None`` when the device refused the name. That is a normal,
    harmless answer (the NG-MB1 returns code 4999 for an option its firmware
    does not have) and it is worth showing: "this model has no work codes" is
    itself useful, and it stops the same name being tried again as though it
    might work next time.

    These are reads. The application has no way to write an option, by design
    (:mod:`clockmanager.protocol.options`).
    """

    name: str
    label: str
    group: str
    value: str | None = None
    note: str = ""

    @property
    def answered(self) -> bool:
        return self.value is not None

    @property
    def display_value(self) -> str:
        return self.value if self.value is not None else "Not available on this model"


@dataclass(frozen=True, slots=True)
class OperationLogEntry:
    """One entry from the device's own record of what was done at the keypad.

    This is a different question from the application's audit trail. The audit
    trail records what *this application* did; the device separately records
    enrolments, deletions and administrator menu access performed by somebody
    standing in front of the clock, and nothing in the application could see
    them before.

    Only two things about an entry are established (PHASE 15): entries are 16
    bytes, and bytes 4:8 hold a packed ZKTeco timestamp. The remaining fields
    are decoded positionally from the layout the ZKTeco SDK family uses and
    their meanings are **not verified on this device**, so they are carried as
    raw integers and described as unknown rather than being given names this
    application cannot stand behind.

    ``occurred_at`` is ``None`` when the timestamp did not decode to a
    plausible date, which is how a misread layout shows itself instead of
    inventing a punch-shaped date.
    """

    index: int
    operation: int
    operator_uid: int
    occurred_at: datetime | None = None
    parameters: tuple[int, int, int] = (0, 0, 0)

    @property
    def operation_label(self) -> str:
        """The operation code, never a guessed name for it."""
        return f"Operation {self.operation}"

    @property
    def occurred_label(self) -> str:
        if self.occurred_at is None:
            return "Unreadable timestamp"
        return self.occurred_at.isoformat(sep=" ", timespec="seconds")
