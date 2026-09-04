"""Database engine creation and bootstrap.

SQLite is the initial store. The engine/session boundary is kept narrow so a
PostgreSQL URL can be substituted for the future NAS/web deployment without
touching callers.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from clockmanager import __version__
from clockmanager.config import AppConfig
from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.errors import PersistenceError
from clockmanager.persistence.migrations import pending_migrations
from clockmanager.persistence.models import SCHEMA_VERSION, Base, SchemaInfo, utc_now

__all__ = [
    "Database",
    "create_database",
    "database_file_for",
    "initialise_database",
    "schema_metadata",
]

_logger = get_logger(__name__)

SCHEMA_VERSION_KEY = "schema_version"
CREATED_BY_KEY = "created_by_version"


@dataclass(frozen=True, slots=True)
class Database:
    """Owns the SQLAlchemy engine and session factory."""

    engine: Engine
    session_factory: sessionmaker[Session]

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Yield a session, committing on success and rolling back on failure."""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            raise PersistenceError(f"Database operation failed: {exc}") from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        """Release pooled connections. Safe to call more than once."""
        self.engine.dispose()


def _enable_sqlite_pragmas(engine: Engine) -> None:
    """Enable foreign keys and WAL for SQLite connections."""

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()


def create_database(config: AppConfig, *, url: str | None = None) -> Database:
    """Create the engine and session factory without touching the schema."""
    database_url = url if url is not None else config.database_url

    if database_url.startswith("sqlite") and ":memory:" not in database_url:
        config.paths.ensure()

    try:
        engine = create_engine(database_url, echo=config.database_echo, future=True)
    except SQLAlchemyError as exc:
        raise PersistenceError(f"Could not create database engine: {exc}") from exc

    if engine.dialect.name == "sqlite":
        _enable_sqlite_pragmas(engine)

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return Database(engine=engine, session_factory=factory)


def _stored_schema_version(database: Database) -> int | None:
    """Return the recorded schema version, or ``None`` for a fresh database."""
    with database.session() as session:
        stored = session.get(SchemaInfo, SCHEMA_VERSION_KEY)
        return None if stored is None else int(stored.value)


def _record_schema_version(database: Database, version: int) -> None:
    with database.session() as session:
        stored = session.get(SchemaInfo, SCHEMA_VERSION_KEY)
        if stored is None:
            session.add(SchemaInfo(key=SCHEMA_VERSION_KEY, value=str(version)))
        else:
            stored.value = str(version)
            stored.updated_at = utc_now()


def _upgrade(database: Database, from_version: int) -> int:
    """Apply pending migrations one at a time, recording each as it lands."""
    migrations = pending_migrations(from_version, SCHEMA_VERSION)
    if not migrations:
        raise PersistenceError(
            f"Database schema version {from_version} needs an upgrade to "
            f"{SCHEMA_VERSION}, but no migration path is defined."
        )

    current = from_version
    for migration in migrations:
        _logger.info(
            "Applying schema migration",
            extra={
                "from_version": current,
                "to_version": migration.version,
                "description": migration.description,
            },
        )
        try:
            with database.engine.begin() as connection:
                migration.apply(connection)
        except SQLAlchemyError as exc:
            raise PersistenceError(
                f"Migration to schema version {migration.version} failed: {exc}"
            ) from exc

        _record_schema_version(database, migration.version)
        current = migration.version

    return current


def initialise_database(database: Database) -> int:
    """Create or upgrade the schema and return the resulting version.

    Existing data is never dropped. A database written by a newer schema
    version is refused rather than silently downgraded.
    """
    existing_tables = set(inspect(database.engine).get_table_names())
    is_fresh = not existing_tables

    try:
        Base.metadata.create_all(database.engine)
    except SQLAlchemyError as exc:
        raise PersistenceError(f"Could not create database schema: {exc}") from exc

    found = _stored_schema_version(database)

    if found is None:
        if not is_fresh and existing_tables - {"schema_info"}:
            raise PersistenceError(
                "The database has tables but no recorded schema version. Refusing "
                "to guess which version it is; restore from a backup instead."
            )
        # create_all() just built the current schema, so stamp it directly.
        with database.session() as session:
            session.add(SchemaInfo(key=SCHEMA_VERSION_KEY, value=str(SCHEMA_VERSION)))
            session.add(SchemaInfo(key=CREATED_BY_KEY, value=__version__))
        _logger.info("Initialised database schema", extra={"schema_version": SCHEMA_VERSION})
        return SCHEMA_VERSION

    if found > SCHEMA_VERSION:
        raise PersistenceError(
            f"Database schema version {found} is newer than the supported "
            f"version {SCHEMA_VERSION}. Upgrade the application."
        )

    if found < SCHEMA_VERSION:
        return _upgrade(database, found)

    return found


def database_file_for(config: AppConfig) -> Path:
    """Return the SQLite file backing ``config`` (diagnostics convenience)."""
    return config.paths.database_file


def schema_metadata(database: Database) -> dict[str, str]:
    """Return the stored ``schema_info`` rows as a plain mapping."""
    with database.session() as session:
        rows = session.execute(select(SchemaInfo)).scalars().all()
        return {row.key: row.value for row in rows}
