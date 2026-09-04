"""Application views.

Each view is a self-contained widget that talks only to
:mod:`clockmanager.services`. None of them performs blocking I/O on the UI
thread, and none of them writes to a device.
"""

from __future__ import annotations

from clockmanager.gui.views.attendance import AttendanceView
from clockmanager.gui.views.dashboard import DashboardView
from clockmanager.gui.views.device_settings import DeviceSettingsView
from clockmanager.gui.views.diagnostics import DiagnosticsView
from clockmanager.gui.views.live import LiveEventsView
from clockmanager.gui.views.users import UsersView

__all__ = [
    "AttendanceView",
    "DashboardView",
    "DeviceSettingsView",
    "DiagnosticsView",
    "LiveEventsView",
    "UsersView",
]
