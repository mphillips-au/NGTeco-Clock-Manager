"""Repository boundary over the ORM.

Services depend on repositories, not on SQLAlchemy queries, so the store can be
swapped (SQLite now, PostgreSQL for the future NAS deployment) without changing
business logic. PHASE 00 provided the device repository; PHASE 03 adds the
audit repository. User and attendance repositories arrive with the phases that
need them.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clockmanager.domain.models import DeviceIdentity
from clockmanager.persistence.models import (
    AttendanceEventRecord,
    AuditEventRecord,
    DeviceRecord,
    SyncHistoryRecord,
    utc_now,
)

__all__ = [
    "AttendanceRepository",
    "AuditRepository",
    "DeviceRepository",
    "SyncHistoryRepository",
]


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


class AttendanceRepository:
    """Stored attendance punches with duplicate-safe inserts.

    Duplicate prevention has two layers: the in-memory plan (event keys and
    the natural key) skips what is already stored, and per-row savepoints
    turn a concurrent insert of the same punch into a skipped duplicate
    rather than a failed sync.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def keys_for_device(self, device_id: int) -> set[str]:
        """Event keys already stored for ``device_id`` (empty keys ignored)."""
        statement = select(AttendanceEventRecord.event_key).where(
            AttendanceEventRecord.device_id == device_id,
            AttendanceEventRecord.event_key != "",
        )
        return {str(key) for key in self._session.execute(statement).scalars().all()}

    def natural_keys_for_device(self, device_id: int) -> set[tuple[str, datetime, int, int]]:
        """Natural keys already stored, normalised for SQLite round trips.

        SQLite returns naive datetimes; the stored wall-clock seconds are what
        matter for identity, so microseconds are dropped and any tzinfo is
        ignored, matching :func:`clockmanager.sync.keys.normalise_for_key`.
        """
        statement = select(
            AttendanceEventRecord.user_id,
            AttendanceEventRecord.occurred_at,
            AttendanceEventRecord.punch,
            AttendanceEventRecord.status,
        ).where(AttendanceEventRecord.device_id == device_id)
        known: set[tuple[str, datetime, int, int]] = set()
        for user_id, occurred_at, punch, status in self._session.execute(statement).all():
            moment = occurred_at.replace(microsecond=0, tzinfo=None)
            known.add((str(user_id), moment, int(punch), int(status)))
        return known

    def try_insert(
        self,
        *,
        device_id: int,
        device_uid: int | None,
        user_id: str,
        occurred_at: datetime,
        punch: int,
        status: int,
        received_at: datetime,
        source: str,
        event_key: str,
        employee_name: str | None,
    ) -> bool:
        """Insert one punch; return ``False`` when it was already stored.

        Uses a savepoint so a duplicate key violation rolls back only this
        row, not the whole sync transaction.
        """
        record = AttendanceEventRecord(
            device_id=device_id,
            device_uid=device_uid,
            user_id=user_id,
            occurred_at=occurred_at,
            punch=punch,
            status=status,
            received_at=received_at,
            source=source,
            event_key=event_key,
            employee_name=employee_name,
        )
        nested = self._session.begin_nested()
        try:
            self._session.add(record)
            self._session.flush()
        except IntegrityError:
            nested.rollback()
            return False
        else:
            nested.commit()
            return True

    def list_for_device(self, device_id: int, *, limit: int = 1000) -> list[AttendanceEventRecord]:
        """Stored punches for one device, newest first."""
        statement = (
            select(AttendanceEventRecord)
            .where(AttendanceEventRecord.device_id == device_id)
            .order_by(AttendanceEventRecord.occurred_at.desc(), AttendanceEventRecord.id.desc())
            .limit(limit)
        )
        return list(self._session.execute(statement).scalars().all())

    def recent(self, *, limit: int = 1000) -> list[AttendanceEventRecord]:
        """Stored punches across all devices, newest first."""
        statement = (
            select(AttendanceEventRecord)
            .order_by(AttendanceEventRecord.occurred_at.desc(), AttendanceEventRecord.id.desc())
            .limit(limit)
        )
        return list(self._session.execute(statement).scalars().all())

    def count_for_device(self, device_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(AttendanceEventRecord)
            .where(AttendanceEventRecord.device_id == device_id)
        )
        return int(self._session.execute(statement).scalar_one())

    def count(self) -> int:
        statement = select(func.count()).select_from(AttendanceEventRecord)
        return int(self._session.execute(statement).scalar_one())

    def max_occurred_at(self, device_id: int) -> datetime | None:
        """Newest device timestamp stored for ``device_id``, if any."""
        statement = select(func.max(AttendanceEventRecord.occurred_at)).where(
            AttendanceEventRecord.device_id == device_id
        )
        value = self._session.execute(statement).scalar_one_or_none()
        return None if value is None else value


class SyncHistoryRepository:
    """Append-only sync run history.

    Like the audit log, runs are written once and never edited or deleted by
    the application. ``device_id`` is a plain value so removing a device
    profile cannot erase its sync history.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: SyncHistoryRecord) -> SyncHistoryRecord:
        self._session.add(record)
        self._session.flush()
        return record

    def recent(self, *, limit: int = 200) -> list[SyncHistoryRecord]:
        statement = (
            select(SyncHistoryRecord)
            .order_by(SyncHistoryRecord.started_at.desc(), SyncHistoryRecord.id.desc())
            .limit(limit)
        )
        return list(self._session.execute(statement).scalars().all())

    def recent_for_device(self, device_id: int, *, limit: int = 50) -> list[SyncHistoryRecord]:
        statement = (
            select(SyncHistoryRecord)
            .where(SyncHistoryRecord.device_id == device_id)
            .order_by(SyncHistoryRecord.started_at.desc(), SyncHistoryRecord.id.desc())
            .limit(limit)
        )
        return list(self._session.execute(statement).scalars().all())

    def latest_success_for_device(self, device_id: int) -> SyncHistoryRecord | None:
        statement = (
            select(SyncHistoryRecord)
            .where(
                SyncHistoryRecord.device_id == device_id,
                SyncHistoryRecord.outcome == "success",
            )
            .order_by(SyncHistoryRecord.started_at.desc(), SyncHistoryRecord.id.desc())
            .limit(1)
        )
        return self._session.execute(statement).scalar_one_or_none()

    def latest_for_device(self, device_id: int) -> SyncHistoryRecord | None:
        statement = (
            select(SyncHistoryRecord)
            .where(SyncHistoryRecord.device_id == device_id)
            .order_by(SyncHistoryRecord.started_at.desc(), SyncHistoryRecord.id.desc())
            .limit(1)
        )
        return self._session.execute(statement).scalar_one_or_none()

    def count(self) -> int:
        statement = select(func.count()).select_from(SyncHistoryRecord)
        return int(self._session.execute(statement).scalar_one())

    def record_run(
        self,
        *,
        device_id: int | None,
        device_name: str | None,
        mode: str,
        source: str,
        events_seen: int,
        events_new: int,
        events_duplicate: int,
        outcome: str,
        error: str = "",
    ) -> SyncHistoryRecord:
        """Append one finished run row stamped with the current UTC time."""
        now = utc_now()
        record = SyncHistoryRecord(
            device_id=device_id,
            device_name=device_name,
            started_at=now,
            finished_at=now,
            mode=mode,
            source=source,
            events_seen=events_seen,
            events_new=events_new,
            events_duplicate=events_duplicate,
            outcome=outcome,
            error=error[:2000],
        )
        self._session.add(record)
        self._session.flush()
        return record
