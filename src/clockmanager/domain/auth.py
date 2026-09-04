"""Application roles and permissions (PHASE 07).

Three local-account roles separate admin/office access:

* ``ADMIN`` — every control, including device settings, diagnostics,
  device writes (still gated by the PHASE 03 operator switches) and
  account administration.
* ``OFFICE_STAFF`` — normal office workflows: employees, timesheets,
  reports, attendance sync and live capture. No device settings, no
  diagnostics, no account administration, no device user writes.
* ``VIEWER`` — read-only. No mutation of any kind.

This module is pure Python: no PySide6, no SQLAlchemy, no I/O. Both the
service layer (which refuses what a role may not do) and the GUI (which
hides what a role may not see) decide from this one matrix, so the two
can never disagree about what a role means.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from clockmanager.errors import SecurityError

__all__ = [
    "ROLE_PERMISSIONS",
    "Permission",
    "Role",
    "can",
    "normalise_role",
    "require",
]


class Role(StrEnum):
    """Local-account roles. Stored as their ``value`` in ``app_users.role``."""

    ADMIN = "admin"
    OFFICE_STAFF = "office_staff"
    VIEWER = "viewer"

    @property
    def label(self) -> str:
        """Human-readable name for display."""
        return {
            Role.ADMIN: "Admin",
            Role.OFFICE_STAFF: "Office staff",
            Role.VIEWER: "Viewer",
        }[self]


class Permission(StrEnum):
    """One thing a role may or may not do. Reads need no permission."""

    #: Create/disable accounts, change roles, reset passwords.
    MANAGE_ACCOUNTS = "accounts.manage"
    #: Save or remove stored device connection profiles.
    MANAGE_DEVICE_SETTINGS = "devices.settings"
    #: Create, update or delete users on a device.
    MANAGE_DEVICE_USERS = "devices.users"
    #: Run protocol/diagnostic checks and read raw detail.
    VIEW_DIAGNOSTICS = "diagnostics.view"
    #: Create, update, (de)activate and map employees.
    MANAGE_EMPLOYEES = "employees.manage"
    #: Run attendance sync (manual or recovery) against a device.
    SYNC_ATTENDANCE = "attendance.sync"
    #: Run live capture and store its punches.
    LIVE_CAPTURE = "attendance.live"
    #: Read the append-only audit log.
    VIEW_AUDIT = "audit.view"
    #: Generate reports and export them. Allowed for every role: an export
    #: mutates nothing but its own audited ``report.export`` entry.
    EXPORT_REPORTS = "reports.export"


#: Exactly what each role may do. Reads are not permissions: every role may
#: view the dashboard, users, attendance, employees, timesheets and reports.
ROLE_PERMISSIONS: Final[dict[Role, frozenset[Permission]]] = {
    Role.ADMIN: frozenset(
        {
            Permission.MANAGE_ACCOUNTS,
            Permission.MANAGE_DEVICE_SETTINGS,
            Permission.MANAGE_DEVICE_USERS,
            Permission.VIEW_DIAGNOSTICS,
            Permission.MANAGE_EMPLOYEES,
            Permission.SYNC_ATTENDANCE,
            Permission.LIVE_CAPTURE,
            Permission.VIEW_AUDIT,
            Permission.EXPORT_REPORTS,
        }
    ),
    Role.OFFICE_STAFF: frozenset(
        {
            Permission.MANAGE_EMPLOYEES,
            Permission.SYNC_ATTENDANCE,
            Permission.LIVE_CAPTURE,
            Permission.VIEW_AUDIT,
            Permission.EXPORT_REPORTS,
        }
    ),
    Role.VIEWER: frozenset({Permission.EXPORT_REPORTS}),
}


def normalise_role(role: Role | str) -> Role:
    """Parse ``role`` strictly; unknown values are refused, never guessed."""
    if isinstance(role, Role):
        return role
    try:
        return Role(str(role).strip().lower())
    except ValueError as exc:
        raise SecurityError(f"Unknown role {role!r}.") from exc


def can(role: Role | str, permission: Permission) -> bool:
    """Return whether ``role`` holds ``permission``."""
    return permission in ROLE_PERMISSIONS[normalise_role(role)]


def require(role: Role | str, permission: Permission) -> Role:
    """Return the normalised role, or raise when it lacks ``permission``.

    The message names the permission and the role label only. It never
    carries a credential, a user list or any other sensitive data.
    """
    resolved = normalise_role(role)
    if permission not in ROLE_PERMISSIONS[resolved]:
        raise SecurityError(f"Role {resolved.label!r} is not permitted to {permission.value!r}.")
    return resolved
