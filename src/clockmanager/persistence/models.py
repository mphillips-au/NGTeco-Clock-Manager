"""SQLAlchemy ORM models.

Multi-device support is designed in from the start (``ARCHITECTURE.md``):
every user and every attendance event belongs to an identified source device,
even while only one NG-MB1 is supported.

No credential, card identifier or biometric template is stored. If a later
phase proves such storage is required, it must be specified in ``SECURITY.md``
first. That applies to the audit log too: it records that a credential changed,
never what it changed to.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

#: Kept in sync with the protocol layer's default without importing it, so
#: persistence stays independent of the device layer.
DEFAULT_DEVICE_PORT: Final = 4370

__all__ = [
    "SCHEMA_VERSION",
    "AttendanceEventRecord",
    "AuditEventRecord",
    "Base",
    "DeviceRecord",
    "DeviceUserRecord",
    "SchemaInfo",
    "utc_now",
]

#: Bumped whenever the schema changes. Every bump needs a matching entry in
#: :data:`clockmanager.persistence.migrations.MIGRATIONS`.
SCHEMA_VERSION: Final = 3


def utc_now() -> datetime:
    """Timezone-aware current time, used for all stored timestamps."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


class SchemaInfo(Base):
    """Key/value metadata about the database itself."""

    __tablename__ = "schema_info"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"SchemaInfo(key={self.key!r}, value={self.value!r})"


class DeviceRecord(Base):
    """A known attendance device.

    Connection settings (host, port, credentials) are added in PHASE 02; no
    device address is stored or defaulted in source.
    """

    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("name", name="uq_devices_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    serial_number: Mapped[str | None] = mapped_column(String(120), unique=True, default=None)
    model: Mapped[str | None] = mapped_column(String(64), default=None)
    platform: Mapped[str | None] = mapped_column(String(64), default=None)
    firmware_version: Mapped[str | None] = mapped_column(String(64), default=None)

    # -- connection settings (schema v2, PHASE 02) ---------------------------
    #: No address is ever defaulted in source (AGENTS.md), so this is nullable
    #: until an operator configures it.
    host: Mapped[str | None] = mapped_column(String(255), default=None)
    port: Mapped[int] = mapped_column(Integer, default=DEFAULT_DEVICE_PORT, nullable=False)
    #: SENSITIVE. Never logged, exported or shown unmasked (SECURITY.md).
    #: Stored unencrypted at rest; the data directory's OS permissions are the
    #: current control. Encryption at rest is a documented open item.
    communication_password: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    auto_reconnect: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    users: Mapped[list[DeviceUserRecord]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    attendance_events: Mapped[list[AttendanceEventRecord]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"DeviceRecord(id={self.id!r}, name={self.name!r})"


class DeviceUserRecord(Base):
    """A user as it exists on a specific device.

    ``device_uid`` is the on-device UID; ``user_id`` is the human-facing ID
    from bytes 96:120 of the MB1 120-byte record.
    """

    __tablename__ = "device_users"
    __table_args__ = (
        UniqueConstraint("device_id", "device_uid", name="uq_device_users_device_uid"),
        UniqueConstraint("device_id", "user_id", name="uq_device_users_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_uid: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    first_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    last_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: Raw device privilege byte. 0 = Employee, 14 = Admin; other values are
    #: preserved verbatim and reported as unknown.
    privilege: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    device: Mapped[DeviceRecord] = relationship(back_populates="users")

    def __repr__(self) -> str:
        return f"DeviceUserRecord(device_id={self.device_id!r}, user_id={self.user_id!r})"


class AttendanceEventRecord(Base):
    """A single attendance punch attributed to its source device.

    The unique constraint is the duplicate-detection key for synchronisation
    (PHASE 04): re-reading the same device history must not create duplicates.
    """

    __tablename__ = "attendance_events"
    __table_args__ = (
        UniqueConstraint(
            "device_id",
            "user_id",
            "occurred_at",
            "punch",
            "status",
            name="uq_attendance_events_natural_key",
        ),
        Index("ix_attendance_events_device_time", "device_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_uid: Mapped[int | None] = mapped_column(Integer, default=None)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Raw device punch value. 0 = IN, 1 = OUT (verified); never inferred from
    #: ``status``.
    punch: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Raw device status metadata, preserved verbatim and never interpreted.
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )

    device: Mapped[DeviceRecord] = relationship(back_populates="attendance_events")

    def __repr__(self) -> str:
        return (
            f"AttendanceEventRecord(device_id={self.device_id!r}, "
            f"user_id={self.user_id!r}, occurred_at={self.occurred_at!r})"
        )


class AuditEventRecord(Base):
    """An append-only record of an action taken against a device or its data.

    ``SECURITY.md`` requires every device write to record an audit event. Rows
    are written once and never updated or deleted by the application.

    ``detail`` is a human-readable description of what changed. It is produced
    by :func:`clockmanager.domain.users.describe_changes`, which reports a PIN
    as an action ("Set a new PIN") and never as a value, so no credential can
    reach this table.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_occurred_at", "occurred_at"),
        Index("ix_audit_events_action", "action"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    #: Who performed the action. Roles arrive in PHASE 07; until then this is
    #: the operating-system account that ran the application.
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    #: What was attempted, e.g. ``user.create``, ``user.update``, ``user.delete``.
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    #: ``succeeded``, ``failed`` or ``refused``. A refused or failed attempt is
    #: recorded too: an audit log that only shows successes is not an audit log.
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The device the action targeted, kept as a plain value rather than a
    #: foreign key so removing a device cannot erase its audit history.
    device_id: Mapped[int | None] = mapped_column(Integer, default=None)
    device_name: Mapped[str | None] = mapped_column(String(120), default=None)
    #: The subject of the action, e.g. a device user ID.
    target: Mapped[str | None] = mapped_column(String(120), default=None)
    target_uid: Mapped[int | None] = mapped_column(Integer, default=None)
    #: What changed, or why the action failed. Never contains a credential.
    detail: Mapped[str] = mapped_column(String(2000), default="", nullable=False)

    def __repr__(self) -> str:
        return (
            f"AuditEventRecord(action={self.action!r}, outcome={self.outcome!r}, "
            f"target={self.target!r})"
        )
