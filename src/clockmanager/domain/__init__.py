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
