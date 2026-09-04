"""User management application service.

The GUI calls this; it never calls the protocol layer directly
(``ARCHITECTURE.md``). Everything here is synchronous and PySide6-free, so the
future headless service manages users through exactly the same code.

Responsibilities that belong here rather than in the adapter:

* refusing a write the operator has not enabled, and saying why
* validating a draft before a connection is opened
* describing exactly what a destructive action would do, so a human can confirm
  it against real device state rather than against a guess
* recording an audit entry for every attempt — succeeded, failed or refused

The byte-level sequence (read, build, send, verify, read back, compare) lives
in the device adapter, where it cannot be skipped by a caller.
"""

from __future__ import annotations

from dataclasses import dataclass

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.auth import Permission, Role, require
from clockmanager.domain.models import DeviceUser
from clockmanager.domain.users import (
    CredentialAction,
    UserDraft,
    UserWriteOutcome,
    describe_changes,
)
from clockmanager.errors import ClockManagerError
from clockmanager.protocol.capabilities import Capability
from clockmanager.protocol.errors import DeviceCapabilityError, DeviceError
from clockmanager.protocol.interface import WritableUserDevice
from clockmanager.services.audit import AuditAction, AuditOutcome, AuditService
from clockmanager.services.devices import DeviceProfile, DeviceService

__all__ = ["CredentialAction", "DeleteImpact", "UserService", "WriteAvailability"]

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WriteAvailability:
    """Whether user writing is available, and if not, why not.

    The GUI uses this to explain a disabled button instead of silently
    presenting one that fails.
    """

    users: bool
    credentials: bool
    reason: str = ""

    @property
    def any_write_allowed(self) -> bool:
        return self.users


@dataclass(frozen=True, slots=True)
class DeleteImpact:
    """Exactly what deleting one user would affect, read from the device.

    ``SECURITY.md`` requires a destructive operation to identify its target and
    be confirmed. This is assembled from a live read so an operator confirms
    against what is actually on the clock, not against a stale list.
    """

    user: DeviceUser
    attendance_records: int
    device_name: str

    @property
    def warning(self) -> str:
        """The warning text an operator must read before confirming."""
        if self.attendance_records:
            history = (
                f"{self.attendance_records} attendance record(s) for this user are "
                "stored on the device. Deleting the user does NOT delete them, and "
                "once the user is gone those punches may no longer show a name."
            )
        else:
            history = (
                "No attendance records for this user were found on the device, but "
                "any that exist elsewhere are not deleted by this action."
            )
        return (
            f"Delete {self.user.display_name} (user ID {self.user.user_id}, device "
            f"UID {self.user.device_uid}) from {self.device_name}?\n\n"
            f"{history}\n\nThis cannot be undone from this application."
        )

    def as_rows(self) -> list[tuple[str, str]]:
        return [
            ("User ID", self.user.user_id),
            ("Name", self.user.display_name),
            ("Privilege", self.user.privilege_label),
            ("Device UID", str(self.user.device_uid)),
            ("PIN set", "Yes" if self.user.has_credential_data else "No"),
            ("Attendance records on device", str(self.attendance_records)),
        ]


class UserService:
    """Lists and manages the users stored on a device."""

    def __init__(
        self,
        devices: DeviceService,
        audit: AuditService,
        *,
        writes_enabled: bool = False,
        credential_writes_enabled: bool = False,
    ) -> None:
        self._devices = devices
        self._audit = audit
        self._writes_enabled = writes_enabled
        self._credential_writes_enabled = credential_writes_enabled and writes_enabled

    # -- availability ---------------------------------------------------------

    def write_availability(self) -> WriteAvailability:
        """Whether writes are enabled for this installation, and why not."""
        if not self._writes_enabled:
            return WriteAvailability(
                users=False,
                credentials=False,
                reason=(
                    "Device writing is switched off. No NG-MB1 has yet accepted a "
                    "record from this application's 120-byte write path, so it stays "
                    "off until an administrator enables it deliberately "
                    "(CLOCKMANAGER_ENABLE_DEVICE_WRITES=1) and proves it with a "
                    "disposable test user."
                ),
            )
        if not self._credential_writes_enabled:
            return WriteAvailability(
                users=True,
                credentials=False,
                reason=(
                    "PIN writing is switched off. The credential region's layout is "
                    "unverified, so setting or clearing a PIN requires "
                    "CLOCKMANAGER_ENABLE_CREDENTIAL_WRITES=1 and a disposable test "
                    "user."
                ),
            )
        return WriteAvailability(users=True, credentials=True)

    # -- reads ----------------------------------------------------------------

    def list_users(self, profile: DeviceProfile) -> list[DeviceUser]:
        """Read every user from the device, credential contents excluded."""
        return self._devices.read_users(profile)

    def find_user(self, profile: DeviceProfile, device_uid: int) -> DeviceUser | None:
        """Read one user by device UID, or ``None`` if it is not there."""
        for user in self.list_users(profile):
            if user.device_uid == device_uid:
                return user
        return None

    def describe_delete(self, profile: DeviceProfile, device_uid: int) -> DeleteImpact:
        """Read the device and describe exactly what a delete would affect."""
        with self._devices.connected(profile) as device:
            user = next(
                (item for item in device.get_users() if item.device_uid == device_uid), None
            )
            if user is None:
                raise ClockManagerError(
                    f"No user with device UID {device_uid} is on the device any more. "
                    "Refresh the user list."
                )
            attendance = sum(
                1 for event in device.get_attendance() if event.user_id == user.user_id
            )

        return DeleteImpact(
            user=user,
            attendance_records=attendance,
            device_name=profile.name,
        )

    # -- writes ---------------------------------------------------------------

    def save_user(
        self,
        profile: DeviceProfile,
        draft: UserDraft,
        *,
        requester_role: Role | str | None = None,
    ) -> UserWriteOutcome:
        """Create or update one user on the device, auditing the attempt.

        The device adapter performs the verified write sequence; this method
        adds the policy decisions around it and records the audit entry.

        ``requester_role`` enforces PHASE 07 roles: only an admin may write
        device users. ``None`` keeps the legacy path for callers without an
        interactive identity (tests, headless); the GUI always passes the
        logged-in role.
        """
        if requester_role is not None:
            require(requester_role, Permission.MANAGE_DEVICE_USERS)
        draft = draft.normalised()
        action = AuditAction.USER_UPDATE if not draft.is_new else AuditAction.USER_CREATE

        availability = self.write_availability()
        if not availability.users:
            self._record(action, AuditOutcome.REFUSED, profile, draft, availability.reason)
            raise DeviceCapabilityError(availability.reason)
        if draft.changes_credential and not availability.credentials:
            self._record(action, AuditOutcome.REFUSED, profile, draft, availability.reason)
            raise DeviceCapabilityError(availability.reason)

        problems = draft.validate()
        if problems:
            message = " ".join(problems)
            self._record(action, AuditOutcome.REFUSED, profile, draft, message)
            raise ClockManagerError(message)

        try:
            with self._writable(profile) as device:
                outcome = device.apply_user_write(draft)
        except DeviceError as exc:
            self._record(action, AuditOutcome.FAILED, profile, draft, str(exc))
            raise

        detail = "; ".join(str(change) for change in outcome.changes) or "No fields changed."
        self._audit.record(
            action,
            AuditOutcome.SUCCEEDED,
            detail=detail,
            device_id=profile.device_id,
            device_name=profile.name,
            target=outcome.user.user_id,
            target_uid=outcome.user.device_uid,
        )
        return outcome

    def delete_user(
        self,
        profile: DeviceProfile,
        device_uid: int,
        *,
        confirmed: bool,
        requester_role: Role | str | None = None,
    ) -> DeviceUser:
        """Delete one user from the device, auditing the attempt.

        ``confirmed`` must be ``True``. It is a required argument rather than a
        default so a caller cannot delete a user by forgetting to ask.
        ``requester_role`` enforces PHASE 07 roles (admin only); ``None``
        keeps the legacy path for callers without an interactive identity.
        """
        if requester_role is not None:
            require(requester_role, Permission.MANAGE_DEVICE_USERS)
        availability = self.write_availability()
        if not availability.users:
            self._record_delete(AuditOutcome.REFUSED, profile, device_uid, availability.reason)
            raise DeviceCapabilityError(availability.reason)

        if not confirmed:
            message = (
                "Deleting a user requires explicit confirmation. Nothing was sent to the device."
            )
            self._record_delete(AuditOutcome.REFUSED, profile, device_uid, message)
            raise ClockManagerError(message)

        try:
            with self._writable(profile) as device:
                deleted = device.delete_user(device_uid)
        except DeviceError as exc:
            self._record_delete(AuditOutcome.FAILED, profile, device_uid, str(exc))
            raise

        self._audit.record(
            AuditAction.USER_DELETE,
            AuditOutcome.SUCCEEDED,
            detail=(
                f"Deleted {deleted.display_name} (user ID {deleted.user_id}, "
                f"privilege {deleted.privilege_label}). Attendance history on the "
                "device was not deleted."
            ),
            device_id=profile.device_id,
            device_name=profile.name,
            target=deleted.user_id,
            target_uid=deleted.device_uid,
        )
        return deleted

    # -- internals ------------------------------------------------------------

    def _writable(self, profile: DeviceProfile) -> _ConnectedWritableDevice:
        device = self._devices.build(
            profile,
            allow_writes=True,
            allow_credential_writes=self._credential_writes_enabled,
        )
        if not isinstance(device, WritableUserDevice):  # pragma: no cover - defensive
            raise DeviceCapabilityError(
                f"The adapter for {profile.name!r} does not support user writing."
            )
        device.capabilities.require(Capability.WRITE_USERS)
        return _ConnectedWritableDevice(device)

    def _record(
        self,
        action: AuditAction,
        outcome: AuditOutcome,
        profile: DeviceProfile,
        draft: UserDraft,
        detail: str,
    ) -> None:
        changes = "; ".join(str(change) for change in describe_changes(None, draft))
        self._audit.record(
            action,
            outcome,
            detail=f"{detail} Requested: {changes}" if detail else changes,
            device_id=profile.device_id,
            device_name=profile.name,
            target=draft.user_id,
            target_uid=draft.device_uid,
        )

    def _record_delete(
        self,
        outcome: AuditOutcome,
        profile: DeviceProfile,
        device_uid: int,
        detail: str,
    ) -> None:
        self._audit.record(
            AuditAction.USER_DELETE,
            outcome,
            detail=detail,
            device_id=profile.device_id,
            device_name=profile.name,
            target_uid=device_uid,
        )


class _ConnectedWritableDevice:
    """Connects on entry and always disconnects on exit."""

    def __init__(self, device: WritableUserDevice) -> None:
        self._device = device

    def __enter__(self) -> WritableUserDevice:
        self._device.connect()
        return self._device

    def __exit__(self, *_exc: object) -> None:
        self._device.disconnect()
