"""User management domain rules.

Pure business rules for creating, changing and deleting a device user. No
sockets, no SQLAlchemy, no PySide6 — the GUI and the future headless service
validate against exactly the same rules.

Field sizes come from the verified 120-byte NG-MB1 record and are expressed in
**bytes**, not characters: a name is validated against what it encodes to, so a
form that accepts an accented name cannot produce a record the device rejects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from clockmanager.domain.models import DeviceUser, Privilege, describe_privilege

__all__ = [
    "FIRST_NAME_MAX_BYTES",
    "LAST_NAME_MAX_BYTES",
    "MAX_DEVICE_UID",
    "PASSWORD_MAX_BYTES",
    "USER_ID_MAX_BYTES",
    "CredentialAction",
    "UserChange",
    "UserDraft",
    "UserWriteOutcome",
    "describe_changes",
]

#: Byte budgets of the verified MB1 record fields.
FIRST_NAME_MAX_BYTES: Final = 24
LAST_NAME_MAX_BYTES: Final = 37
USER_ID_MAX_BYTES: Final = 24
#: The candidate credential field is 8 bytes (UNVERIFIED, see ``PROTOCOL.md``).
PASSWORD_MAX_BYTES: Final = 8
#: The device stores the UID in two bytes.
MAX_DEVICE_UID: Final = 0xFFFF


class CredentialAction(StrEnum):
    """What a write should do with the user's PIN/password.

    The device's credential region has no known internal layout, so
    :data:`PRESERVE` is the default for every update: changing a name must
    never silently destroy a PIN.
    """

    PRESERVE = "preserve"
    CLEAR = "clear"
    SET = "set"

    @property
    def label(self) -> str:
        if self is CredentialAction.PRESERVE:
            return "Leave the PIN unchanged"
        if self is CredentialAction.CLEAR:
            return "Remove the PIN"
        return "Set a new PIN"


def _encoded_length(value: str, encoding: str = "utf-8") -> int:
    return len(value.encode(encoding, errors="strict"))


@dataclass(frozen=True, slots=True)
class UserDraft:
    """A validated intent to create or update one device user.

    ``password`` is a secret. It is excluded from ``repr`` so it cannot reach a
    log, a traceback or a diagnostic dump (``SECURITY.md``), it is never
    persisted, and it lives only for the duration of one write.
    """

    user_id: str
    first_name: str = ""
    last_name: str = ""
    privilege: int = int(Privilege.EMPLOYEE)
    #: ``None`` means "create a new user and let the device UID be assigned".
    device_uid: int | None = None
    credential_action: CredentialAction = CredentialAction.PRESERVE
    password: str | None = field(default=None, repr=False)

    @property
    def is_new(self) -> bool:
        return self.device_uid is None

    @property
    def display_name(self) -> str:
        full_name = f"{self.first_name} {self.last_name}".strip()
        return full_name or self.user_id

    @property
    def privilege_label(self) -> str:
        return describe_privilege(self.privilege)

    @property
    def changes_credential(self) -> bool:
        return self.credential_action is not CredentialAction.PRESERVE

    def validate(self, *, encoding: str = "utf-8") -> list[str]:
        """Return human-readable problems; empty means the draft is writable."""
        problems: list[str] = []

        user_id = self.user_id.strip()
        if not user_id:
            problems.append("User ID is required.")
        elif _encoded_length(user_id, encoding) > USER_ID_MAX_BYTES:
            problems.append(f"User ID must fit in {USER_ID_MAX_BYTES} bytes on the device.")

        if _encoded_length(self.first_name, encoding) > FIRST_NAME_MAX_BYTES:
            problems.append(f"First name must fit in {FIRST_NAME_MAX_BYTES} bytes on the device.")
        if _encoded_length(self.last_name, encoding) > LAST_NAME_MAX_BYTES:
            problems.append(f"Last name must fit in {LAST_NAME_MAX_BYTES} bytes on the device.")

        for label, value in (
            ("User ID", user_id),
            ("First name", self.first_name),
            ("Last name", self.last_name),
        ):
            if "\x00" in value:
                problems.append(f"{label} must not contain a NUL character.")

        if Privilege.from_raw(self.privilege) is None:
            problems.append(
                f"Privilege {self.privilege} is not one of the values verified on this "
                "device model (0 Employee, 14 Admin)."
            )

        if self.device_uid is not None and not 0 <= self.device_uid <= MAX_DEVICE_UID:
            problems.append(f"Device UID must be between 0 and {MAX_DEVICE_UID}.")

        problems.extend(self._validate_credential(encoding))
        return problems

    def _validate_credential(self, encoding: str) -> list[str]:
        problems: list[str] = []
        if self.credential_action is CredentialAction.SET:
            if not self.password:
                problems.append("A PIN is required when setting a new PIN.")
            elif _encoded_length(self.password, encoding) > PASSWORD_MAX_BYTES:
                problems.append(f"PIN must fit in {PASSWORD_MAX_BYTES} bytes on the device.")
        elif self.password:
            problems.append(
                "A PIN was supplied but the credential action does not set one. "
                "Choose “Set a new PIN” or clear the field."
            )
        return problems

    def normalised(self) -> UserDraft:
        """A copy with surrounding whitespace stripped from every text field."""
        return UserDraft(
            user_id=self.user_id.strip(),
            first_name=self.first_name.strip(),
            last_name=self.last_name.strip(),
            privilege=self.privilege,
            device_uid=self.device_uid,
            credential_action=self.credential_action,
            password=self.password,
        )

    @classmethod
    def from_user(cls, user: DeviceUser) -> UserDraft:
        """Build an editable draft from a user already on the device."""
        return cls(
            user_id=user.user_id,
            first_name=user.first_name,
            last_name=user.last_name,
            privilege=user.privilege,
            device_uid=user.device_uid,
            credential_action=CredentialAction.PRESERVE,
        )


@dataclass(frozen=True, slots=True)
class UserChange:
    """One field that a write altered, safe to display and to audit."""

    field_name: str
    before: str
    after: str

    def __str__(self) -> str:
        return f"{self.field_name}: {self.before or '(empty)'} -> {self.after or '(empty)'}"


def describe_changes(before: DeviceUser | None, after: UserDraft) -> list[UserChange]:
    """Describe what a write would alter, for confirmation and for the audit log.

    The PIN is reported as an action only. Neither the old nor the new value is
    ever included (``SECURITY.md``).
    """
    changes: list[UserChange] = []
    if before is None:
        changes.append(UserChange("User", "(does not exist)", after.display_name))
        changes.append(UserChange("User ID", "", after.user_id))
        changes.append(UserChange("Privilege", "", after.privilege_label))
        if after.changes_credential:
            # "PIN change" rather than "PIN": the redaction filter treats
            # "pin: <word>" as a credential and would mask the description.
            changes.append(UserChange("PIN change", "none", after.credential_action.label))
        return changes

    for field_name, old, new in (
        ("User ID", before.user_id, after.user_id),
        ("First name", before.first_name, after.first_name),
        ("Last name", before.last_name, after.last_name),
        ("Privilege", before.privilege_label, after.privilege_label),
    ):
        if old != new:
            changes.append(UserChange(field_name, old, new))

    if after.changes_credential:
        changes.append(
            UserChange(
                "PIN change",
                "previously set" if before.has_credential_data else "previously not set",
                after.credential_action.label,
            )
        )
    return changes


@dataclass(frozen=True, slots=True)
class UserWriteOutcome:
    """The verified result of a completed user write.

    Only produced once the device has been read back and the stored record
    matches what was sent.
    """

    user: DeviceUser
    created: bool
    changes: tuple[UserChange, ...] = ()

    @property
    def summary(self) -> str:
        verb = "Created" if self.created else "Updated"
        return f"{verb} {self.user.display_name} (user ID {self.user.user_id}) on the device."
