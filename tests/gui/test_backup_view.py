"""Backup view tests (PHASE 09).

Creating, previewing and restoring go through the backup service off the UI
thread. Restoring additionally needs an explicit confirmation dialog: without
it nothing changes.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from clockmanager.domain.auth import Role
from clockmanager.domain.payroll import Employee
from clockmanager.gui.views.backup import BackupView
from clockmanager.services.application import ApplicationContext
from clockmanager.services.devices import DeviceProfile
from tests.gui.conftest import drain

pytestmark = pytest.mark.gui


def _seeded(mock_context: ApplicationContext) -> None:
    profile = mock_context.devices.save_profile(
        DeviceProfile(name="Bench clock", host="192.0.2.10")
    )
    mock_context.employees.create(Employee(user_id="1001", first_name="Ada"))
    assert mock_context.sync.manual_sync(profile).ok


def test_offline_status_loads_without_a_device(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    _seeded(mock_context)
    view = BackupView(mock_context)
    try:
        drain(qt_app)
        assert "Offline mode" in view._offline_label.text()
        assert "Bench clock" in view._offline_label.text()
    finally:
        view.close()


def test_create_lists_the_new_backup(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    _seeded(mock_context)
    view = BackupView(mock_context)
    try:
        drain(qt_app)
        view._label.setText("nightly")
        view._create()
        drain(qt_app)
        drain(qt_app)  # creation reloads the list
        assert view._table.rowCount() == 1
        assert "nightly" in view._table.item(0, 1).text()
        assert "Created backup" in view._status.text()
    finally:
        view.close()


def test_selecting_a_backup_shows_its_preview(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    _seeded(mock_context)
    mock_context.backups.create_backup(label="checkpoint")
    view = BackupView(mock_context)
    try:
        drain(qt_app)
        assert view._table.rowCount() == 1
        view._table.selectRow(0)
        drain(qt_app)
        assert "employees: 1" in view._preview_label.text()
        assert view._restore_button.isEnabled()
    finally:
        view.close()


def test_restore_without_confirmation_changes_nothing(
    qt_app: QApplication, mock_context: ApplicationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seeded(mock_context)
    mock_context.backups.create_backup(label="checkpoint")
    view = BackupView(mock_context)
    try:
        drain(qt_app)
        view._table.selectRow(0)
        drain(qt_app)

        monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
        view._restore()
        drain(qt_app)

        assert "nothing was changed" in view._status.text()
        assert mock_context.employees.count() == 1
    finally:
        view.close()


def test_confirmed_restore_recovers_diverged_state(
    qt_app: QApplication, mock_context: ApplicationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seeded(mock_context)
    mock_context.backups.create_backup(label="checkpoint")
    mock_context.employees.create(Employee(user_id="9999", first_name="Zed"))
    assert mock_context.employees.count() == 2

    view = BackupView(mock_context)
    try:
        drain(qt_app)
        view._table.selectRow(0)
        drain(qt_app)

        monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
        view._restore()
        drain(qt_app)
        drain(qt_app)  # restore reloads the list and the offline report

        assert mock_context.employees.count() == 1
        assert "Restored" in view._status.text()
    finally:
        view.close()


def test_non_admin_controls_are_locked(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    view = BackupView(mock_context, role=Role.VIEWER)
    try:
        drain(qt_app)
        assert not view._create_button.isEnabled()
        assert not view._restore_button.isEnabled()
        notes = [label.text() for label in view.findChildren(QLabel)]
        assert any("administrators" in text for text in notes)
    finally:
        view.close()
