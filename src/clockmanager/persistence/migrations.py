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
)


def pending_migrations(from_version: int, to_version: int) -> list[Migration]:
    """Return the migrations needed to move between two versions."""
    return [migration for migration in MIGRATIONS if from_version < migration.version <= to_version]
