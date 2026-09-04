"""Device application services.

The GUI talks to this module, never to :mod:`clockmanager.protocol` directly
(``ARCHITECTURE.md``). Everything here is synchronous and PySide6-free; keeping
it off the UI thread is the GUI's job.

This module builds devices and performs read-only device operations. Saving a
device profile writes to the local database only.

User writing lives in :mod:`clockmanager.services.users`, which asks
:meth:`DeviceService.build` for a write-enabled adapter. A device built here
without ``allow_writes`` cannot write at all: the capability gate refuses it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from typing import Protocol

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.models import AttendanceEvent, DeviceInfo, DeviceUser
from clockmanager.errors import ClockManagerError
from clockmanager.persistence.database import Database
from clockmanager.persistence.models import DeviceRecord
from clockmanager.persistence.repositories import DeviceRepository
from clockmanager.protocol.capabilities import DeviceCapabilities
from clockmanager.protocol.constants import DEFAULT_PORT as _PROTOCOL_DEFAULT_PORT
from clockmanager.protocol.errors import DeviceError
from clockmanager.protocol.interface import AttendanceDevice, DeviceConnectionSettings
from clockmanager.protocol.mb1 import NGTecoMB1Device
from clockmanager.protocol.mock import MockAttendanceDevice, MockDeviceScript

__all__ = [
    "DEFAULT_DEVICE_PORT",
    "ConnectionTestResult",
    "DeviceFactory",
    "DeviceProfile",
    "DeviceService",
    "MockDeviceFactory",
    "build_device",
    "build_mock_device",
]

_logger = get_logger(__name__)

#: Re-exported so the GUI never has to import the protocol layer directly
#: (ARCHITECTURE.md: the GUI calls application services only).
DEFAULT_DEVICE_PORT = _PROTOCOL_DEFAULT_PORT


class DeviceFactory(Protocol):
    """Builds a device from a profile.

    Injectable so the GUI and tests can run against the mock device with no
    hardware. The write unlocks are keyword-only and default to off, so a
    caller that does not ask for writing cannot accidentally receive a device
    that can write.
    """

    def __call__(
        self,
        profile: DeviceProfile,
        *,
        allow_writes: bool = False,
        allow_credential_writes: bool = False,
    ) -> AttendanceDevice: ...


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """A configured device, as stored locally.

    ``communication_password`` is a secret: it is excluded from ``repr`` and
    must never be logged, exported or displayed unmasked (``SECURITY.md``).
    """

    name: str
    host: str = ""
    port: int = DEFAULT_DEVICE_PORT
    timeout_seconds: float = 10.0
    auto_reconnect: bool = True
    sync_interval_seconds: int = 300
    enabled: bool = True
    communication_password: int = field(default=0, repr=False)
    device_id: int | None = None
    model: str | None = None
    platform: str | None = None
    firmware_version: str | None = None
    serial_number: str | None = None

    @property
    def is_configured(self) -> bool:
        """Whether this profile has enough detail to attempt a connection."""
        return bool(self.host.strip())

    @property
    def endpoint(self) -> str:
        """``host:port``, safe to log. Never includes the password."""
        return f"{self.host}:{self.port}" if self.is_configured else "(no address configured)"

    @property
    def has_communication_password(self) -> bool:
        return self.communication_password != 0

    def validate(self) -> list[str]:
        """Return human-readable problems, empty when the profile is usable."""
        problems: list[str] = []
        if not self.name.strip():
            problems.append("Device name is required.")
        if not self.host.strip():
            problems.append("IP address or hostname is required.")
        if not 1 <= self.port <= 65535:
            problems.append("Port must be between 1 and 65535.")
        if self.timeout_seconds <= 0:
            problems.append("Timeout must be greater than zero seconds.")
        if self.sync_interval_seconds < 30:
            problems.append("Sync interval must be at least 30 seconds.")
        if self.communication_password < 0:
            problems.append("Communication password must not be negative.")
        return problems

    def to_connection_settings(self) -> DeviceConnectionSettings:
        """Convert to the protocol layer's connection settings."""
        problems = self.validate()
        if problems:
            raise ClockManagerError("; ".join(problems))
        return DeviceConnectionSettings(
            name=self.name,
            host=self.host.strip(),
            port=self.port,
            timeout_seconds=self.timeout_seconds,
            communication_password=self.communication_password,
        )

    @classmethod
    def from_record(cls, record: DeviceRecord) -> DeviceProfile:
        return cls(
            device_id=record.id,
            name=record.name,
            host=record.host or "",
            port=record.port,
            timeout_seconds=record.timeout_seconds,
            auto_reconnect=record.auto_reconnect,
            sync_interval_seconds=record.sync_interval_seconds,
            enabled=record.enabled,
            communication_password=record.communication_password,
            model=record.model,
            platform=record.platform,
            firmware_version=record.firmware_version,
            serial_number=record.serial_number,
        )


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    """The outcome of a connection test, safe to display and to log."""

    ok: bool
    summary: str
    info: DeviceInfo | None = None
    error_type: str | None = None

    def as_rows(self) -> list[tuple[str, str]]:
        rows = [("Result", "Connected" if self.ok else "Failed"), ("Detail", self.summary)]
        if self.info is not None:
            rows.extend(self.info.as_rows())
        return rows


def build_device(
    profile: DeviceProfile,
    *,
    allow_writes: bool = False,
    allow_credential_writes: bool = False,
) -> AttendanceDevice:
    """Build the real NG-MB1 adapter for ``profile``."""
    return NGTecoMB1Device(
        profile.to_connection_settings(),
        auto_reconnect=profile.auto_reconnect,
        allow_writes=allow_writes,
        allow_credential_writes=allow_credential_writes,
    )


class MockDeviceFactory:
    """Builds mock devices that remember what was written to them.

    One instance per application context. A mock rebuilt from scratch on every
    call would forget every write the moment the service returned, which would
    make the write path impossible to exercise without hardware and would let a
    broken write look like a successful one.

    State is held per instance rather than in a module-level cache, so two
    application contexts -- two tests, say -- never see each other's devices.
    """

    def __init__(self) -> None:
        self._devices: dict[str, MockAttendanceDevice] = {}

    def __call__(
        self,
        profile: DeviceProfile,
        *,
        allow_writes: bool = False,
        allow_credential_writes: bool = False,
    ) -> AttendanceDevice:
        key = profile.name
        device = self._devices.get(key)
        if device is None:
            device = self._build(profile)
            self._devices[key] = device

        # The unlocks belong to the caller's request, not to the stored device,
        # so they are reapplied on every hand-out. A read must never receive a
        # device that is still allowed to write from an earlier call.
        device.set_write_unlocks(
            allow_writes=allow_writes,
            allow_credential_writes=allow_credential_writes,
        )
        return device

    def _build(self, profile: DeviceProfile) -> MockAttendanceDevice:
        from clockmanager.protocol.records import parse_user_payload
        from clockmanager.services.sample_data import sample_attendance, sample_user_payload

        users = parse_user_payload(sample_user_payload())
        return MockAttendanceDevice(
            settings=profile.to_connection_settings() if profile.is_configured else None,
            script=MockDeviceScript(users=users, attendance=sample_attendance(users)),
        )


def build_mock_device(
    profile: DeviceProfile,
    *,
    allow_writes: bool = False,
    allow_credential_writes: bool = False,
) -> AttendanceDevice:
    """Build a one-shot mock device.

    Kept for callers that want a throwaway device. Anything that writes should
    use :class:`MockDeviceFactory`, whose devices remember their contents.
    """
    return MockDeviceFactory()(
        profile,
        allow_writes=allow_writes,
        allow_credential_writes=allow_credential_writes,
    )


class DeviceService:
    """Stores device profiles and performs read-only device operations."""

    def __init__(
        self,
        database: Database,
        *,
        device_factory: DeviceFactory = build_device,
    ) -> None:
        self._database = database
        self._device_factory = device_factory

    # -- stored profiles ------------------------------------------------------

    def list_profiles(self) -> list[DeviceProfile]:
        with self._database.session() as session:
            return [
                DeviceProfile.from_record(record) for record in DeviceRepository(session).list_all()
            ]

    def get_profile(self, device_id: int) -> DeviceProfile | None:
        with self._database.session() as session:
            record = session.get(DeviceRecord, device_id)
            return None if record is None else DeviceProfile.from_record(record)

    def first_enabled_profile(self) -> DeviceProfile | None:
        """The profile the dashboard works with by default."""
        for profile in self.list_profiles():
            if profile.enabled:
                return profile
        return None

    def save_profile(self, profile: DeviceProfile) -> DeviceProfile:
        """Create or update a stored profile. Writes locally, never to a device."""
        problems = profile.validate()
        if problems:
            raise ClockManagerError("; ".join(problems))

        with self._database.session() as session:
            repository = DeviceRepository(session)
            record = (
                session.get(DeviceRecord, profile.device_id)
                if profile.device_id is not None
                else None
            )
            if record is None:
                existing = repository.get_by_name(profile.name)
                if existing is not None and existing.id != profile.device_id:
                    raise ClockManagerError(f"A device named {profile.name!r} already exists.")
                record = DeviceRecord(name=profile.name)
                session.add(record)

            record.name = profile.name
            record.host = profile.host.strip() or None
            record.port = profile.port
            record.communication_password = profile.communication_password
            record.timeout_seconds = profile.timeout_seconds
            record.auto_reconnect = profile.auto_reconnect
            record.sync_interval_seconds = profile.sync_interval_seconds
            record.enabled = profile.enabled
            session.flush()
            saved_id = record.id

        _logger.info(
            "Saved device profile",
            extra={"device": profile.name, "endpoint": profile.endpoint},
        )
        return replace(profile, device_id=saved_id)

    def delete_profile(self, device_id: int) -> None:
        """Remove a stored profile and its local data. Never touches a device."""
        with self._database.session() as session:
            record = session.get(DeviceRecord, device_id)
            if record is None:
                return
            name = record.name
            session.delete(record)
        _logger.info("Deleted device profile", extra={"device": name})

    def record_identity(self, profile: DeviceProfile, info: DeviceInfo) -> None:
        """Store what the device reported about itself."""
        if profile.device_id is None:
            return
        with self._database.session() as session:
            record = session.get(DeviceRecord, profile.device_id)
            if record is None:
                return
            record.model = info.identity.model
            record.platform = info.identity.platform
            record.firmware_version = info.identity.firmware_version
            if info.identity.serial_number:
                record.serial_number = info.identity.serial_number

    # -- device operations ----------------------------------------------------

    def build(
        self,
        profile: DeviceProfile,
        *,
        allow_writes: bool = False,
        allow_credential_writes: bool = False,
    ) -> AttendanceDevice:
        """Build an unconnected device for ``profile``.

        Write unlocks are opt-in and never inferred: a caller that wants to
        write must say so, and the device's own capability gate still applies.
        """
        if not allow_writes and allow_credential_writes:
            raise ClockManagerError(
                "Credential writing cannot be enabled while user writing is not."
            )
        return self._device_factory(
            profile,
            allow_writes=allow_writes,
            allow_credential_writes=allow_credential_writes,
        )

    def capabilities(self, profile: DeviceProfile) -> DeviceCapabilities:
        return self._device_factory(profile).capabilities

    def test_connection(self, profile: DeviceProfile) -> ConnectionTestResult:
        """Connect, read the device snapshot and disconnect.

        Never raises for a device problem: the failure is the result.
        """
        try:
            device = self._device_factory(profile)
        except ClockManagerError as exc:
            return ConnectionTestResult(ok=False, summary=str(exc), error_type=type(exc).__name__)

        try:
            info = device.connect()
        except DeviceError as exc:
            _logger.warning(
                "Connection test failed",
                extra={"device": profile.name, "endpoint": profile.endpoint},
            )
            return ConnectionTestResult(ok=False, summary=str(exc), error_type=type(exc).__name__)
        else:
            self.record_identity(profile, info)
            return ConnectionTestResult(
                ok=True,
                summary=f"Connected to {profile.endpoint}",
                info=info,
            )
        finally:
            device.disconnect()

    def read_device_info(self, profile: DeviceProfile) -> DeviceInfo:
        with self._connected(profile) as device:
            return device.get_device_info()

    def read_users(self, profile: DeviceProfile) -> list[DeviceUser]:
        with self._connected(profile) as device:
            return device.get_users()

    def read_attendance(self, profile: DeviceProfile) -> list[AttendanceEvent]:
        with self._connected(profile) as device:
            return device.get_attendance()

    def open_device(self, profile: DeviceProfile) -> AttendanceDevice:
        """Return a connected device for a long-running operation.

        The caller owns it and must call ``disconnect()``. Used by live capture,
        which cannot use a short-lived context manager.
        """
        device = self._device_factory(profile)
        device.connect()
        return device

    def connected(self, profile: DeviceProfile) -> _ConnectedDevice:
        """A context manager that connects on entry and always disconnects."""
        return _ConnectedDevice(self._device_factory(profile))

    #: Retained for the existing read helpers in this module.
    _connected = connected


class _ConnectedDevice:
    """Connects on entry and always disconnects on exit."""

    def __init__(self, device: AttendanceDevice) -> None:
        self._device = device

    def __enter__(self) -> AttendanceDevice:
        self._device.connect()
        return self._device

    def __exit__(self, *_exc: object) -> None:
        self._device.disconnect()


def live_events(
    device: AttendanceDevice, *, timeout_seconds: float = 5.0
) -> Iterator[AttendanceEvent | None]:
    """Yield live attendance events from an already-connected device."""
    yield from device.live_capture(timeout_seconds=timeout_seconds)
