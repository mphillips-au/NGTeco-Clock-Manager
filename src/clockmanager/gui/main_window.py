"""Main application window.

PHASE 02 turns the PHASE 00 shell into a usable application: a navigation list
on the left, a stack of views on the right, and a status bar.

The window itself holds no business rules and performs no I/O. Views call
:mod:`clockmanager.services`, always off the UI thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QSize, QThreadPool, QTimer
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
from clockmanager.domain.auth import Role
from clockmanager.gui.views import (
    AttendanceView,
    AuditView,
    BackupView,
    DashboardView,
    DeviceSettingsView,
    DiagnosticsView,
    EmployeesView,
    LiveEventsView,
    ReportsView,
    TimesheetsView,
    UserAccountsView,
    UsersView,
)
from clockmanager.services.application import ApplicationContext
from clockmanager.services.auth import AuthenticatedUser

__all__ = ["MainWindow", "visible_views_for"]

_logger = get_logger(__name__)

#: Views every logged-in role may see. Reads need no permission; everything a
#: role may not do is disabled inside the view or refused by the service.
_VIEWER_VIEWS: frozenset[str] = frozenset(
    {"Dashboard", "Users", "Attendance", "Employees", "Timesheets", "Reports"}
)
#: Office staff additionally run live capture and read the audit log.
_OFFICE_VIEWS: frozenset[str] = _VIEWER_VIEWS | {"Live events", "Audit log"}
#: The eleven pre-PHASE-07 views, in navigation order. Backup is
#: administrator-only (it holds the full database copy); office staff and
#: viewers never see it.
_LEGACY_VIEWS: tuple[str, ...] = (
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
)


def visible_views_for(role: Role | None) -> frozenset[str]:
    """Return the navigation labels ``role`` may see.

    ``None`` is the pre-login/test path and sees the ten legacy views.
    Administrators see those plus User accounts.
    """
    if role is None:
        return frozenset(_LEGACY_VIEWS)
    if role == Role.ADMIN:
        return frozenset(_LEGACY_VIEWS) | {"User accounts"}
    if role == Role.OFFICE_STAFF:
        return _OFFICE_VIEWS
    return _VIEWER_VIEWS


@dataclass(frozen=True, slots=True)
class _NavigationEntry:
    label: str
    widget: QWidget


class MainWindow(QMainWindow):
    """Navigation shell hosting the application views."""

    def __init__(
        self,
        context: ApplicationContext,
        current_user: AuthenticatedUser | None = None,
    ) -> None:
        super().__init__()
        self._context = context
        self._service = context.devices
        self._sync_service = context.sync
        #: Who is logged in. ``None`` is the pre-login/test path: every
        #: legacy view is shown and nothing is role-gated.
        self._current_user = current_user
        self._role = current_user.role if current_user is not None else None
        self._logout_requested = False

        self.setWindowTitle(f"{APPLICATION_NAME} {__version__}")
        self.resize(1100, 720)

        self._users_service = context.users
        role = self._role

        self.dashboard_view = DashboardView(context, self._service, self)
        self.users_view = UsersView(self._service, self._users_service, self, role=role)
        self.attendance_view = AttendanceView(self._service, self._sync_service, self, role=role)
        self.live_view = LiveEventsView(self._service, self._sync_service, self, role=role)
        self.employees_view = EmployeesView(context.employees, self, role=role)
        self.timesheets_view = TimesheetsView(context.employees, context.timesheets, self)
        self.reports_view = ReportsView(context.employees, context.reports, self, role=role)
        self.device_settings_view = DeviceSettingsView(self._service, self, role=role)
        self.audit_view = AuditView(context.audit, self)
        self.diagnostics_view = DiagnosticsView(context, self._service, self, role=role)
        self.backup_view = BackupView(context, self, role=role)
        self.accounts_view: UserAccountsView | None = None
        if current_user is not None and self._role == Role.ADMIN:
            self.accounts_view = UserAccountsView(context.auth, current_user, self)

        all_entries = [
            _NavigationEntry("Dashboard", self.dashboard_view),
            _NavigationEntry("Users", self.users_view),
            _NavigationEntry("Attendance", self.attendance_view),
            _NavigationEntry("Live events", self.live_view),
            _NavigationEntry("Employees", self.employees_view),
            _NavigationEntry("Timesheets", self.timesheets_view),
            _NavigationEntry("Reports", self.reports_view),
            _NavigationEntry("Device settings", self.device_settings_view),
            _NavigationEntry("Audit log", self.audit_view),
            _NavigationEntry("Diagnostics", self.diagnostics_view),
            _NavigationEntry("Backup", self.backup_view),
        ]
        if self.accounts_view is not None:
            all_entries.append(_NavigationEntry("User accounts", self.accounts_view))

        # Hidden restricted screens (PHASE 07): a role that may not see a
        # view gets no navigation entry and no menu item for it. The widget
        # is still constructed so background machinery (live-capture state,
        # background sync guards) keeps working.
        visible = visible_views_for(self._role)
        self._entries = [entry for entry in all_entries if entry.label in visible]

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
        self._start_background_sync()

    def _start_background_sync(self) -> None:
        """Periodically sync when the profile's interval has elapsed.

        Runs off the UI thread and only when due, so a quiet device costs one
        cheap history read per minute and nothing more. Failures stay in the
        log and the sync history; they never pop up while the operator works.
        """
        self._background_timer = QTimer(self)
        self._background_timer.setInterval(60_000)
        self._background_timer.timeout.connect(self._maybe_background_sync)
        self._background_timer.start()

    def _maybe_background_sync(self) -> None:
        from clockmanager.gui.views.common import run_off_thread

        if self._role == Role.VIEWER:
            return  # viewers are read-only: no automatic writes, even local ones
        profile = self._service.first_enabled_profile()
        if profile is None or not profile.is_configured or profile.device_id is None:
            return
        if self.live_view.is_capturing:
            return  # live capture owns the connection; the next sync recovers

        def _work() -> str | None:
            result = self._sync_service.background_sync_if_due(profile)
            return None if result is None else result.summary

        def _done(summary: object) -> None:
            if isinstance(summary, str):
                _logger.info("Background sync finished", extra={"summary": summary})

        def _failed(message: str) -> None:
            _logger.warning("Background sync failed", extra={"error": message})

        run_off_thread(_work, on_success=_done, on_failure=_failed)

    # -- navigation -----------------------------------------------------------

    @property
    def current_view_name(self) -> str:
        row = self._navigation.currentRow()
        return self._entries[row].label if 0 <= row < len(self._entries) else ""

    def show_view(self, label: str) -> None:
        """Switch to a view by name. Hidden restricted screens refuse."""
        for row, entry in enumerate(self._entries):
            if entry.label == label:
                self._navigation.setCurrentRow(row)
                return
        self.statusBar().showMessage(f"{label} is not available for your role.")

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
        elif entry.widget is self.attendance_view:
            self.attendance_view.load()
        elif entry.widget is self.employees_view:
            self.employees_view.load()
        elif entry.widget is self.timesheets_view:
            self.timesheets_view.load_employees()
        elif entry.widget is self.reports_view:
            self.reports_view.load_employees()
        elif entry.widget is self.device_settings_view:
            self.device_settings_view.refresh()
        elif entry.widget is self.diagnostics_view:
            self.diagnostics_view.set_live_capture_state(
                "Running" if self.live_view.is_capturing else "Not running"
            )
        elif entry.widget is self.backup_view:
            self.backup_view.load()
        elif self.accounts_view is not None and entry.widget is self.accounts_view:
            self.accounts_view.load()

    # -- menus ----------------------------------------------------------------

    def _build_menus(self) -> None:
        labels = {entry.label for entry in self._entries}
        file_menu = self.menuBar().addMenu("&File")
        if "Device settings" in labels:
            settings_action = file_menu.addAction("&Device settings")
            settings_action.triggered.connect(lambda: self.show_view("Device settings"))
            file_menu.addSeparator()
        if self._current_user is not None:
            logout_action = file_menu.addAction("&Logout")
            logout_action.triggered.connect(self._logout)
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

        # SECURITY.md: diagnostic/developer views are administrator-only.
        # The pre-login/test path (no identity) keeps the legacy gate.
        if self._context.config.developer_mode and (self._role is None or self._role == Role.ADMIN):
            developer_menu = self.menuBar().addMenu("&Developer")
            schema_action = developer_menu.addAction("Database &metadata")
            schema_action.triggered.connect(self._show_schema_info)

    @property
    def logout_requested(self) -> bool:
        """Whether the operator chose Logout rather than closing."""
        return self._logout_requested

    def _logout(self) -> None:
        """Close so the login screen returns. Audited by the caller."""
        self._logout_requested = True
        self.close()

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
        if self._current_user is not None:
            parts.append(
                f"logged in as {self._current_user.username} ({self._current_user.role.label})"
            )
        if self._context.config.use_mock_device:
            parts.append("using the built-in mock device, not real hardware")
        if self._context.config.enable_device_writes:
            parts.append("device writing ENABLED (unverified)")
        self.statusBar().showMessage(" — ".join(parts))

    # -- lifecycle ------------------------------------------------------------

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        timer = getattr(self, "_background_timer", None)
        if timer is not None:
            timer.stop()
        self.live_view.shutdown()
        QThreadPool.globalInstance().waitForDone(5000)
        super().closeEvent(event)
