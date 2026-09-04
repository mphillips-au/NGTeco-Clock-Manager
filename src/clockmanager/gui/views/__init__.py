"""Application views.

Each view is a self-contained widget that talks only to
:mod:`clockmanager.services`. None of them performs blocking I/O on the UI
thread.

The Users view can write to a device, but only through
:class:`~clockmanager.services.users.UserService`: the widget never builds a
packet, never decides whether writing is permitted and never skips a
confirmation.
"""

from __future__ import annotations

from clockmanager.gui.views.attendance import AttendanceView
from clockmanager.gui.views.audit import AuditView
from clockmanager.gui.views.dashboard import DashboardView
from clockmanager.gui.views.device_settings import DeviceSettingsView
from clockmanager.gui.views.diagnostics import DiagnosticsView
from clockmanager.gui.views.employees import EmployeesView
from clockmanager.gui.views.live import LiveEventsView
from clockmanager.gui.views.reports import ReportsView
from clockmanager.gui.views.timesheets import TimesheetsView
from clockmanager.gui.views.user_form import UserFormDialog
from clockmanager.gui.views.users import UsersView

__all__ = [
    "AttendanceView",
    "AuditView",
    "DashboardView",
    "DeviceSettingsView",
    "DiagnosticsView",
    "EmployeesView",
    "LiveEventsView",
    "ReportsView",
    "TimesheetsView",
    "UserFormDialog",
    "UsersView",
]
