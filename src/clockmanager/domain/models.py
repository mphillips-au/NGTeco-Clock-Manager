"""Stable domain models shared by every layer.

Only device behaviour verified against the real NG-MB1 (``PROTOCOL.md``) is
encoded here. Unverified values are preserved as raw integers rather than being
guessed at, so the GUI can show "Unknown (7)" instead of inventing a meaning.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum

__all__ = [
    "AttendanceEvent",
    "DeviceIdentity",
    "DeviceInfo",
    "DeviceUser",
    "Privilege",
    "PunchDirection",
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

    def as_rows(self) -> list[tuple[str, str]]:
        """Label/value pairs for diagnostics display.

        Contains no credential or secret, so it is safe to log or show.
        """

        def _count(value: int | None) -> str:
            return "Not reported" if value is None else str(value)

        return [
            ("Device name", self.identity.name),
            ("Model", self.identity.model or "Unknown"),
            ("Platform", self.identity.platform or "Unknown"),
            ("Firmware", self.identity.firmware_version or "Unknown"),
            ("Serial number", self.identity.serial_number or "Unknown"),
            (
                "Device time",
                "Unknown" if self.device_time is None else self.device_time.isoformat(),
            ),
            ("Users on device", _count(self.user_count)),
            ("Attendance records", _count(self.attendance_count)),
            ("Fingerprint templates", _count(self.fingerprint_count)),
            ("Face templates", _count(self.face_count)),
        ]
