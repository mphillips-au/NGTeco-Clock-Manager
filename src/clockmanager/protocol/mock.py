"""A simulated attendance device.

``TESTING.md`` requires a mock device covering connect, device info, users,
attendance, live events and failures, so the test suite and GUI development
never need real hardware.

This lives in the shipped package rather than in ``tests/`` so PHASE 02 can run
the GUI against it without a device on the bench.

It holds no real credentials: fixtures are synthetic.

PHASE 03 gives it the same write path as the real adapter, including the
capability gate, so the write sequence and its failure modes can be tested
without hardware. Writes are refused unless the mock is built with
``allow_writes=True``, exactly as the real device requires an operator unlock.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Self

from clockmanager.domain.models import AttendanceEvent, DeviceIdentity, DeviceInfo, DeviceUser
from clockmanager.domain.users import (
    CredentialAction,
    UserDraft,
    UserWriteOutcome,
    describe_changes,
)
from clockmanager.protocol.builders import RawUserRecord, build_user_record
from clockmanager.protocol.capabilities import (
    NG_MB1_CAPABILITIES,
    WRITE_CAPABILITIES,
    Capability,
    DeviceCapabilities,
)
from clockmanager.protocol.constants import MAX_USER_UID, USER_CREDENTIAL_SLICE
from clockmanager.protocol.errors import (
    DeviceCapabilityError,
    DeviceConnectionError,
    DeviceNotConnectedError,
    DeviceValidationError,
    DeviceVerificationError,
    DeviceWriteError,
)
from clockmanager.protocol.interface import DeviceConnectionSettings
from clockmanager.protocol.records import parse_user_record

__all__ = ["MockAttendanceDevice", "MockDeviceScript", "sample_settings"]


#: Filler for a seeded credential region. Deliberately not a plausible PIN:
#: the mock only ever needs the region to be non-empty so that credential
#: preservation can be observed.
_CREDENTIAL_FILLER_BYTE = 0xAB


def _seed_record(user: DeviceUser) -> bytes:
    """Build the mock's starting 120-byte record for one seeded user.

    A user whose ``has_credential_data`` is set gets a non-empty credential
    region, so a test can prove that an update preserved it.
    """
    record = build_user_record(
        uid=user.device_uid,
        user_id=user.user_id,
        first_name=user.first_name,
        last_name=user.last_name,
        privilege=user.privilege,
        credential_action=CredentialAction.CLEAR,
    )
    if not user.has_credential_data:
        return record
    filler = bytes([_CREDENTIAL_FILLER_BYTE]) * (
        USER_CREDENTIAL_SLICE.stop - USER_CREDENTIAL_SLICE.start
    )
    return record[: USER_CREDENTIAL_SLICE.start] + filler + record[USER_CREDENTIAL_SLICE.stop :]


def _without_credential(record: bytes) -> bytes:
    """A record with the opaque credential region removed, for comparison."""
    return record[: USER_CREDENTIAL_SLICE.start] + record[USER_CREDENTIAL_SLICE.stop :]


def _mock_capabilities(*, allow_writes: bool, allow_credential_writes: bool) -> DeviceCapabilities:
    """Mirror the real adapter's capability policy, locks and unlocks included."""
    if allow_credential_writes and not allow_writes:
        raise DeviceValidationError(
            "Credential writing cannot be unlocked while user writing is locked."
        )
    unlocked: list[Capability] = []
    if allow_writes:
        unlocked.extend((Capability.WRITE_USERS, Capability.DELETE_USERS))
    if allow_credential_writes:
        unlocked.append(Capability.WRITE_USER_PASSWORD)

    still_locked = [c for c in WRITE_CAPABILITIES if c not in unlocked]
    capabilities = NG_MB1_CAPABILITIES.locked(
        still_locked, reason="Mock device with writing left locked."
    )
    if not unlocked:
        return capabilities
    return capabilities.unlocked(unlocked, reason="Mock device with writes enabled for testing.")


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
    #: Number of times a write should be rejected by the device before
    #: succeeding. Exercises the "device refused the write" path.
    write_failures: int = 0
    #: Raw attendance payload for diagnostics (PHASE 10), as built by
    #: ``tests.fixtures.mb1.build_attendance_payload``. ``None`` means this
    #: mock carries parsed events only, so diagnostics shows parsed
    #: attendance with an explanatory note instead of raw bytes.
    attendance_raw: bytes | None = None
    #: The device record count the raw payload above was read with.
    attendance_record_count: int = 0
    #: When set, the mock stores this record instead of the one it was sent,
    #: so read-back comparison failure can be tested.
    corrupt_next_write: bool = False


class MockAttendanceDevice:
    """In-memory device implementing the read-only device interface."""

    def __init__(
        self,
        settings: DeviceConnectionSettings | None = None,
        script: MockDeviceScript | None = None,
        *,
        allow_writes: bool = False,
        allow_credential_writes: bool = False,
    ) -> None:
        self._settings = settings if settings is not None else sample_settings()
        self._script = script if script is not None else MockDeviceScript()
        self._connected = False
        self._stop_live = False
        self._remaining_connect_failures = self._script.connect_failures
        self._remaining_read_failures = self._script.read_failures
        self._remaining_write_failures = self._script.write_failures
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.write_calls = 0
        self.delete_calls = 0
        #: Raw 120-byte records, so the mock exercises the same
        #: read-modify-write and credential-preservation logic as the adapter.
        self._records: dict[int, bytes] = {
            user.device_uid: _seed_record(user) for user in self._script.users
        }
        self._capabilities = _mock_capabilities(
            allow_writes=allow_writes,
            allow_credential_writes=allow_credential_writes,
        )

    # -- identity -------------------------------------------------------------

    @property
    def settings(self) -> DeviceConnectionSettings:
        return self._settings

    @property
    def capabilities(self) -> DeviceCapabilities:
        return self._capabilities

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def script(self) -> MockDeviceScript:
        return self._script

    def set_write_unlocks(self, *, allow_writes: bool, allow_credential_writes: bool) -> None:
        """Re-apply the operator write unlocks to a device being handed out.

        A long-lived mock is reused across calls, so its unlocks must reflect
        what the current caller asked for, not what an earlier one did.
        """
        self._capabilities = _mock_capabilities(
            allow_writes=allow_writes,
            allow_credential_writes=allow_credential_writes,
        )

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
        return [parse_user_record(raw) for _uid, raw in sorted(self._records.items())]

    def read_raw_user_records(self) -> list[RawUserRecord]:
        self.capabilities.require(Capability.READ_USERS)
        self._guard()
        return [
            RawUserRecord(uid=uid, user_id=parse_user_record(raw).user_id, raw=raw)
            for uid, raw in sorted(self._records.items())
        ]

    def read_raw_attendance_payload(self) -> tuple[bytes, int]:
        """Return the script's fixture payload, if it carries one.

        Diagnostics-only. A mock built without ``attendance_raw`` has parsed
        events but no packet bytes, and says so instead of inventing any.
        """
        self.capabilities.require(Capability.READ_ATTENDANCE)
        self._guard()
        if self._script.attendance_raw is None:
            raise DeviceCapabilityError(
                "This mock carries parsed attendance only; no raw payload was provided."
            )
        return self._script.attendance_raw, self._script.attendance_record_count

    def get_attendance(self) -> list[AttendanceEvent]:
        self.capabilities.require(Capability.READ_ATTENDANCE)
        self._guard()
        return list(self._script.attendance)

    # -- writes ---------------------------------------------------------------

    def next_available_uid(self) -> int:
        for candidate in range(1, MAX_USER_UID + 1):
            if candidate not in self._records:
                return candidate
        raise DeviceWriteError("The mock device is full.")

    def apply_user_write(self, draft: UserDraft) -> UserWriteOutcome:
        """Create or update a user, following the same sequence as the adapter."""
        self.capabilities.require(Capability.WRITE_USERS)
        if draft.changes_credential:
            self.capabilities.require(Capability.WRITE_USER_PASSWORD)
        self._guard()
        self.write_calls += 1

        draft = draft.normalised()
        problems = draft.validate()
        if problems:
            raise DeviceValidationError(" ".join(problems))

        current_raw = None if draft.device_uid is None else self._records.get(draft.device_uid)
        if draft.device_uid is not None and current_raw is None:
            raise DeviceValidationError(
                f"No user with device UID {draft.device_uid} exists on the device."
            )

        current = (
            None
            if current_raw is None
            else RawUserRecord(uid=draft.device_uid or 0, user_id=draft.user_id, raw=current_raw)
        )
        before = None if current_raw is None else parse_user_record(current_raw)

        for uid, raw in self._records.items():
            if parse_user_record(raw).user_id == draft.user_id and uid != draft.device_uid:
                raise DeviceValidationError(
                    f"User ID {draft.user_id!r} is already used by device UID {uid}."
                )

        uid = draft.device_uid if draft.device_uid is not None else self.next_available_uid()
        record = build_user_record(
            uid=uid,
            user_id=draft.user_id,
            first_name=draft.first_name,
            last_name=draft.last_name,
            privilege=draft.privilege,
            credential_action=draft.credential_action,
            existing=current,
            password=draft.password,
        )

        if self._remaining_write_failures > 0:
            self._remaining_write_failures -= 1
            raise DeviceWriteError("Mock device rejected the write.")

        stored = record
        if self._script.corrupt_next_write:
            self._script.corrupt_next_write = False
            # Store something the device was not asked to store, so the
            # read-back comparison has to catch it.
            stored = record[:35] + b"X" + record[36:]
        self._records[uid] = stored

        read_back = self._records[uid]
        if _without_credential(read_back) != _without_credential(record):
            raise DeviceVerificationError(
                f"The record stored for UID {uid} does not match what was sent."
            )

        return UserWriteOutcome(
            user=parse_user_record(read_back),
            created=current_raw is None,
            changes=tuple(describe_changes(before, draft)),
        )

    def delete_user(self, device_uid: int) -> DeviceUser:
        """Delete a user, verifying the removal, as the adapter does."""
        self.capabilities.require(Capability.DELETE_USERS)
        self._guard()
        self.delete_calls += 1

        raw = self._records.get(device_uid)
        if raw is None:
            raise DeviceValidationError(
                f"No user with device UID {device_uid} exists on the device."
            )
        doomed = parse_user_record(raw)

        if self._remaining_write_failures > 0:
            self._remaining_write_failures -= 1
            raise DeviceWriteError("Mock device rejected the delete.")

        del self._records[device_uid]
        if device_uid in self._records:  # pragma: no cover - defensive
            raise DeviceVerificationError(f"UID {device_uid} is still present after deletion.")
        return doomed

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
