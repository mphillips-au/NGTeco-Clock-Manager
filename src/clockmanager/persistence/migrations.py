"""Schema migrations.

PHASE 00 shipped schema version 1 with no way to move an existing database
forward. PHASE 02 adds persisted device connection settings, so the mechanism
is introduced here.

Migrations are small, ordered and forward-only. Each one runs inside a
transaction and is recorded in ``schema_info`` before the next begins, so an
interrupted upgrade resumes rather than half-applying.

Rules:

* Never drop or rewrite a column that holds user data.
* A migration must be safe to run against a database that already contains
  records; existing rows get defaults, never NULL where the model forbids it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, inspect, text

from clockmanager.diagnostics.logging_setup import get_logger

__all__ = ["MIGRATIONS", "Migration", "pending_migrations"]

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Migration:
    """One forward step between two schema versions."""

    version: int
    description: str
    apply: Callable[[Connection], None]


def _add_column_if_missing(
    connection: Connection, table: str, column: str, definition: str
) -> None:
    """Add a column, tolerating a database where it already exists.

    SQLite has no ``ADD COLUMN IF NOT EXISTS``, and a partially applied upgrade
    must be resumable.
    """
    existing = {info["name"] for info in inspect(connection).get_columns(table)}
    if column in existing:
        return
    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


def _migrate_to_2(connection: Connection) -> None:
    """Add device connection settings to ``devices`` (PHASE 02).

    Existing rows keep their identity and receive protocol defaults. ``host``
    is nullable because a device discovered from its own reports may not have a
    configured address yet, and no address is ever defaulted in source.
    """
    _add_column_if_missing(connection, "devices", "host", "VARCHAR(255)")
    _add_column_if_missing(connection, "devices", "port", "INTEGER NOT NULL DEFAULT 4370")
    _add_column_if_missing(
        connection, "devices", "communication_password", "INTEGER NOT NULL DEFAULT 0"
    )
    _add_column_if_missing(connection, "devices", "timeout_seconds", "FLOAT NOT NULL DEFAULT 10.0")
    _add_column_if_missing(connection, "devices", "auto_reconnect", "BOOLEAN NOT NULL DEFAULT 1")
    _add_column_if_missing(
        connection, "devices", "sync_interval_seconds", "INTEGER NOT NULL DEFAULT 300"
    )
    _add_column_if_missing(connection, "devices", "enabled", "BOOLEAN NOT NULL DEFAULT 1")


def _migrate_to_3(connection: Connection) -> None:
    """Add the append-only ``audit_events`` table (PHASE 03).

    Creating a new table cannot disturb existing rows, so this migration is
    additive only. ``IF NOT EXISTS`` keeps it resumable after an interruption.
    """
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                occurred_at DATETIME NOT NULL,
                actor VARCHAR(120) NOT NULL,
                action VARCHAR(64) NOT NULL,
                outcome VARCHAR(32) NOT NULL,
                device_id INTEGER,
                device_name VARCHAR(120),
                target VARCHAR(120),
                target_uid INTEGER,
                detail VARCHAR(2000) NOT NULL DEFAULT ''
            )
            """
        )
    )
    connection.execute(
        text("CREATE INDEX IF NOT EXISTS ix_audit_events_occurred_at ON audit_events (occurred_at)")
    )
    connection.execute(
        text("CREATE INDEX IF NOT EXISTS ix_audit_events_action ON audit_events (action)")
    )


def _migrate_to_4(connection: Connection) -> None:
    """Add PHASE 04 attendance sync columns and the sync history table.

    Additive only: existing attendance rows keep their data and are backfilled
    in place. ``received_at`` falls back to ``created_at`` for rows stored
    before it existed; ``source`` defaults to ``historical``; ``event_key``
    is backfilled in Python (it is a SHA-256 over the natural key, which
    SQLite cannot compute); ``employee_name`` stays NULL until the next sync
    snapshots it.
    """
    from clockmanager.sync.keys import build_event_key

    _add_column_if_missing(
        connection,
        "attendance_events",
        "received_at",
        "DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00'",
    )
    _add_column_if_missing(
        connection, "attendance_events", "source", "VARCHAR(32) NOT NULL DEFAULT 'historical'"
    )
    _add_column_if_missing(
        connection, "attendance_events", "event_key", "VARCHAR(128) NOT NULL DEFAULT ''"
    )
    _add_column_if_missing(connection, "attendance_events", "employee_name", "VARCHAR(128)")

    # Existing rows predate received_at: preserve their original store time.
    connection.execute(
        text(
            "UPDATE attendance_events SET received_at = created_at "
            "WHERE received_at = '1970-01-01 00:00:00'"
        )
    )

    # Backfill deterministic keys for rows stored before keys existed. The
    # natural-key unique constraint already guarantees these rows are distinct,
    # so the generated keys are distinct too.
    rows = connection.execute(
        text(
            "SELECT id, device_id, user_id, occurred_at, punch, status "
            "FROM attendance_events WHERE event_key = ''"
        )
    ).all()
    for row_id, device_id, user_id, occurred_at, punch, status in rows:
        moment = occurred_at
        if isinstance(moment, str):
            # SQLite stores datetimes as text; parse the common shapes.
            moment = _parse_stored_datetime(moment)
        key = build_event_key(
            device_id=int(device_id),
            user_id=str(user_id),
            occurred_at=moment,
            punch=int(punch),
            status=int(status),
        )
        connection.execute(
            text("UPDATE attendance_events SET event_key = :key WHERE id = :id"),
            {"key": key, "id": row_id},
        )

    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_events_event_key "
            "ON attendance_events (device_id, event_key)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_attendance_events_event_key "
            "ON attendance_events (device_id, event_key)"
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS sync_history (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER,
                device_name VARCHAR(120),
                started_at DATETIME NOT NULL,
                finished_at DATETIME,
                mode VARCHAR(32) NOT NULL,
                source VARCHAR(32) NOT NULL,
                events_seen INTEGER NOT NULL DEFAULT 0,
                events_new INTEGER NOT NULL DEFAULT 0,
                events_duplicate INTEGER NOT NULL DEFAULT 0,
                outcome VARCHAR(32) NOT NULL,
                error VARCHAR(2000) NOT NULL DEFAULT ''
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_sync_history_device_started "
            "ON sync_history (device_id, started_at)"
        )
    )


def _migrate_to_5(connection: Connection) -> None:
    """Add PHASE 05 business tables: employees, device links, pay schedules.

    Additive only: no existing table is touched. ``IF NOT EXISTS`` keeps the
    migration resumable after an interruption. Timesheets are derived on
    demand and intentionally have no table: raw attendance stays immutable.
    """
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                user_id VARCHAR(64) NOT NULL,
                first_name VARCHAR(64) NOT NULL DEFAULT '',
                last_name VARCHAR(64) NOT NULL DEFAULT '',
                active BOOLEAN NOT NULL DEFAULT 1,
                department VARCHAR(120) NOT NULL DEFAULT '',
                position VARCHAR(120) NOT NULL DEFAULT '',
                email VARCHAR(255) NOT NULL DEFAULT '',
                notes VARCHAR(2000) NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                CONSTRAINT uq_employees_user_id UNIQUE (user_id)
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS employee_device_links (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL REFERENCES employees (id) ON DELETE CASCADE,
                device_id INTEGER NOT NULL REFERENCES devices (id) ON DELETE CASCADE,
                user_id VARCHAR(64) NOT NULL,
                device_uid INTEGER,
                CONSTRAINT uq_employee_device UNIQUE (employee_id, device_id)
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_employee_links_device_user "
            "ON employee_device_links (device_id, user_id)"
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS pay_schedules (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(120) NOT NULL,
                schedule_type VARCHAR(32) NOT NULL,
                anchor_date DATE NOT NULL,
                timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
                day_cutoff_hour INTEGER NOT NULL DEFAULT 0,
                duplicate_interval_seconds INTEGER NOT NULL DEFAULT 60,
                max_shift_hours FLOAT NOT NULL DEFAULT 16.0,
                display_decimal BOOLEAN NOT NULL DEFAULT 0,
                daily_overtime_hours FLOAT,
                weekly_overtime_hours FLOAT,
                is_active BOOLEAN NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
    )


def _migrate_to_6(connection: Connection) -> None:
    """Add the PHASE 07 local-account ``app_users`` table.

    Additive only: no existing table is touched. ``IF NOT EXISTS`` keeps the
    migration resumable after an interruption. The table holds a salted
    password hash, never a password (``SECURITY.md``).
    """
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS app_users (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                username VARCHAR(64) NOT NULL,
                display_name VARCHAR(120) NOT NULL DEFAULT '',
                role VARCHAR(32) NOT NULL DEFAULT 'viewer',
                password_hash VARCHAR(256) NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                last_login_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                CONSTRAINT uq_app_users_username UNIQUE (username)
            )
            """
        )
    )


def _migrate_to_7(connection: Connection) -> None:
    """Add the PHASE 08 ``devices.last_seen_at`` column.

    Additive only: existing rows keep their data and read ``None`` (never
    seen) until the next successful connection. Resumable via
    ``_add_column_if_missing``.
    """
    _add_column_if_missing(connection, "devices", "last_seen_at", "DATETIME")


def _parse_stored_datetime(value: str) -> datetime:
    """Parse a SQLite-stored datetime string back into a datetime."""
    from datetime import datetime as _datetime

    text_value = value.strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return _datetime.strptime(text_value[: len(fmt)], fmt)  # noqa: DTZ007
        except ValueError:
            continue
    # Last resort: ISO format with timezone info.
    return _datetime.fromisoformat(text_value)


#: Ordered migrations. Index by target version; version 1 is the initial schema
#: and therefore has no migration.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=2,
        description="Add device connection settings (host, port, timeout, sync, enabled)",
        apply=_migrate_to_2,
    ),
    Migration(
        version=3,
        description="Add the append-only audit_events table",
        apply=_migrate_to_3,
    ),
    Migration(
        version=4,
        description="Add attendance sync columns (received_at, source, event_key, employee) and sync_history",
        apply=_migrate_to_4,
    ),
    Migration(
        version=5,
        description="Add employees, employee_device_links and pay_schedules (PHASE 05)",
        apply=_migrate_to_5,
    ),
    Migration(
        version=6,
        description="Add app_users local accounts (PHASE 07)",
        apply=_migrate_to_6,
    ),
    Migration(
        version=7,
        description="Add devices.last_seen_at (PHASE 08)",
        apply=_migrate_to_7,
    ),
)


def pending_migrations(from_version: int, to_version: int) -> list[Migration]:
    """Return the migrations needed to move between two versions."""
    return [migration for migration in MIGRATIONS if from_version < migration.version <= to_version]
