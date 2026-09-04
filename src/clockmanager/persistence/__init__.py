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
)

__all__ = [
    "SCHEMA_VERSION",
    "AttendanceEventRecord",
    "AuditEventRecord",
    "Base",
    "Database",
    "DeviceRecord",
    "DeviceUserRecord",
    "SchemaInfo",
    "create_database",
    "initialise_database",
]
