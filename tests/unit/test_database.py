"""Database bootstrap and persistence tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from clockmanager.config import AppConfig, AppPaths
from clockmanager.domain.models import DeviceIdentity
from clockmanager.errors import PersistenceError
from clockmanager.persistence.database import (
    SCHEMA_VERSION_KEY,
    create_database,
    initialise_database,
    schema_metadata,
)
from clockmanager.persistence.models import (
    SCHEMA_VERSION,
    AttendanceEventRecord,
    DeviceRecord,
    DeviceUserRecord,
    SchemaInfo,
)
from clockmanager.persistence.repositories import DeviceRepository

EXPECTED_TABLES = {
    "schema_info",
    "devices",
    "device_users",
    "attendance_events",
    "audit_events",
    "sync_history",
}


@pytest.fixture
def database(tmp_path: Path):  # type: ignore[no-untyped-def]
    config = AppConfig(paths=AppPaths(tmp_path / "appdata"), log_to_console=False)
    db = create_database(config)
    try:
        yield db
    finally:
        db.dispose()


def test_initialise_creates_all_tables(database) -> None:  # type: ignore[no-untyped-def]
    version = initialise_database(database)
    assert version == SCHEMA_VERSION
    assert set(inspect(database.engine).get_table_names()) >= EXPECTED_TABLES


def test_schema_metadata_recorded(database) -> None:  # type: ignore[no-untyped-def]
    initialise_database(database)
    metadata = schema_metadata(database)
    assert metadata[SCHEMA_VERSION_KEY] == str(SCHEMA_VERSION)
    assert "created_by_version" in metadata


def test_initialise_is_idempotent(database) -> None:  # type: ignore[no-untyped-def]
    initialise_database(database)
    with database.session() as session:
        session.add(DeviceRecord(name="Workshop clock"))

    assert initialise_database(database) == SCHEMA_VERSION
    with database.session() as session:
        assert DeviceRepository(session).count() == 1


def test_newer_schema_version_is_refused(database) -> None:  # type: ignore[no-untyped-def]
    initialise_database(database)
    with database.session() as session:
        row = session.get(SchemaInfo, SCHEMA_VERSION_KEY)
        assert row is not None
        row.value = str(SCHEMA_VERSION + 1)

    with pytest.raises(PersistenceError, match="newer"):
        initialise_database(database)


def test_database_file_is_created_in_data_dir(tmp_path: Path) -> None:
    config = AppConfig(paths=AppPaths(tmp_path / "appdata"), log_to_console=False)
    db = create_database(config)
    try:
        initialise_database(db)
    finally:
        db.dispose()
    assert config.paths.database_file.exists()


def test_sqlite_foreign_keys_enforced(database) -> None:  # type: ignore[no-untyped-def]
    initialise_database(database)
    with pytest.raises(PersistenceError), database.session() as session:
        session.add(DeviceUserRecord(device_id=999, device_uid=1, user_id="1001"))


def test_session_rolls_back_on_error(database) -> None:  # type: ignore[no-untyped-def]
    initialise_database(database)
    with pytest.raises(RuntimeError), database.session() as session:
        session.add(DeviceRecord(name="Rolled back"))
        session.flush()
        raise RuntimeError("boom")

    with database.session() as session:
        assert DeviceRepository(session).get_by_name("Rolled back") is None


def test_device_repository_round_trip(database) -> None:  # type: ignore[no-untyped-def]
    initialise_database(database)
    identity = DeviceIdentity(
        name="Front door clock",
        serial_number="TEST-SERIAL-0001",
        model="NG-MB1",
        platform="ZMM510_TFT",
        firmware_version="Ver 8.0.4.5-7108-02",
    )
    with database.session() as session:
        DeviceRepository(session).add(identity)

    with database.session() as session:
        repository = DeviceRepository(session)
        record = repository.get_by_name("Front door clock")
        assert record is not None
        assert repository.to_identity(record) == identity
        assert repository.count() == 1
        assert [r.name for r in repository.list_all()] == ["Front door clock"]


def test_duplicate_device_name_rejected(database) -> None:  # type: ignore[no-untyped-def]
    initialise_database(database)
    with database.session() as session:
        session.add(DeviceRecord(name="Only one"))
    with pytest.raises(PersistenceError), database.session() as session:
        session.add(DeviceRecord(name="Only one"))


def test_attendance_natural_key_prevents_duplicates(database) -> None:  # type: ignore[no-untyped-def]
    """Re-reading the same device history must not create duplicate rows."""
    initialise_database(database)
    with database.session() as session:
        device = DeviceRecord(name="Front door clock")
        session.add(device)
        session.flush()
        device_id = device.id

    punch_time = datetime(2026, 2, 3, 7, 55, tzinfo=UTC)

    def _event() -> AttendanceEventRecord:
        return AttendanceEventRecord(
            device_id=device_id,
            device_uid=1,
            user_id="1001",
            occurred_at=punch_time,
            punch=0,
            status=0,
        )

    with database.session() as session:
        session.add(_event())

    with pytest.raises(PersistenceError), database.session() as session:
        session.add(_event())


def test_attendance_preserves_raw_punch_and_status(database) -> None:  # type: ignore[no-untyped-def]
    initialise_database(database)
    with database.session() as session:
        device = DeviceRecord(name="Front door clock")
        session.add(device)
        session.flush()
        session.add(
            AttendanceEventRecord(
                device_id=device.id,
                user_id="1001",
                occurred_at=datetime(2026, 2, 3, 16, 5, tzinfo=UTC),
                punch=1,
                status=7,
            )
        )

    with database.session() as session:
        stored = session.query(AttendanceEventRecord).one()
        assert stored.punch == 1
        assert stored.status == 7


#: The one sensitive column the schema is allowed to hold: the operator-set
#: device communication password, required to reconnect to a device. It is NOT
#: user credential data. Any other match is a defect.
ALLOWED_SENSITIVE_COLUMNS = {("devices", "communication_password")}


def test_no_user_credential_columns_exist(database) -> None:  # type: ignore[no-untyped-def]
    """SECURITY.md: no user PIN, card or biometric data is persisted."""
    initialise_database(database)
    inspector = inspect(database.engine)
    found: set[tuple[str, str]] = set()

    for table in EXPECTED_TABLES:
        names = {column["name"].lower() for column in inspector.get_columns(table)}
        for forbidden in ("pin", "password", "card", "credential", "template", "biometric"):
            found |= {(table, name) for name in names if forbidden in name}

    assert found == ALLOWED_SENSITIVE_COLUMNS, f"Unexpected sensitive columns: {found}"


def test_user_and_attendance_tables_hold_nothing_sensitive(database) -> None:  # type: ignore[no-untyped-def]
    """No exception applies to user or attendance data."""
    initialise_database(database)
    inspector = inspect(database.engine)
    for table in ("device_users", "attendance_events"):
        names = {column["name"].lower() for column in inspector.get_columns(table)}
        for forbidden in ("pin", "password", "card", "credential", "template", "biometric"):
            assert not any(forbidden in name for name in names), (table, forbidden)


def test_integrity_error_is_wrapped(database) -> None:  # type: ignore[no-untyped-def]
    """SQLAlchemy errors surface as application errors, never leak upward raw."""
    initialise_database(database)
    with pytest.raises(PersistenceError) as excinfo, database.session() as session:
        session.add(DeviceRecord(name="Dup"))
        session.add(DeviceRecord(name="Dup"))
    assert not isinstance(excinfo.value, IntegrityError)


def test_sqlite_returns_naive_datetimes(database) -> None:  # type: ignore[no-untyped-def]
    """Documents a real limitation rather than asserting desired behaviour.

    Timestamps are written as timezone-aware UTC, but SQLite has no timezone
    type and returns naive datetimes. PHASE 04 must normalise on read; this
    test fails loudly if the behaviour ever changes.
    """
    initialise_database(database)
    with database.session() as session:
        device = DeviceRecord(name="Front door clock")
        session.add(device)
        session.flush()
        session.add(
            AttendanceEventRecord(
                device_id=device.id,
                user_id="1001",
                occurred_at=datetime(2026, 2, 3, 7, 55, tzinfo=UTC),
                punch=0,
            )
        )

    with database.session() as session:
        stored = session.query(AttendanceEventRecord).one()
        assert stored.occurred_at.tzinfo is None
        assert stored.occurred_at == datetime(2026, 2, 3, 7, 55)  # noqa: DTZ001
