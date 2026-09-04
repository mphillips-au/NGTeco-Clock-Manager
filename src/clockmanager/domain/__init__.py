"""Domain models and business rules.

This layer is pure Python: no PySide6, no SQLAlchemy, no sockets.
"""

from __future__ import annotations

from clockmanager.domain.models import (
    AttendanceEvent,
    DeviceIdentity,
    DeviceInfo,
    DeviceUser,
    Privilege,
    PunchDirection,
    describe_privilege,
    describe_punch,
)
from clockmanager.domain.users import (
    CredentialAction,
    UserChange,
    UserDraft,
    UserWriteOutcome,
    describe_changes,
)

__all__ = [
    "AttendanceEvent",
    "CredentialAction",
    "DeviceIdentity",
    "DeviceInfo",
    "DeviceUser",
    "Privilege",
    "PunchDirection",
    "UserChange",
    "UserDraft",
    "UserWriteOutcome",
    "describe_changes",
    "describe_privilege",
    "describe_punch",
]
