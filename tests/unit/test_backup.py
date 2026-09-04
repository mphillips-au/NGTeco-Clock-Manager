"""Backup, restore and offline resilience tests (PHASE 09).

Backups must be complete (database, configuration and every per-type
export), restorable only after preview plus an explicit confirmation, and
free of secrets in their portable exports. Restores are audited and always
keep a pre-restore safety backup.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from clockmanager.config import AppConfig, AppPaths
from clockmanager.domain.auth import Role
from clockmanager.domain.payroll import Employee
from clockmanager.errors import ClockManagerError, SecurityError
from clockmanager.persistence.models import SCHEMA_VERSION
from clockmanager.services.application import ApplicationContext, bootstrap
from clockmanager.services.audit import AuditAction
from clockmanager.services.devices import DeviceProfile


@pytest.fixture
def context(tmp_path: Path) -> Iterator[ApplicationContext]:
    config = AppConfig(
        paths=AppPaths(tmp_path / "appdata"),
        log_to_console=False,
        use_mock_device=True,
    )
    ctx = bootstrap(config=config)
    try:
        yield ctx
    finally:
        ctx.shutdown()


def _seeded(context: ApplicationContext) -> DeviceProfile:
    """One device, one employee and one sync of stored attendance."""
    profile = context.devices.save_profile(DeviceProfile(name="Door", host="192.0.2.10"))
    context.employees.create(Employee(user_id="1001", first_name="Ada", last_name="Lovelace"))
    result = context.sync.manual_sync(profile)
    assert result.ok and result.new > 0
    return profile


def _names(archive: zipfile.ZipFile) -> list[str]:
    return sorted(archive.namelist())


class TestCreateBackup:
    def test_backup_holds_every_required_file(self, context: ApplicationContext) -> None:
        _seeded(context)
        target = context.backups.create_backup(label="nightly")
        assert target.is_file()
        with zipfile.ZipFile(target) as archive:
            names = _names(archive)
            for required in (
                "manifest.json",
                "config.json",
                "clockmanager.sqlite3",
                "employees.csv",
                "employees.json",
                "device_users.json",
                "attendance.csv",
                "audit.csv",
                "sync_history.json",
            ):
                assert required in names
            manifest = json.loads(archive.read("manifest.json").decode())
        assert manifest["schema_version"] == SCHEMA_VERSION
        assert manifest["label"] == "nightly"
        assert manifest["counts"]["employees"] == 1
        assert manifest["counts"]["attendance_events"] > 0
        assert "communication passwords" in manifest["sensitive_note"].lower()

    def test_backup_is_audited(self, context: ApplicationContext) -> None:
        _seeded(context)
        target = context.backups.create_backup()
        actions = [entry.action for entry in context.audit.recent(limit=50)]
        assert AuditAction.BACKUP_CREATE.value in actions
        assert any(target.name in entry.target for entry in context.audit.recent(limit=50))

    def test_exports_carry_no_secrets(self, context: ApplicationContext) -> None:
        context.devices.save_profile(
            DeviceProfile(name="Door", host="192.0.2.10", communication_password=4321)
        )
        target = context.backups.create_backup()
        with zipfile.ZipFile(target) as archive:
            for name in _names(archive):
                if name == "clockmanager.sqlite3":
                    continue  # full copy; documented as sensitive
                text = archive.read(name).decode("utf-8")
                assert "4321" not in text
                assert "communication_password" not in text
            users = json.loads(archive.read("device_users.json").decode())
        assert users and users[0]["status"] == "backed_up"
        for entry in users:
            for user in entry.get("users", []):
                assert set(user) == {
                    "device_uid",
                    "user_id",
                    "first_name",
                    "last_name",
                    "privilege",
                    "has_credential_data",
                }

    def test_backup_lists_newest_first(self, context: ApplicationContext) -> None:
        first = context.backups.create_backup(label="first")
        second = context.backups.create_backup(label="second")
        assert first != second
        listed = context.backups.list_backups()
        assert [item.path for item in listed] == [second, first]

    def test_unreachable_device_is_skipped_not_failed(self, context: ApplicationContext) -> None:
        context.devices.save_profile(DeviceProfile(name="Dead", host="192.0.2.99", enabled=False))
        target = context.backups.create_backup()
        with zipfile.ZipFile(target) as archive:
            users = json.loads(archive.read("device_users.json").decode())
        assert users == []

    def test_non_admin_cannot_back_up(self, context: ApplicationContext) -> None:
        with pytest.raises(SecurityError):
            context.backups.create_backup(requester_role=Role.VIEWER)


class TestPreview:
    def test_valid_backup_previews_clean(self, context: ApplicationContext) -> None:
        _seeded(context)
        target = context.backups.create_backup(label="good")
        preview = context.backups.preview_backup(target)
        assert preview.valid
        assert preview.problems == ()
        assert preview.counts["employees"] == 1
        assert "database" not in preview.describe()  # describe is about contents
        assert "employees: 1" in preview.describe()

    def test_missing_file_is_invalid(self, context: ApplicationContext, tmp_path: Path) -> None:
        missing = tmp_path / "nope.zip"
        preview = context.backups.preview_backup(missing)
        assert not preview.valid

    def test_corrupt_zip_is_invalid(self, context: ApplicationContext, tmp_path: Path) -> None:
        broken = tmp_path / "broken.zip"
        broken.write_text("not a zip", encoding="utf-8")
        assert not context.backups.preview_backup(broken).valid

    def test_newer_schema_is_refused(self, context: ApplicationContext, tmp_path: Path) -> None:
        path = tmp_path / "future.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "manifest.json",
                json.dumps({"label": "future", "schema_version": SCHEMA_VERSION + 1}),
            )
            archive.writestr("config.json", "{}")
            archive.writestr("clockmanager.sqlite3", b"fake")
        preview = context.backups.preview_backup(path)
        assert not preview.valid
        assert "newer" in preview.problems[0]

    def test_preview_changes_nothing(self, context: ApplicationContext) -> None:
        profile = _seeded(context)
        before = context.employees.count()
        target = context.backups.create_backup()
        context.backups.preview_backup(target)
        assert context.employees.count() == before
        assert context.devices.get_profile(profile.device_id or 0) is not None


class TestRestore:
    def test_restore_needs_an_explicit_confirmation(self, context: ApplicationContext) -> None:
        _seeded(context)
        target = context.backups.create_backup()
        with pytest.raises(ClockManagerError, match="confirmation"):
            context.backups.restore_backup(target, confirmed=False)
        assert context.employees.count() == 1

    def test_restore_round_trip(self, context: ApplicationContext) -> None:
        _seeded(context)
        target = context.backups.create_backup(label="checkpoint")

        # Diverge: a new employee, fresh attendance state and changed config.
        context.employees.create(Employee(user_id="9999", first_name="Zed"))
        assert context.employees.count() == 2

        result = context.backups.restore_backup(target, confirmed=True)
        assert result.ok
        assert result.restored == ("database", "configuration")
        assert result.safety_backup is not None and result.safety_backup.is_file()
        assert context.employees.count() == 1
        assert context.employees.list_employees()[0].user_id == "1001"

    def test_restore_is_audited_and_keeps_a_safety_backup(
        self, context: ApplicationContext
    ) -> None:
        _seeded(context)
        target = context.backups.create_backup(label="checkpoint")
        context.backups.restore_backup(target, confirmed=True)

        actions = [entry.action for entry in context.audit.recent(limit=50)]
        assert AuditAction.BACKUP_RESTORE.value in actions
        safety = [
            item for item in context.backups.list_backups() if "pre-restore" in item.path.name
        ]
        assert len(safety) == 1

    def test_restore_refuses_an_invalid_backup(
        self, context: ApplicationContext, tmp_path: Path
    ) -> None:
        _seeded(context)
        broken = tmp_path / "broken.zip"
        broken.write_text("not a zip", encoding="utf-8")
        with pytest.raises(ClockManagerError):
            context.backups.restore_backup(broken, confirmed=True)
        assert context.employees.count() == 1

    def test_non_admin_cannot_restore(self, context: ApplicationContext) -> None:
        _seeded(context)
        target = context.backups.create_backup()
        with pytest.raises(SecurityError):
            context.backups.restore_backup(target, confirmed=True, requester_role=Role.VIEWER)

    def test_restored_database_is_fully_usable(self, context: ApplicationContext) -> None:
        profile = _seeded(context)
        target = context.backups.create_backup(label="checkpoint")
        context.backups.restore_backup(target, confirmed=True)

        # Reads, syncs and new writes all work against the restored database.
        reread = context.devices.get_profile(profile.device_id or 0)
        assert reread is not None
        assert context.sync.manual_sync(reread).ok
        context.employees.create(Employee(user_id="4242", first_name="Grace"))
        assert context.employees.count() == 2


class TestOfflineReport:
    def test_report_describes_local_state(self, context: ApplicationContext) -> None:
        _seeded(context)
        report = context.backups.offline_report()
        assert len(report.devices) == 1
        state = report.devices[0]
        assert state.name == "Door"
        assert state.stored_events > 0
        assert state.last_outcome == "success"
        text = report.describe()
        assert "Offline mode" in text
        assert "Door" in text

    def test_report_with_no_devices(self, context: ApplicationContext) -> None:
        assert "No devices" in context.backups.offline_report().describe()

    def test_report_survives_device_removal_history(self, context: ApplicationContext) -> None:
        """Removing a profile keeps its punches; the report still works."""
        profile = _seeded(context)
        stored_before = context.sync.count_stored(profile)
        assert stored_before > 0
        report = context.backups.offline_report()
        assert report.devices[0].stored_events == stored_before
