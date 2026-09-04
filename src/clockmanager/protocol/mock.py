"""A simulated attendance device.

``TESTING.md`` requires a mock device covering connect, device info, users,
attendance, live events and failures, so the test suite and GUI development
never need real hardware.

This lives in the shipped package rather than in ``tests/`` so PHASE 02 can run
the GUI against it without a device on the bench.

It holds no real credentials: fixtures are synthetic.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Self

from clockmanager.domain.models import AttendanceEvent, DeviceIdentity, DeviceInfo, DeviceUser
from clockmanager.protocol.capabilities import (
    NG_MB1_CAPABILITIES,
    Capability,
    DeviceCapabilities,
)
from clockmanager.protocol.errors import DeviceConnectionError, DeviceNotConnectedError
from clockmanager.protocol.interface import DeviceConnectionSettings

__all__ = ["MockAttendanceDevice", "MockDeviceScript", "sample_settings"]


def sample_settings(name: str = "Mock clock") -> DeviceConnectionSettings:
    """Connection settings pointing at a non-routable documentation address.

    ``192.0.2.0/24`` is reserved by RFC 5737 for documentation and examples, so
    this can never reach a real device.
    """
    return DeviceConnectionSettings(name=name, host="192.0.2.10", timeout_seconds=1.0)


@dataclass
class MockDeviceScript:
    """Controls how the mock device behaves, including how it fails."""

    identity: DeviceIdentity = field(
        default_factory=lambda: DeviceIdentity(
            name="Mock NG-MB1",
            serial_number="MOCK-0000000001",
            model="NG-MB1",
            platform="ZMM510_TFT",
            firmware_version="Ver 8.0.4.5-7108-02",
        )
    )
    device_time: datetime = field(default_factory=lambda: datetime(2026, 3, 1, 9, 0, 0))  # noqa: DTZ001
    users: list[DeviceUser] = field(default_factory=list)
    attendance: list[AttendanceEvent] = field(default_factory=list)
    live_events: list[AttendanceEvent | None] = field(default_factory=list)

    #: Number of times connect() should fail before succeeding. Used to
    #: exercise retry and reconnect behaviour.
    connect_failures: int = 0
    #: Number of times each read should fail before succeeding.
    read_failures: int = 0


class MockAttendanceDevice:
    """In-memory device implementing the read-only device interface."""

    def __init__(
        self,
        settings: DeviceConnectionSettings | None = None,
        script: MockDeviceScript | None = None,
    ) -> None:
        self._settings = settings if settings is not None else sample_settings()
        self._script = script if script is not None else MockDeviceScript()
        self._connected = False
        self._stop_live = False
        self._remaining_connect_failures = self._script.connect_failures
        self._remaining_read_failures = self._script.read_failures
        self.connect_calls = 0
        self.disconnect_calls = 0

    # -- identity -------------------------------------------------------------

    @property
    def settings(self) -> DeviceConnectionSettings:
        return self._settings

    @property
    def capabilities(self) -> DeviceCapabilities:
        return NG_MB1_CAPABILITIES

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def script(self) -> MockDeviceScript:
        return self._script

    # -- connection -----------------------------------------------------------

    def connect(self) -> DeviceInfo:
        self.connect_calls += 1
        if self._remaining_connect_failures > 0:
            self._remaining_connect_failures -= 1
            raise DeviceConnectionError(
                f"Mock device refused connection to {self._settings.endpoint}"
            )
        self._connected = True
        return self.get_device_info()

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._connected = False

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.disconnect()

    def _guard(self) -> None:
        if not self._connected:
            raise DeviceNotConnectedError("Mock device is not connected. Call connect() first.")
        if self._remaining_read_failures > 0:
            self._remaining_read_failures -= 1
            raise DeviceConnectionError("Mock device dropped the connection during a read")

    # -- reads ----------------------------------------------------------------

    def get_device_info(self) -> DeviceInfo:
        self.capabilities.require(Capability.DEVICE_INFO)
        return DeviceInfo(
            identity=self._script.identity,
            device_time=self._script.device_time,
            user_count=len(self._script.users),
            attendance_count=len(self._script.attendance),
            fingerprint_count=0,
            face_count=0,
        )

    def get_device_time(self) -> datetime:
        self.capabilities.require(Capability.READ_TIME)
        self._guard()
        return self._script.device_time

    def get_users(self) -> list[DeviceUser]:
        self.capabilities.require(Capability.READ_USERS)
        self._guard()
        return list(self._script.users)

    def get_attendance(self) -> list[AttendanceEvent]:
        self.capabilities.require(Capability.READ_ATTENDANCE)
        self._guard()
        return list(self._script.attendance)

    # -- live capture ---------------------------------------------------------

    def stop_live_capture(self) -> None:
        self._stop_live = True

    def live_capture(self, *, timeout_seconds: float = 10.0) -> Iterator[AttendanceEvent | None]:
        self.capabilities.require(Capability.LIVE_CAPTURE)
        self._guard()
        self._stop_live = False
        for event in self._script.live_events:
            if self._stop_live:
                return
            yield event
