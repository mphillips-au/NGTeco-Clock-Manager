"""PHASE 07 GUI tests: login, role-gated navigation and restricted screens.

Service logic is tested in ``tests/unit/test_auth.py``; these check that the
window shows each role exactly what it may see, disables what it may not do,
and refuses hidden screens instead of crashing.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from clockmanager.domain.auth import Role
from clockmanager.gui.main_window import MainWindow, visible_views_for
from clockmanager.gui.views.auth import BootstrapAdminDialog, LoginDialog, UserAccountsView
from clockmanager.services.application import ApplicationContext
from clockmanager.services.auth import AuthenticatedUser
from tests.gui.conftest import drain

pytestmark = pytest.mark.gui

PASSWORD = "correct-horse-41"


def make_users(mock_context: ApplicationContext) -> dict[str, AuthenticatedUser]:
    """Bootstrap an admin plus one office and one viewer account."""
    auth = mock_context.auth
    auth.bootstrap_admin(username="boss", display_name="Boss", password=PASSWORD)
    admin = auth.authenticate("boss", PASSWORD)
    office = auth.create_user(
        username="office",
        display_name="",
        role=Role.OFFICE_STAFF,
        password=PASSWORD,
        requester=admin,
    )
    viewer = auth.create_user(
        username="viewer",
        display_name="",
        role=Role.VIEWER,
        password=PASSWORD,
        requester=admin,
    )
    auth.logout()
    return {"admin": admin, "office": office, "viewer": viewer}


# -- navigation visibility -------------------------------------------------------


def test_visible_views_for_each_role() -> None:
    assert visible_views_for(None) == frozenset(
        {
            "Dashboard",
            "Users",
            "Attendance",
            "Live events",
            "Employees",
            "Timesheets",
            "Reports",
            "Device settings",
            "Audit log",
            "Diagnostics",
            "Backup",
        }
    )
    assert "User accounts" in visible_views_for(Role.ADMIN)
    assert "Device settings" in visible_views_for(Role.ADMIN)
    office = visible_views_for(Role.OFFICE_STAFF)
    assert {"Live events", "Audit log"} <= office
    assert "Device settings" not in office
    assert "Diagnostics" not in office
    assert "User accounts" not in office
    viewer = visible_views_for(Role.VIEWER)
    assert viewer == frozenset(
        {"Dashboard", "Users", "Attendance", "Employees", "Timesheets", "Reports"}
    )


def test_admin_sees_everything_including_accounts(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    users = make_users(mock_context)
    window = MainWindow(mock_context, current_user=users["admin"])
    try:
        drain(qt_app)
        labels = [entry.label for entry in window._entries]
        assert "User accounts" in labels
        assert "Device settings" in labels
        assert "Diagnostics" in labels
        assert "Backup" in labels
        assert window.accounts_view is not None
    finally:
        window.close()


def test_office_hides_device_settings_diagnostics_and_accounts(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    users = make_users(mock_context)
    window = MainWindow(mock_context, current_user=users["office"])
    try:
        drain(qt_app)
        labels = [entry.label for entry in window._entries]
        assert "Live events" in labels
        assert "Audit log" in labels
        assert "Device settings" not in labels
        assert "Diagnostics" not in labels
        assert "User accounts" not in labels
        assert "Backup" not in labels
    finally:
        window.close()


def test_viewer_sees_read_only_views_only(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    users = make_users(mock_context)
    window = MainWindow(mock_context, current_user=users["viewer"])
    try:
        drain(qt_app)
        labels = [entry.label for entry in window._entries]
        assert labels == [
            "Dashboard",
            "Users",
            "Attendance",
            "Employees",
            "Timesheets",
            "Reports",
        ]
    finally:
        window.close()


def test_hidden_view_refuses_instead_of_crashing(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    users = make_users(mock_context)
    window = MainWindow(mock_context, current_user=users["viewer"])
    try:
        drain(qt_app)
        window.show_view("Device settings")
        drain(qt_app)
        assert window.current_view_name != "Device settings"
        assert "not available" in window.statusBar().currentMessage()
    finally:
        window.close()


def test_legacy_window_without_login_is_unchanged(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    window = MainWindow(mock_context)
    try:
        drain(qt_app)
        assert [entry.label for entry in window._entries] == [
            "Dashboard",
            "Users",
            "Attendance",
            "Live events",
            "Employees",
            "Timesheets",
            "Reports",
            "Device settings",
            "Audit log",
            "Diagnostics",
            "Backup",
        ]
    finally:
        window.close()


# -- per-view role gating ----------------------------------------------------------


def test_viewer_users_view_disables_writes(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    users = make_users(mock_context)
    window = MainWindow(mock_context, current_user=users["viewer"])
    try:
        drain(qt_app)
        assert not window.users_view._add_button.isEnabled()
        assert not window.users_view._edit_button.isEnabled()
        assert not window.users_view._delete_button.isEnabled()
    finally:
        window.close()


def test_viewer_attendance_view_disables_sync(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    users = make_users(mock_context)
    window = MainWindow(mock_context, current_user=users["viewer"])
    try:
        drain(qt_app)
        assert not window.attendance_view._sync_button.isEnabled()
        assert window.attendance_view._refresh_button.isEnabled()
    finally:
        window.close()


def test_viewer_employees_view_is_read_only(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    users = make_users(mock_context)
    window = MainWindow(mock_context, current_user=users["viewer"])
    try:
        drain(qt_app)
        assert not window.employees_view._add_button.isEnabled()
    finally:
        window.close()


def test_office_device_settings_view_locks_save_and_delete(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    from clockmanager.gui.views.device_settings import DeviceSettingsView

    view = DeviceSettingsView(mock_context.devices, role=Role.OFFICE_STAFF)
    try:
        drain(qt_app)
        assert not view._save_button.isEnabled()
        assert not view._delete_button.isEnabled()
        assert view._test_button.isEnabled()  # read-only checks stay available
    finally:
        view.close()


def test_developer_menu_requires_admin_in_developer_mode(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    from dataclasses import replace

    from clockmanager.services.application import ApplicationContext as Context

    users = make_users(mock_context)
    developer_context = Context(
        config=replace(mock_context.config, developer_mode=True),
        database=mock_context.database,
        log_file=mock_context.log_file,
        schema_version=mock_context.schema_version,
        auth_session=mock_context.auth_session,
    )
    office_window = MainWindow(developer_context, current_user=users["office"])
    try:
        drain(qt_app)
        titles = [action.text() for action in office_window.menuBar().actions()]
        assert "&Developer" not in titles
    finally:
        office_window.close()

    admin_window = MainWindow(developer_context, current_user=users["admin"])
    try:
        drain(qt_app)
        titles = [action.text() for action in admin_window.menuBar().actions()]
        assert "&Developer" in titles
    finally:
        admin_window.close()


def test_status_bar_names_the_logged_in_user(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    users = make_users(mock_context)
    window = MainWindow(mock_context, current_user=users["office"])
    try:
        drain(qt_app)
        message = window.statusBar().currentMessage()
        assert "office" in message
        assert "Office staff" in message
    finally:
        window.close()


# -- dialogs -------------------------------------------------------------------------


def test_login_dialog_accepts_good_credentials(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    make_users(mock_context)
    dialog = LoginDialog(mock_context.auth)
    try:
        dialog._username.setText("viewer")
        dialog._password.setText(PASSWORD)
        dialog._on_accept()
        assert dialog.user is not None
        assert dialog.user.username == "viewer"
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        dialog.close()


def test_login_dialog_rejects_bad_credentials_without_closing(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    make_users(mock_context)
    dialog = LoginDialog(mock_context.auth)
    try:
        dialog._username.setText("viewer")
        dialog._password.setText("wrong-password")
        dialog._on_accept()
        assert dialog.user is None
        assert "Invalid username or password" in dialog._error.text()
        assert dialog.result() == QDialog.DialogCode.Rejected  # still open
        assert PASSWORD not in dialog._error.text()
    finally:
        dialog.close()


def test_bootstrap_dialog_creates_first_admin_and_logs_in(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    dialog = BootstrapAdminDialog(mock_context.auth)
    try:
        dialog._username.setText("boss")
        dialog._display.setText("Boss")
        dialog._password.setText(PASSWORD)
        dialog._confirm.setText(PASSWORD)
        dialog._on_accept()
        assert dialog.user is not None
        assert dialog.user.role == Role.ADMIN
        assert mock_context.auth.current_user == dialog.user
    finally:
        dialog.close()


def test_bootstrap_dialog_refuses_mismatched_passwords(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    dialog = BootstrapAdminDialog(mock_context.auth)
    try:
        dialog._username.setText("boss")
        dialog._password.setText(PASSWORD)
        dialog._confirm.setText("something-else-entirely")
        dialog._on_accept()
        assert dialog.user is None
        assert "do not match" in dialog._error.text()
        assert mock_context.auth.needs_setup()
    finally:
        dialog.close()


def test_password_fields_are_masked(qt_app: QApplication, mock_context: ApplicationContext) -> None:
    from PySide6.QtWidgets import QLineEdit

    dialog = LoginDialog(mock_context.auth)
    try:
        assert dialog._password.echoMode() == QLineEdit.EchoMode.Password
    finally:
        dialog.close()


def test_accounts_view_lists_accounts_for_admin(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    users = make_users(mock_context)
    view = UserAccountsView(mock_context.auth, users["admin"])
    try:
        view.load()
        drain(qt_app)
        assert view._table.rowCount() == 3
        names = {view._table.item(row, 0).text() for row in range(view._table.rowCount())}
        assert names == {"boss", "office", "viewer"}
    finally:
        view.close()
