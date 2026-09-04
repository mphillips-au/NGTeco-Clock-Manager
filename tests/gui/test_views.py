"""View smoke tests.

TESTING.md keeps business logic in the service layer, so these check wiring:
that each view loads, shows what the mock device reports, and never blocks the
UI thread or exposes a credential.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QLineEdit

from clockmanager.gui.views import (
    AttendanceView,
    DashboardView,
    DeviceSettingsView,
    DiagnosticsView,
    LiveEventsView,
    UsersView,
)
from clockmanager.services.application import ApplicationContext
from clockmanager.services.devices import DeviceProfile
from tests.gui.conftest import drain

pytestmark = pytest.mark.gui


def _table_text(table) -> str:  # type: ignore[no-untyped-def]
    return "\n".join(
        table.item(row, column).text() if table.item(row, column) else ""
        for row in range(table.rowCount())
        for column in range(table.columnCount())
    )


class TestDashboard:
    def test_reports_when_no_device_is_configured(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        view = DashboardView(mock_context, mock_context.devices)
        drain(qt_app)
        assert "No device is configured" in view._device_summary.text()

    def test_shows_the_configured_device(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        view = DashboardView(configured_context, configured_context.devices)
        drain(qt_app)

        assert "Bench clock" in view._device_summary.text()
        text = _table_text(view._device_table)
        assert "192.0.2.10:4370" in text

    def test_check_connection_populates_device_info(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        view = DashboardView(configured_context, configured_context.devices)
        drain(qt_app)
        view.check_connection()
        drain(qt_app)

        assert "Connected" in view._device_summary.text()
        assert "ZMM510_TFT" in _table_text(view._device_table)


class TestUsersView:
    def test_loads_users_from_the_device(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        view = UsersView(configured_context.devices, configured_context.users)
        view.load()
        drain(qt_app)

        assert view._table.rowCount() == 5
        assert "Lovelace" in _table_text(view._table)
        assert "5 user(s)" in view._status.text()

    def test_shows_privilege_labels(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        view = UsersView(configured_context.devices, configured_context.users)
        view.load()
        drain(qt_app)
        text = _table_text(view._table)
        assert "Admin" in text
        assert "Employee" in text

    def test_filter_narrows_the_list(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        view = UsersView(configured_context.devices, configured_context.users)
        view.load()
        drain(qt_app)

        view._filter.setText("Hopper")
        assert view._table.rowCount() == 1

    def test_reports_when_no_device_is_configured(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        view = UsersView(mock_context.devices, mock_context.users)
        view.load()
        drain(qt_app)
        assert "No device is configured" in view._status.text()

    def test_never_shows_credential_contents(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        """SECURITY.md: only presence is shown, never the region's contents."""
        view = UsersView(configured_context.devices, configured_context.users)
        view.load()
        drain(qt_app)

        headers = [
            view._table.horizontalHeaderItem(column).text()
            for column in range(view._table.columnCount())
        ]
        assert headers == [
            "UID",
            "User ID",
            "First name",
            "Last name",
            "Privilege",
            "PIN set",
        ]
        assert set(_table_text(view._table).split()) & {"Yes", "No"}


class TestAttendanceView:
    def test_loads_attendance(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        view = AttendanceView(configured_context.devices)
        view.load()
        drain(qt_app)

        assert view._table.rowCount() > 0
        assert "IN" in view._status.text()
        assert "OUT" in view._status.text()

    def test_shows_raw_status_column(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        view = AttendanceView(configured_context.devices)
        headers = [
            view._table.horizontalHeaderItem(column).text()
            for column in range(view._table.columnCount())
        ]
        assert "Status (raw)" in headers

    def test_filter_narrows_the_list(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        view = AttendanceView(configured_context.devices)
        view.load()
        drain(qt_app)
        total = view._table.rowCount()

        view._filter.setText("1001")
        assert 0 < view._table.rowCount() < total


class TestDeviceSettingsView:
    def test_password_field_is_masked(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        """SECURITY.md: sensitive fields must be masked in the GUI."""
        view = DeviceSettingsView(mock_context.devices)
        drain(qt_app)
        assert view._password.echoMode() == QLineEdit.EchoMode.Password

    def test_exposes_every_phase_02_setting(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        view = DeviceSettingsView(mock_context.devices)
        drain(qt_app)
        for field in (
            view._name,
            view._host,
            view._port,
            view._password,
            view._timeout,
            view._auto_reconnect,
            view._sync_interval,
            view._enabled,
        ):
            assert field is not None

    def test_saving_persists_the_profile(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        view = DeviceSettingsView(mock_context.devices)
        drain(qt_app)

        view._name.setText("Workshop clock")
        view._host.setText("192.0.2.30")
        view._port.setValue(4371)
        view._save()
        drain(qt_app)

        profiles = mock_context.devices.list_profiles()
        assert [p.name for p in profiles] == ["Workshop clock"]
        assert profiles[0].port == 4371

    def test_invalid_input_is_reported_and_not_saved(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        view = DeviceSettingsView(mock_context.devices)
        drain(qt_app)

        view._name.setText("No address")
        view._host.setText("")
        view._save()
        drain(qt_app)

        assert "IP address" in view._status.text()
        assert mock_context.devices.list_profiles() == []

    def test_non_numeric_password_is_rejected(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        view = DeviceSettingsView(mock_context.devices)
        drain(qt_app)

        view._name.setText("Bench")
        view._host.setText("192.0.2.10")
        view._password.setText("not-a-number")
        view._save()
        drain(qt_app)

        assert "must be a number" in view._status.text()
        assert mock_context.devices.list_profiles() == []

    def test_stored_password_is_never_echoed_back_into_the_form(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        mock_context.devices.save_profile(
            DeviceProfile(name="Bench clock", host="192.0.2.10", communication_password=4321)
        )
        view = DeviceSettingsView(mock_context.devices)
        drain(qt_app)

        assert view._password.text() == ""
        assert "4321" not in view._password.placeholderText()
        assert "Unchanged" in view._password.placeholderText()

    def test_leaving_the_password_blank_keeps_the_stored_value(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        mock_context.devices.save_profile(
            DeviceProfile(name="Bench clock", host="192.0.2.10", communication_password=4321)
        )
        view = DeviceSettingsView(mock_context.devices)
        drain(qt_app)

        view._host.setText("192.0.2.11")
        view._save()
        drain(qt_app)

        stored = mock_context.devices.list_profiles()[0]
        assert stored.host == "192.0.2.11"
        assert stored.communication_password == 4321

    def test_test_connection_reports_the_result(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        view = DeviceSettingsView(mock_context.devices)
        drain(qt_app)

        view._name.setText("Bench clock")
        view._host.setText("192.0.2.10")
        view._test_connection()
        drain(qt_app)

        assert "Connected" in view._status.text()


class TestDiagnosticsView:
    def test_connection_check(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        view = DiagnosticsView(configured_context, configured_context.devices)
        drain(qt_app)
        view._test_connection()
        drain(qt_app)

        assert "Connected" in view._status.text()
        assert "ZMM510_TFT" in _table_text(view._results)

    def test_user_count_check(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        view = DiagnosticsView(configured_context, configured_context.devices)
        view._count_users()
        drain(qt_app)

        text = _table_text(view._results)
        assert "Users read" in text
        assert "120-byte" in text

    def test_attendance_count_check(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        view = DiagnosticsView(configured_context, configured_context.devices)
        view._count_attendance()
        drain(qt_app)
        assert "Attendance records" in _table_text(view._results)

    def test_device_time_check(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        view = DiagnosticsView(configured_context, configured_context.devices)
        view._device_time()
        drain(qt_app)
        assert "Device time" in _table_text(view._results)

    def test_capabilities_check_reports_unsupported_writes(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        view = DiagnosticsView(configured_context, configured_context.devices)
        view._capabilities()
        drain(qt_app)

        text = _table_text(view._results)
        assert "write_users" in text
        assert "UNSUPPORTED" in text
        assert "Live capture state" in text

    def test_reports_when_no_device_is_configured(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        view = DiagnosticsView(mock_context, mock_context.devices)
        view._test_connection()
        drain(qt_app)
        assert "No device is configured" in view._status.text()

    def test_log_tail_is_shown(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        view = DiagnosticsView(configured_context, configured_context.devices)
        view.refresh_logs()
        drain(qt_app)
        assert view._log_view.toPlainText()

    def test_developer_detail_is_hidden_by_default(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        """SECURITY.md: diagnostic detail is administrator-only."""
        assert configured_context.config.developer_mode is False
        view = DiagnosticsView(configured_context, configured_context.devices)
        view._capabilities()
        drain(qt_app)

        text = _table_text(view._results)
        assert "UNSUPPORTED" in text
        # The reason text is developer-only detail.
        assert "wrong user packet shape" not in text


class TestLiveEventsView:
    def test_starts_and_stops(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        view = LiveEventsView(configured_context.devices)
        view.start()
        drain(qt_app)
        view.shutdown()
        drain(qt_app)
        assert not view.is_capturing

    def test_reports_when_no_device_is_configured(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        view = LiveEventsView(mock_context.devices)
        view.start()
        drain(qt_app)
        assert "No device is configured" in view._state.text()
        assert not view.is_capturing

    def test_shutdown_is_safe_when_never_started(
        self, qt_app: QApplication, configured_context: ApplicationContext
    ) -> None:
        LiveEventsView(configured_context.devices).shutdown()
