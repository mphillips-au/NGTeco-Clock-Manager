"""Repository boundary over the ORM.

Services depend on repositories, not on SQLAlchemy queries, so the store can be
swapped (SQLite now, PostgreSQL for the future NAS deployment) without changing
business logic. PHASE 00 provided the device repository; PHASE 03 adds the
audit repository. User and attendance repositories arrive with the phases that
need them.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from clockmanager.domain.models import DeviceIdentity
from clockmanager.persistence.models import AuditEventRecord, DeviceRecord

__all__ = ["AuditRepository", "DeviceRepository"]


class DeviceRepository:
    """Read/write access to known attendance devices."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_all(self) -> list[DeviceRecord]:
        statement = select(DeviceRecord).order_by(DeviceRecord.name)
        return list(self._session.execute(statement).scalars().all())

    def count(self) -> int:
        statement = select(func.count()).select_from(DeviceRecord)
        return int(self._session.execute(statement).scalar_one())

    def get_by_name(self, name: str) -> DeviceRecord | None:
        statement = select(DeviceRecord).where(DeviceRecord.name == name)
        return self._session.execute(statement).scalar_one_or_none()

    def add(self, identity: DeviceIdentity) -> DeviceRecord:
        """Register a new device from its reported identity."""
        record = DeviceRecord(
            name=identity.name,
            serial_number=identity.serial_number,
            model=identity.model,
            platform=identity.platform,
            firmware_version=identity.firmware_version,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def to_identity(self, record: DeviceRecord) -> DeviceIdentity:
        """Convert a stored row back into the stable domain model."""
        return DeviceIdentity(
            name=record.name,
            serial_number=record.serial_number,
            model=record.model,
            platform=record.platform,
            firmware_version=record.firmware_version,
        )


class AuditRepository:
    """Append-only access to the audit log.

    There is deliberately no update or delete method. An audit log that the
    application can rewrite is not evidence of anything.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: AuditEventRecord) -> AuditEventRecord:
        self._session.add(record)
        self._session.flush()
        return record

    def recent(self, *, limit: int = 200) -> list[AuditEventRecord]:
        """The most recent entries, newest first."""
        statement = (
            select(AuditEventRecord)
            .order_by(AuditEventRecord.occurred_at.desc(), AuditEventRecord.id.desc())
            .limit(limit)
        )
        return list(self._session.execute(statement).scalars().all())

    def count(self) -> int:
        statement = select(func.count()).select_from(AuditEventRecord)
        return int(self._session.execute(statement).scalar_one())
