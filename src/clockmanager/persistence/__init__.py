"""Persistence layer: SQLAlchemy ORM over SQLite (PostgreSQL-ready).

The GUI never touches this layer directly; it goes through
:mod:`clockmanager.services`.
"""

from __future__ import annotations

from clockmanager.persistence.database import Database, create_database, initialise_database
from clockmanager.persistence.models import (
    SCHEMA_VERSION,
    AttendanceEventRecord,
    AuditEventRecord,
    Base,
    DeviceRecord,
    DeviceUserRecord,
    SchemaInfo,
    SyncHistoryRecord,
)
from clockmanager.persistence.repositories import (
    AttendanceRepository,
    AuditRepository,
    DeviceRepository,
    SyncHistoryRepository,
)

__all__ = [
    "SCHEMA_VERSION",
    "AttendanceEventRecord",
    "AttendanceRepository",
    "AuditEventRecord",
    "AuditRepository",
    "Base",
    "Database",
    "DeviceRecord",
    "DeviceRepository",
    "DeviceUserRecord",
    "SchemaInfo",
    "SyncHistoryRecord",
    "SyncHistoryRepository",
    "create_database",
    "initialise_database",
]
