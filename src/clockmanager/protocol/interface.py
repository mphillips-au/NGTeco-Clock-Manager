"""The attendance-device interface.

``ARCHITECTURE.md`` requires an interface for attendance devices so more models
can be supported later, while only the NG-MB1 is implemented now.

:class:`AttendanceDevice` stays read-only. PHASE 03 adds user writes as a
*separate* interface, :class:`WritableUserDevice`, so a caller must ask for the
writing capability explicitly and a device that cannot write is a type error
rather than a runtime surprise.

There is still no interface for clearing attendance, resetting a device or
writing biometric templates, and none may be added without device evidence.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from clockmanager.domain.models import AttendanceEvent, DeviceInfo, DeviceUser
from clockmanager.domain.users import UserDraft, UserWriteOutcome
from clockmanager.protocol.capabilities import DeviceCapabilities
from clockmanager.protocol.constants import DEFAULT_PORT

__all__ = ["AttendanceDevice", "DeviceConnectionSettings", "WritableUserDevice"]


@dataclass(frozen=True, slots=True)
class DeviceConnectionSettings:
    """How to reach one device.

    ``communication_password`` is a secret. It is excluded from ``repr`` so it
    cannot reach a log, traceback or diagnostic dump by accident
    (``SECURITY.md``). There is no default host: ``AGENTS.md`` forbids a
    hardcoded production address.
    """

    name: str
    host: str
    port: int = DEFAULT_PORT
    timeout_seconds: float = 10.0
    communication_password: int = field(default=0, repr=False)
    force_udp: bool = False
    omit_ping: bool = True
    encoding: str = "utf-8"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Device name must not be empty")
        if not self.host.strip():
            raise ValueError("Device host must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError(f"Device port must be between 1 and 65535, got {self.port}")
        if self.timeout_seconds <= 0:
            raise ValueError("Device timeout must be greater than zero")

    @property
    def endpoint(self) -> str:
        """``host:port``, safe to log. Contains no credential."""
        return f"{self.host}:{self.port}"

    @property
    def has_communication_password(self) -> bool:
        return self.communication_password != 0


@runtime_checkable
class AttendanceDevice(Protocol):
    """Read-only operations every attendance device must provide."""

    @property
    def settings(self) -> DeviceConnectionSettings: ...

    @property
    def capabilities(self) -> DeviceCapabilities: ...

    @property
    def is_connected(self) -> bool: ...

    def connect(self) -> DeviceInfo:
        """Open a connection and return the device snapshot."""
        ...

    def disconnect(self) -> None:
        """Close the connection. Safe to call when already closed."""
        ...

    def get_device_info(self) -> DeviceInfo:
        """Read identity, clock and record counts."""
        ...

    def get_device_time(self) -> datetime:
        """Read the device clock (naive device-local time)."""
        ...

    def get_users(self) -> list[DeviceUser]:
        """Read all users using the device's verified record layout."""
        ...

    def get_attendance(self) -> list[AttendanceEvent]:
        """Read all stored attendance records."""
        ...

    def live_capture(self, *, timeout_seconds: float = 10.0) -> Iterator[AttendanceEvent | None]:
        """Yield attendance events as they happen.

        Yields ``None`` on each idle timeout so a caller can check whether it
        has been asked to stop without waiting indefinitely.
        """
        ...

    def stop_live_capture(self) -> None:
        """Ask an in-progress :meth:`live_capture` to finish."""
        ...


@runtime_checkable
class WritableUserDevice(AttendanceDevice, Protocol):
    """A device that can also create, update and delete users.

    Every implementation must perform the whole write sequence internally --
    read, validate, build, send, verify the acknowledgement, read back and
    compare -- and raise unless it completed. A caller cannot be trusted to do
    those steps, and a half-done write must never look like a success.

    Both operations are additionally gated on the device's capability set, so a
    device whose write path is unproven refuses until an operator unlocks it.
    """

    def apply_user_write(self, draft: UserDraft) -> UserWriteOutcome:
        """Create or update one user and return the verified result."""
        ...

    def delete_user(self, device_uid: int) -> DeviceUser:
        """Delete one user, returning it as it was immediately before deletion."""
        ...

    def next_available_uid(self) -> int:
        """The lowest device UID not currently in use."""
        ...
