"""Main application window.

PHASE 02 turns the PHASE 00 shell into a usable application: a navigation list
on the left, a stack of views on the right, and a status bar.

The window itself holds no business rules and performs no I/O. Views call
:mod:`clockmanager.services`, always off the UI thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QSize, QThreadPool
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

from clockmanager import APPLICATION_NAME, __version__
from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.gui.views import (
    AttendanceView,
    AuditView,
    DashboardView,
    DeviceSettingsView,
    DiagnosticsView,
    LiveEventsView,
    UsersView,
)
from clockmanager.services.application import ApplicationContext

__all__ = ["MainWindow"]

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _NavigationEntry:
    label: str
    widget: QWidget


class MainWindow(QMainWindow):
    """Navigation shell hosting the PHASE 02 views."""

    def __init__(self, context: ApplicationContext) -> None:
        super().__init__()
        self._context = context
        self._service = context.devices

        self.setWindowTitle(f"{APPLICATION_NAME} {__version__}")
        self.resize(1100, 720)

        self._users_service = context.users

        self.dashboard_view = DashboardView(context, self._service, self)
        self.users_view = UsersView(self._service, self._users_service, self)
        self.attendance_view = AttendanceView(self._service, self)
        self.live_view = LiveEventsView(self._service, self)
        self.device_settings_view = DeviceSettingsView(self._service, self)
        self.audit_view = AuditView(context.audit, self)
        self.diagnostics_view = DiagnosticsView(context, self._service, self)

        self._entries = [
            _NavigationEntry("Dashboard", self.dashboard_view),
            _NavigationEntry("Users", self.users_view),
            _NavigationEntry("Attendance", self.attendance_view),
            _NavigationEntry("Live events", self.live_view),
            _NavigationEntry("Device settings", self.device_settings_view),
            _NavigationEntry("Audit log", self.audit_view),
            _NavigationEntry("Diagnostics", self.diagnostics_view),
        ]

        self._navigation = QListWidget(self)
        self._navigation.setMaximumWidth(200)
        self._navigation.setIconSize(QSize(16, 16))
        for entry in self._entries:
            self._navigation.addItem(QListWidgetItem(entry.label))
        self._navigation.currentRowChanged.connect(self._on_navigate)

        self._stack = QStackedWidget(self)
        for entry in self._entries:
            self._stack.addWidget(entry.widget)

        layout = QHBoxLayout()
        layout.addWidget(self._navigation)
        layout.addWidget(self._stack, stretch=1)

        central = QWidget(self)
        central.setLayout(layout)
        self.setCentralWidget(central)

        self._build_menus()
        self._navigation.setCurrentRow(0)
        self._show_ready_message()

    # -- navigation -----------------------------------------------------------

    @property
    def current_view_name(self) -> str:
        row = self._navigation.currentRow()
        return self._entries[row].label if 0 <= row < len(self._entries) else ""

    def show_view(self, label: str) -> None:
        """Switch to a view by name."""
        for row, entry in enumerate(self._entries):
            if entry.label == label:
                self._navigation.setCurrentRow(row)
                return

    def _on_navigate(self, row: int) -> None:
        if not 0 <= row < len(self._entries):
            return
        entry = self._entries[row]
        self._stack.setCurrentWidget(entry.widget)
        self.statusBar().showMessage(entry.label)

        # Keep views that depend on stored settings current.
        if entry.widget is self.dashboard_view:
            self.dashboard_view.refresh()
        elif entry.widget is self.audit_view:
            self.audit_view.refresh()
        elif entry.widget is self.device_settings_view:
            self.device_settings_view.refresh()
        elif entry.widget is self.diagnostics_view:
            self.diagnostics_view.set_live_capture_state(
                "Running" if self.live_view.is_capturing else "Not running"
            )

    # -- menus ----------------------------------------------------------------

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        settings_action = file_menu.addAction("&Device settings")
        settings_action.triggered.connect(lambda: self.show_view("Device settings"))
        file_menu.addSeparator()
        exit_action = file_menu.addAction("E&xit")
        exit_action.triggered.connect(self.close)

        view_menu = self.menuBar().addMenu("&View")
        for entry in self._entries:
            action = view_menu.addAction(entry.label)
            action.triggered.connect(
                lambda _checked=False, label=entry.label: self.show_view(label)
            )

        help_menu = self.menuBar().addMenu("&Help")
        about_action = help_menu.addAction("&About")
        about_action.triggered.connect(self._show_about)

        if self._context.config.developer_mode:
            # SECURITY.md: diagnostic/developer views are administrator-only.
            developer_menu = self.menuBar().addMenu("&Developer")
            schema_action = developer_menu.addAction("Database &metadata")
            schema_action.triggered.connect(self._show_schema_info)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APPLICATION_NAME}",
            f"{APPLICATION_NAME} {__version__}\n\n"
            "Attendance management for NGTeco NG-MB1 devices.\n\n" + self._write_mode_summary(),
        )

    def _write_mode_summary(self) -> str:
        """State plainly whether this installation can change a device."""
        if not self._context.config.enable_device_writes:
            return (
                "Device writing is disabled: this build reads from the clock and never changes it."
            )
        if self._context.config.enable_credential_writes:
            return (
                "Device writing is ENABLED, including PIN changes. Neither has been "
                "verified on real hardware; use disposable test users."
            )
        return (
            "Device user writing is ENABLED. It has not been verified on real "
            "hardware; use disposable test users. PIN writing remains disabled."
        )

    def _show_schema_info(self) -> None:
        info = self._context.schema_info()
        body = "\n".join(f"{key}: {value}" for key, value in sorted(info.items()))
        QMessageBox.information(self, "Database metadata", body or "No metadata recorded.")

    def _show_ready_message(self) -> None:
        parts = ["Ready"]
        if self._context.config.use_mock_device:
            parts.append("using the built-in mock device, not real hardware")
        if self._context.config.enable_device_writes:
            parts.append("device writing ENABLED (unverified)")
        self.statusBar().showMessage(" — ".join(parts))

    # -- lifecycle ------------------------------------------------------------

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self.live_view.shutdown()
        QThreadPool.globalInstance().waitForDone(5000)
        super().closeEvent(event)
