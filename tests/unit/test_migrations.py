"""Schema migration tests.

The important case is a v1 database that already holds records: an upgrade must
add the new columns without losing or corrupting anything.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from clockmanager.config import AppConfig, AppPaths
from clockmanager.errors import PersistenceError
from clockmanager.persistence.database import (
    SCHEMA_VERSION_KEY,
    create_database,
    initialise_database,
    schema_metadata,
)
from clockmanager.persistence.migrations import MIGRATIONS, pending_migrations
from clockmanager.persistence.models import SCHEMA_VERSION, DeviceRecord, SchemaInfo

V2_COLUMNS = {
    "host",
    "port",
    "communication_password",
    "timeout_seconds",
    "auto_reconnect",
    "sync_interval_seconds",
    "enabled",
}


@pytest.fixture
def database(tmp_path: Path):  # type: ignore[no-untyped-def]
    config = AppConfig(paths=AppPaths(tmp_path / "appdata"), log_to_console=False)
    db = create_database(config)
    try:
        yield db
    finally:
        db.dispose()


def _build_v1_database(database) -> None:  # type: ignore[no-untyped-def]
    """Recreate the PHASE 00 schema exactly, then stamp it as version 1."""
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE schema_info ("
                " key VARCHAR(64) NOT NULL PRIMARY KEY,"
                " value VARCHAR(255) NOT NULL,"
                " updated_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE devices ("
                " id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,"
                " name VARCHAR(120) NOT NULL,"
                " serial_number VARCHAR(120),"
                " model VARCHAR(64),"
                " platform VARCHAR(64),"
                " firmware_version VARCHAR(64),"
                " created_at DATETIME NOT NULL,"
                " updated_at DATETIME NOT NULL,"
                " CONSTRAINT uq_devices_name UNIQUE (name))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE device_users ("
                " id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,"
                " device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,"
                " device_uid INTEGER NOT NULL,"
                " user_id VARCHAR(64) NOT NULL,"
                " first_name VARCHAR(64) NOT NULL,"
                " last_name VARCHAR(64) NOT NULL,"
                " privilege INTEGER NOT NULL,"
                " created_at DATETIME NOT NULL,"
                " updated_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE attendance_events ("
                " id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,"
                " device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,"
                " device_uid INTEGER,"
                " user_id VARCHAR(64) NOT NULL,"
                " occurred_at DATETIME NOT NULL,"
                " punch INTEGER NOT NULL,"
                " status INTEGER NOT NULL,"
                " created_at DATETIME NOT NULL)"
            )
        )
        now = datetime.now(UTC).isoformat(sep=" ")
        connection.execute(
            text(
                "INSERT INTO schema_info (key, value, updated_at) "
                f"VALUES ('schema_version', '1', '{now}')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO devices (name, model, platform, created_at, updated_at) "
                f"VALUES ('Existing clock', 'NG-MB1', 'ZMM510_TFT', '{now}', '{now}')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO device_users "
                "(device_id, device_uid, user_id, first_name, last_name, privilege,"
                " created_at, updated_at) "
                f"VALUES (1, 7, '1001', 'Ada', 'Lovelace', 14, '{now}', '{now}')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO attendance_events "
                "(device_id, device_uid, user_id, occurred_at, punch, status, created_at) "
                f"VALUES (1, 7, '1001', '2026-02-03 07:55:00', 0, 3, '{now}')"
            )
        )


def test_migration_list_is_ordered_and_contiguous() -> None:
    versions = [migration.version for migration in MIGRATIONS]
    assert versions == sorted(versions)
    assert versions == list(range(2, SCHEMA_VERSION + 1))


def test_pending_migrations_selects_the_right_range() -> None:
    assert [m.version for m in pending_migrations(1, 2)] == [2]
    assert pending_migrations(2, 2) == []


def test_fresh_database_is_stamped_at_the_current_version(database) -> None:  # type: ignore[no-untyped-def]
    assert initialise_database(database) == SCHEMA_VERSION
    assert schema_metadata(database)[SCHEMA_VERSION_KEY] == str(SCHEMA_VERSION)


def test_v1_database_is_upgraded_in_place(database) -> None:  # type: ignore[no-untyped-def]
    _build_v1_database(database)
    columns = {c["name"] for c in inspect(database.engine).get_columns("devices")}
    assert not V2_COLUMNS & columns

    assert initialise_database(database) == SCHEMA_VERSION

    columns = {c["name"] for c in inspect(database.engine).get_columns("devices")}
    assert columns >= V2_COLUMNS
    assert schema_metadata(database)[SCHEMA_VERSION_KEY] == str(SCHEMA_VERSION)


def test_upgrade_preserves_existing_rows(database) -> None:  # type: ignore[no-untyped-def]
    _build_v1_database(database)
    initialise_database(database)

    with database.session() as session:
        device = session.query(DeviceRecord).one()
        assert device.name == "Existing clock"
        assert device.platform == "ZMM510_TFT"

        rows = session.execute(text("SELECT user_id, privilege FROM device_users")).all()
        assert rows == [("1001", 14)]

        events = session.execute(text("SELECT user_id, punch, status FROM attendance_events")).all()
        assert events == [("1001", 0, 3)]


def test_upgrade_applies_defaults_to_existing_rows(database) -> None:  # type: ignore[no-untyped-def]
    """An existing device must not end up with NULL in a NOT NULL column."""
    _build_v1_database(database)
    initialise_database(database)

    with database.session() as session:
        device = session.query(DeviceRecord).one()
        assert device.host is None  # no address is ever invented
        assert device.port == 4370
        assert device.communication_password == 0
        assert device.timeout_seconds == 10.0
        assert device.auto_reconnect is True
        assert device.sync_interval_seconds == 300
        assert device.enabled is True


def test_upgrade_is_idempotent(database) -> None:  # type: ignore[no-untyped-def]
    _build_v1_database(database)
    initialise_database(database)
    assert initialise_database(database) == SCHEMA_VERSION

    with database.session() as session:
        assert session.query(DeviceRecord).count() == 1


def test_partially_applied_migration_resumes(database) -> None:  # type: ignore[no-untyped-def]
    """An upgrade interrupted midway must complete, not fail on re-run."""
    _build_v1_database(database)
    with database.engine.begin() as connection:
        connection.execute(text("ALTER TABLE devices ADD COLUMN host VARCHAR(255)"))

    assert initialise_database(database) == SCHEMA_VERSION
    columns = {c["name"] for c in inspect(database.engine).get_columns("devices")}
    assert columns >= V2_COLUMNS


def test_newer_schema_version_is_refused(database) -> None:  # type: ignore[no-untyped-def]
    initialise_database(database)
    with database.session() as session:
        row = session.get(SchemaInfo, SCHEMA_VERSION_KEY)
        assert row is not None
        row.value = str(SCHEMA_VERSION + 1)

    with pytest.raises(PersistenceError, match="newer"):
        initialise_database(database)


def test_tables_without_a_recorded_version_are_refused(database) -> None:  # type: ignore[no-untyped-def]
    """Refuse to guess the version of an unstamped database."""
    _build_v1_database(database)
    with database.engine.begin() as connection:
        connection.execute(text("DELETE FROM schema_info"))

    with pytest.raises(PersistenceError, match="Refusing to guess"):
        initialise_database(database)


# -- schema version 3: the audit log (PHASE 03) --------------------------------


def test_v1_database_gains_the_audit_table(database) -> None:  # type: ignore[no-untyped-def]
    """The audit table must appear on an upgrade, not only on a fresh install."""
    _build_v1_database(database)
    assert "audit_events" not in inspect(database.engine).get_table_names()

    initialise_database(database)

    assert "audit_events" in inspect(database.engine).get_table_names()


def test_audit_table_upgrade_preserves_device_data(database) -> None:  # type: ignore[no-untyped-def]
    _build_v1_database(database)
    initialise_database(database)

    with database.session() as session:
        assert session.query(DeviceRecord).one().name == "Existing clock"
        assert session.execute(text("SELECT COUNT(*) FROM audit_events")).scalar_one() == 0


def test_audit_migration_is_resumable(database) -> None:  # type: ignore[no-untyped-def]
    """Re-running after an interruption must not fail on an existing table."""
    from clockmanager.persistence.migrations import MIGRATIONS as _MIGRATIONS

    migration = next(m for m in _MIGRATIONS if m.version == 3)
    with database.engine.begin() as connection:
        migration.apply(connection)
        migration.apply(connection)

    assert "audit_events" in inspect(database.engine).get_table_names()


def test_audit_rows_written_before_an_upgrade_survive_it(database) -> None:  # type: ignore[no-untyped-def]
    """AGENTS.md: never drop or rewrite a column holding user data."""
    initialise_database(database)
    now = datetime.now(UTC).isoformat(sep=" ")
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO audit_events "
                "(occurred_at, actor, action, outcome, detail) "
                f"VALUES ('{now}', 'tester', 'user.delete', 'succeeded', 'kept')"
            )
        )

    initialise_database(database)

    with database.session() as session:
        rows = session.execute(text("SELECT actor, detail FROM audit_events")).all()
        assert rows == [("tester", "kept")]


# -- schema version 4: attendance sync (PHASE 04) -------------------------------

V4_ATTENDANCE_COLUMNS = {"received_at", "source", "event_key", "employee_name"}


def test_fresh_database_has_sync_columns_and_history(database) -> None:  # type: ignore[no-untyped-def]
    initialise_database(database)
    columns = {c["name"] for c in inspect(database.engine).get_columns("attendance_events")}
    assert columns >= V4_ATTENDANCE_COLUMNS
    assert "sync_history" in inspect(database.engine).get_table_names()


def test_v1_database_gains_sync_columns_and_history(database) -> None:  # type: ignore[no-untyped-def]
    _build_v1_database(database)
    initialise_database(database)

    columns = {c["name"] for c in inspect(database.engine).get_columns("attendance_events")}
    assert columns >= V4_ATTENDANCE_COLUMNS
    assert "sync_history" in inspect(database.engine).get_table_names()


def test_upgrade_backfills_event_keys_and_received_at(database) -> None:  # type: ignore[no-untyped-def]
    """Rows stored before keys existed get deterministic keys, not blanks."""
    _build_v1_database(database)
    initialise_database(database)

    with database.session() as session:
        rows = session.execute(
            text("SELECT user_id, event_key, received_at, source FROM attendance_events")
        ).all()
    assert len(rows) == 1
    user_id, event_key, received_at, source = rows[0]
    assert user_id == "1001"
    assert event_key and len(event_key) == 64
    assert received_at is not None
    assert source == "historical"


def test_upgrade_preserves_attendance_rows(database) -> None:  # type: ignore[no-untyped-def]
    _build_v1_database(database)
    initialise_database(database)

    with database.session() as session:
        events = session.execute(text("SELECT user_id, punch, status FROM attendance_events")).all()
        assert events == [("1001", 0, 3)]


def test_sync_migration_is_resumable(database) -> None:  # type: ignore[no-untyped-def]
    """Re-running after an interruption must not fail on existing columns."""
    from clockmanager.persistence.migrations import MIGRATIONS as _MIGRATIONS

    initialise_database(database)
    migration = next(m for m in _MIGRATIONS if m.version == 4)
    with database.engine.begin() as connection:
        migration.apply(connection)
        migration.apply(connection)

    assert "sync_history" in inspect(database.engine).get_table_names()
