"""Backup, restore and offline resilience (PHASE 09).

The application is useful without the clock: attendance, employees,
timesheets, reports, sync history and the audit log all live in local
storage. This service makes that state recoverable:

* :meth:`BackupService.create_backup` writes one zip containing a full
  database copy, the configuration file and portable per-type exports
  (employees, device users, attendance, audit, sync history).
* :meth:`BackupService.preview_backup` validates a backup and describes
  what restoring it would do, without changing anything.
* :meth:`BackupService.restore_backup` replaces local state only after
  validation *and* an explicit confirmation, and always keeps an automatic
  pre-restore safety backup. The restore itself is audited.
* :meth:`BackupService.offline_report` describes what keeps working while
  the clock is unreachable and how the next sync recovers.

The zip's database copy contains the stored device communication passwords
(``SECURITY.md`` documents them as unencrypted at rest), so a backup file
needs the same protection as the data directory. The portable exports
never contain a communication password, a PIN, a card identifier or a
biometric template: device users export only the ``has_credential_data``
indicator.

Everything here is synchronous and PySide6-free.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from clockmanager import __version__
from clockmanager.config import AppConfig, save_config
from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.auth import Permission, Role, require
from clockmanager.errors import ClockManagerError
from clockmanager.persistence.database import Database, initialise_database
from clockmanager.persistence.models import SCHEMA_VERSION, utc_now
from clockmanager.persistence.repositories import (
    AttendanceRepository,
    AuditRepository,
    DeviceRepository,
    EmployeeRepository,
    SyncHistoryRepository,
)
from clockmanager.protocol.errors import DeviceError
from clockmanager.services.audit import AuditAction, AuditOutcome, AuditService
from clockmanager.services.devices import DeviceService
from clockmanager.services.employees import EmployeeService

__all__ = [
    "BackupService",
    "BackupSummary",
    "OfflineDeviceState",
    "OfflineReport",
    "RestorePreview",
    "RestoreResult",
]

_logger = get_logger(__name__)

#: Files every backup zip must contain. The per-type exports are portable
#: conveniences; only these three are needed to restore.
_REQUIRED_FILES: Final[tuple[str, ...]] = (
    "manifest.json",
    "config.json",
    "clockmanager.sqlite3",
)

#: Bound on portable exports so one huge history cannot exhaust memory.
_MAX_EXPORT_ROWS: Final = 50_000

_BACKUP_PREFIX: Final = "clockmanager-backup-"


def _safe_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", label.strip()).strip("-")
    return cleaned[:40] or "manual"


@dataclass(frozen=True, slots=True)
class BackupSummary:
    """One backup file on disk, with its manifest data when readable."""

    path: Path
    label: str
    created_at: str
    app_version: str
    schema_version: int | None
    size_bytes: int

    def describe(self) -> str:
        schema = self.schema_version if self.schema_version is not None else "unknown"
        return f"{self.path.name} — {self.label}, schema {schema}, {self.size_bytes} bytes"


@dataclass(frozen=True, slots=True)
class RestorePreview:
    """What :meth:`BackupService.restore_backup` would do. Changes nothing."""

    path: Path
    valid: bool
    problems: tuple[str, ...]
    label: str = ""
    created_at: str = ""
    app_version: str = ""
    schema_version: int | None = None
    counts: dict[str, int] = field(default_factory=dict)
    files: tuple[str, ...] = ()

    def describe(self) -> str:
        if not self.valid:
            return "Backup is not restorable: " + "; ".join(self.problems)
        parts = [f"{name}: {count}" for name, count in sorted(self.counts.items())]
        schema = self.schema_version if self.schema_version is not None else "unknown"
        return (
            f"Backup {self.path.name} ({self.label}, schema {schema}) would restore "
            + ", ".join(parts)
        )


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """The outcome of a confirmed restore."""

    ok: bool
    restored: tuple[str, ...]
    detail: str
    safety_backup: Path | None = None


@dataclass(frozen=True, slots=True)
class OfflineDeviceState:
    """Locally known state for one device while the clock may be unreachable."""

    device_id: int | None
    name: str
    enabled: bool
    configured: bool
    last_seen_at: datetime | None
    stored_events: int
    last_success_at: datetime | None
    last_outcome: str | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class OfflineReport:
    """What keeps working offline, per device, from local storage only."""

    generated_at: datetime
    devices: tuple[OfflineDeviceState, ...] = ()

    def describe(self) -> str:
        lines = [
            "Offline mode: every view below reads local storage, so history, "
            "employees, timesheets, reports and the audit log keep working "
            "without the clock. The next successful sync re-reads the whole "
            "device log and stores whatever was missed."
        ]
        if not self.devices:
            lines.append("No devices are configured yet.")
            return "\n".join(lines)
        for state in self.devices:
            seen = (
                state.last_seen_at.isoformat(sep=" ", timespec="seconds")
                if state.last_seen_at is not None
                else "never"
            )
            lines.append(
                f"- {state.name}: {state.stored_events} stored punch(es), "
                f"last seen {seen}, last sync {state.last_outcome or 'never'}."
            )
        return "\n".join(lines)


class BackupService:
    """Creates, validates and restores application backups."""

    def __init__(
        self,
        database: Database,
        audit: AuditService,
        config: AppConfig,
        devices: DeviceService,
        employees: EmployeeService,
    ) -> None:
        self._database = database
        self._audit = audit
        self._config = config
        self._devices = devices
        self._employees = employees

    # -- backup -------------------------------------------------------------

    @property
    def backup_dir(self) -> Path:
        return self._config.paths.backup_dir

    def create_backup(
        self, *, label: str = "manual", requester_role: Role | str | None = None
    ) -> Path:
        """Write one backup zip into the backup directory and audit it.

        ``requester_role`` restricts backups to administrators (the zip holds
        the full database copy, including stored device connection secrets);
        ``None`` keeps the path for callers without an interactive identity.
        Never touches a device beyond a best-effort user-list read.
        """
        if requester_role is not None:
            require(requester_role, Permission.MANAGE_DEVICE_SETTINGS)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = utc_now().strftime("%Y%m%d-%H%M%S")
        target = self.backup_dir / f"{_BACKUP_PREFIX}{stamp}-{_safe_label(label)}.zip"

        counts = self._collect_counts()
        manifest = {
            "application": "NGTecoClockManager",
            "app_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "created_at": utc_now().isoformat(),
            "label": label,
            "counts": counts,
            "sensitive_note": (
                "This backup contains the full database copy, including stored "
                "device communication passwords. Protect it like the data directory."
            ),
        }
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
            archive.writestr(
                "config.json", json.dumps(self._config.to_dict(), indent=2, sort_keys=True)
            )
            archive.writestr("employees.csv", self._employees_csv())
            archive.writestr("employees.json", self._employees_json())
            archive.writestr("device_users.json", self._device_users_json())
            archive.writestr("attendance.csv", self._attendance_csv())
            archive.writestr("audit.csv", self._audit_csv())
            archive.writestr("sync_history.json", self._sync_history_json())
            self._write_database_copy(archive)

        self._audit.record(
            AuditAction.BACKUP_CREATE,
            AuditOutcome.SUCCEEDED,
            target=target.name,
            detail=f"Created backup {target.name} ({label})",
        )
        _logger.info("Created backup", extra={"backup": target.name, "label": label})
        return target

    def list_backups(self) -> list[BackupSummary]:
        """Backup files on disk, newest first. Never touches a device."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        summaries: list[BackupSummary] = []
        for path in sorted(self.backup_dir.glob(f"{_BACKUP_PREFIX}*.zip")):
            label, created, version, schema = self._read_manifest(path)
            summaries.append(
                BackupSummary(
                    path=path,
                    label=label,
                    created_at=created,
                    app_version=version,
                    schema_version=schema,
                    size_bytes=path.stat().st_size,
                )
            )
        summaries.sort(key=lambda item: item.path.name, reverse=True)
        return summaries

    # -- restore ------------------------------------------------------------

    def preview_backup(self, path: Path) -> RestorePreview:
        """Validate ``path`` and describe what restoring it would do.

        Pure read: never changes local state and never touches a device.
        """
        if not path.is_file():
            return RestorePreview(path=path, valid=False, problems=(f"{path} does not exist.",))
        try:
            archive = zipfile.ZipFile(path)
        except zipfile.BadZipFile:
            return RestorePreview(path=path, valid=False, problems=("Not a valid zip file.",))
        with archive:
            files = tuple(sorted(archive.namelist()))
            missing = [name for name in _REQUIRED_FILES if name not in archive.namelist()]
            if missing:
                return RestorePreview(
                    path=path,
                    valid=False,
                    problems=(f"Missing required file(s): {', '.join(missing)}.",),
                    files=files,
                )
            try:
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return RestorePreview(
                    path=path, valid=False, problems=("manifest.json is not valid JSON.",)
                )
            if not isinstance(manifest, dict):
                return RestorePreview(
                    path=path, valid=False, problems=("manifest.json is not an object.",)
                )
            try:
                config = json.loads(archive.read("config.json").decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return RestorePreview(
                    path=path, valid=False, problems=("config.json is not valid JSON.",)
                )
            if not isinstance(config, dict):
                return RestorePreview(
                    path=path, valid=False, problems=("config.json is not an object.",)
                )
            schema = manifest.get("schema_version")
            if isinstance(schema, int) and schema > SCHEMA_VERSION:
                return RestorePreview(
                    path=path,
                    valid=False,
                    problems=(
                        f"Backup schema version {schema} is newer than the supported "
                        f"version {SCHEMA_VERSION}. Upgrade the application first.",
                    ),
                    files=files,
                )
            counts = manifest.get("counts")
            return RestorePreview(
                path=path,
                valid=True,
                problems=(),
                label=str(manifest.get("label", "")),
                created_at=str(manifest.get("created_at", "")),
                app_version=str(manifest.get("app_version", "")),
                schema_version=schema if isinstance(schema, int) else None,
                counts=dict(counts) if isinstance(counts, dict) else {},
                files=files,
            )

    def restore_backup(
        self, path: Path, *, confirmed: bool, requester_role: Role | str | None = None
    ) -> RestoreResult:
        """Replace local database and configuration from ``path``.

        Requires ``confirmed=True``: without an explicit confirmation the
        restore is refused and nothing changes. Takes an automatic
        pre-restore safety backup first, validates the backup, refuses a
        backup from a newer schema, migrates an older database forward, and
        audits the restore. Never touches a device.
        """
        if requester_role is not None:
            require(requester_role, Permission.MANAGE_DEVICE_SETTINGS)
        if not confirmed:
            raise ClockManagerError(
                "Restore needs an explicit confirmation before any local data is replaced."
            )
        preview = self.preview_backup(path)
        if not preview.valid:
            raise ClockManagerError("; ".join(preview.problems))

        safety = self.create_backup(label="pre-restore")
        restored: list[str] = []
        with tempfile.TemporaryDirectory(prefix="clockmanager-restore-") as work:
            with zipfile.ZipFile(path) as archive:
                archive.extract("clockmanager.sqlite3", work)
                archive.extract("config.json", work)
            extracted_db = Path(work) / "clockmanager.sqlite3"
            self._restore_database(extracted_db)
            restored.append("database")
            self._restore_config(Path(work) / "config.json")
            restored.append("configuration")

        detail = f"Restored {', '.join(restored)} from {path.name}"
        self._audit.record(
            AuditAction.BACKUP_RESTORE,
            AuditOutcome.SUCCEEDED,
            target=path.name,
            detail=f"{detail}. Safety backup: {safety.name}",
        )
        _logger.info("Restored backup", extra={"backup": path.name, "safety": safety.name})
        return RestoreResult(ok=True, restored=tuple(restored), detail=detail, safety_backup=safety)

    # -- offline ------------------------------------------------------------

    def offline_report(self) -> OfflineReport:
        """Describe what keeps working while the clock is unreachable.

        Local reads only: never touches a device.
        """
        states: list[OfflineDeviceState] = []
        for status in self._devices.statuses():
            profile = status.profile
            states.append(
                OfflineDeviceState(
                    device_id=profile.device_id,
                    name=profile.name,
                    enabled=profile.enabled,
                    configured=profile.is_configured,
                    last_seen_at=profile.last_seen_at,
                    stored_events=status.stored_events,
                    last_success_at=status.last_success_at,
                    last_outcome=status.last_outcome,
                    last_error=status.last_error,
                )
            )
        return OfflineReport(generated_at=utc_now(), devices=tuple(states))

    # -- internals ----------------------------------------------------------

    def _collect_counts(self) -> dict[str, int]:
        with self._database.session() as session:
            return {
                "devices": DeviceRepository(session).count(),
                "employees": EmployeeRepository(session).count(),
                "attendance_events": AttendanceRepository(session).count(),
                "audit_events": AuditRepository(session).count(),
                "sync_runs": SyncHistoryRepository(session).count(),
            }

    def _device_names(self) -> dict[int, str]:
        with self._database.session() as session:
            return {record.id: record.name for record in DeviceRepository(session).list_all()}

    def _employees_csv(self) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            ["user_id", "first_name", "last_name", "active", "department", "position", "email"]
        )
        for profile in self._employees.list_employees(include_inactive=True):
            writer.writerow(
                [
                    profile.user_id,
                    profile.first_name,
                    profile.last_name,
                    "yes" if profile.active else "no",
                    profile.department,
                    profile.position,
                    profile.email,
                ]
            )
        return buffer.getvalue()

    def _employees_json(self) -> str:
        with self._database.session() as session:
            repository = EmployeeRepository(session)
            payload = []
            for row in repository.list_all(include_inactive=True):
                payload.append(
                    {
                        "user_id": row.user_id,
                        "first_name": row.first_name,
                        "last_name": row.last_name,
                        "active": bool(row.active),
                        "department": row.department,
                        "position": row.position,
                        "email": row.email,
                        "device_links": [
                            {
                                "device_id": link.device_id,
                                "user_id": link.user_id,
                                "device_uid": link.device_uid,
                            }
                            for link in repository.links_for(row.id)
                        ],
                    }
                )
        return json.dumps(payload, indent=2, sort_keys=True)

    def _device_users_json(self) -> str:
        """Best-effort user lists, read live; unreachable devices are skipped.

        Only display-safe fields: user ID, names, privilege and the
        credential-presence indicator. Never credential contents.
        """
        devices: list[dict[str, Any]] = []
        for profile in self._devices.list_profiles():
            if not profile.enabled or not profile.is_configured:
                continue
            try:
                users = self._devices.read_users(profile)
            except (DeviceError, ClockManagerError) as exc:
                devices.append({"device": profile.name, "status": "skipped", "reason": str(exc)})
                continue
            devices.append(
                {
                    "device": profile.name,
                    "status": "backed_up",
                    "users": [
                        {
                            "device_uid": user.device_uid,
                            "user_id": user.user_id,
                            "first_name": user.first_name,
                            "last_name": user.last_name,
                            "privilege": user.privilege,
                            "has_credential_data": bool(user.has_credential_data),
                        }
                        for user in users
                    ],
                }
            )
        return json.dumps(devices, indent=2, sort_keys=True)

    def _attendance_csv(self) -> str:
        names = self._device_names()
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            ["device", "user_id", "occurred_at", "punch", "status", "source", "employee_name"]
        )
        with self._database.session() as session:
            rows = AttendanceRepository(session).recent(limit=_MAX_EXPORT_ROWS)
            for row in rows:
                writer.writerow(
                    [
                        names.get(row.device_id, str(row.device_id)),
                        row.user_id,
                        row.occurred_at.isoformat(sep=" ", timespec="seconds"),
                        row.punch,
                        row.status,
                        row.source,
                        row.employee_name or "",
                    ]
                )
        return buffer.getvalue()

    def _audit_csv(self) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            ["occurred_at", "actor", "action", "outcome", "device_name", "target", "detail"]
        )
        with self._database.session() as session:
            rows = AuditRepository(session).list_filtered(limit=_MAX_EXPORT_ROWS)
            for row in rows:
                writer.writerow(
                    [
                        row.occurred_at.isoformat(sep=" ", timespec="seconds"),
                        row.actor,
                        row.action,
                        row.outcome,
                        row.device_name or "",
                        row.target or "",
                        row.detail,
                    ]
                )
        return buffer.getvalue()

    def _sync_history_json(self) -> str:
        with self._database.session() as session:
            rows = SyncHistoryRepository(session).recent(limit=_MAX_EXPORT_ROWS)
            payload = [
                {
                    "device_id": row.device_id,
                    "device_name": row.device_name,
                    "started_at": row.started_at.isoformat(sep=" ", timespec="seconds"),
                    "mode": row.mode,
                    "source": row.source,
                    "events_seen": row.events_seen,
                    "events_new": row.events_new,
                    "events_duplicate": row.events_duplicate,
                    "outcome": row.outcome,
                    "error": row.error,
                }
                for row in rows
            ]
        return json.dumps(payload, indent=2, sort_keys=True)

    def _write_database_copy(self, archive: zipfile.ZipFile) -> None:
        """Append the live database bytes to the open archive."""
        if self._database.engine.dialect.name != "sqlite":
            raise ClockManagerError("Backups currently support the SQLite store only.")
        self._database.engine.dispose()
        raw = self._database.engine.raw_connection()
        try:
            native = raw.driver_connection
            assert isinstance(native, sqlite3.Connection), (
                f"SQLite backups need a sqlite3 connection, got {type(native).__name__}."
            )
            with tempfile.TemporaryDirectory(prefix="clockmanager-backup-") as work:
                target = str(Path(work) / "clockmanager.sqlite3")
                destination = sqlite3.connect(target)
                try:
                    native.backup(destination)
                finally:
                    destination.close()
                archive.write(target, "clockmanager.sqlite3")
        finally:
            raw.close()

    def _restore_database(self, extracted: Path) -> None:
        """Copy the backup database into the live store, migrating forward."""
        probe = sqlite3.connect(str(extracted))
        try:
            probe.execute("SELECT count(*) FROM schema_info").fetchone()
        except sqlite3.Error as exc:
            raise ClockManagerError(f"Backup database is not readable: {exc}") from exc
        finally:
            probe.close()
        self._database.engine.dispose()
        raw = self._database.engine.raw_connection()
        try:
            native = raw.driver_connection
            assert isinstance(native, sqlite3.Connection), (
                f"SQLite restores need a sqlite3 connection, got {type(native).__name__}."
            )
            source = sqlite3.connect(str(extracted))
            try:
                source.backup(native)
            finally:
                source.close()
        finally:
            raw.close()
        initialise_database(self._database)

    def _restore_config(self, extracted: Path) -> None:
        try:
            stored = json.loads(extracted.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ClockManagerError(f"Backup configuration is not readable: {exc}") from exc
        if not isinstance(stored, dict):
            raise ClockManagerError("Backup configuration is not an object.")
        known = set(self._config.to_dict())
        unknown = sorted(set(stored) - known)
        if unknown:
            raise ClockManagerError(
                f"Backup configuration holds unknown keys: {', '.join(unknown)}."
            )
        save_config(replace(self._config, **stored))
        self._config = replace(self._config, **stored)

    def _read_manifest(self, path: Path) -> tuple[str, str, str, int | None]:
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (zipfile.BadZipFile, KeyError, ValueError, UnicodeDecodeError):
            return path.stem, "", "", None
        if not isinstance(manifest, dict):
            return path.stem, "", "", None
        schema = manifest.get("schema_version")
        return (
            str(manifest.get("label", path.stem)),
            str(manifest.get("created_at", "")),
            str(manifest.get("app_version", "")),
            schema if isinstance(schema, int) else None,
        )
