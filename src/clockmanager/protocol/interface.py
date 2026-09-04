"""The attendance-device interface.

``ARCHITECTURE.md`` requires an interface for attendance devices so more models
can be supported later, while only the NG-MB1 is implemented now.

PHASE 01 is read-only: this interface deliberately declares no write, delete or
clear operation. Adding one is a PHASE 03 decision that must come with a
verified 120-byte write path, confirmation, read-back and audit logging.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from clockmanager.domain.models import AttendanceEvent, DeviceInfo, DeviceUser
from clockmanager.protocol.capabilities import DeviceCapabilities
from clockmanager.protocol.constants import DEFAULT_PORT

__all__ = ["AttendanceDevice", "DeviceConnectionSettings"]


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
