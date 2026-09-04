"""Main window smoke tests.

Business logic lives in the service layer and is tested there; these check that
the window assembles, navigates and shuts down cleanly.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from clockmanager.gui.main_window import MainWindow
from clockmanager.services.application import ApplicationContext
from tests.gui.conftest import drain

pytestmark = pytest.mark.gui

EXPECTED_VIEWS = [
    "Dashboard",
    "Users",
    "Attendance",
    "Live events",
    "Employees",
    "Timesheets",
    "Device settings",
    "Audit log",
    "Diagnostics",
]


def test_window_title_includes_application_name(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    window = MainWindow(mock_context)
    try:
        drain(qt_app)
        assert "NGTeco Clock Manager" in window.windowTitle()
    finally:
        window.close()


def test_all_expected_views_are_present(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    window = MainWindow(mock_context)
    try:
        drain(qt_app)
        labels = [entry.label for entry in window._entries]
        assert labels == EXPECTED_VIEWS
        assert window._stack.count() == len(EXPECTED_VIEWS)
    finally:
        window.close()


def test_navigation_switches_the_visible_view(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    window = MainWindow(mock_context)
    try:
        drain(qt_app)
        assert window.current_view_name == "Dashboard"

        for label in EXPECTED_VIEWS:
            window.show_view(label)
            drain(qt_app)
            assert window.current_view_name == label
    finally:
        window.close()


def test_navigating_to_diagnostics_reports_live_capture_state(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    window = MainWindow(mock_context)
    try:
        drain(qt_app)
        window.show_view("Diagnostics")
        drain(qt_app)
        assert window.diagnostics_view._live_state == "Not running"
    finally:
        window.close()


def test_developer_menu_hidden_by_default(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    """SECURITY.md: diagnostic views are not shown to ordinary users."""
    window = MainWindow(mock_context)
    try:
        drain(qt_app)
        titles = [action.text() for action in window.menuBar().actions()]
        assert "&Developer" not in titles
        assert titles == ["&File", "&View", "&Help"]
    finally:
        window.close()


def test_developer_menu_appears_in_developer_mode(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    from dataclasses import replace

    from clockmanager.services.application import ApplicationContext as Context

    developer_context = Context(
        config=replace(mock_context.config, developer_mode=True),
        database=mock_context.database,
        log_file=mock_context.log_file,
        schema_version=mock_context.schema_version,
    )

    window = MainWindow(developer_context)
    try:
        drain(qt_app)
        titles = [action.text() for action in window.menuBar().actions()]
        assert "&Developer" in titles
    finally:
        window.close()


def test_mock_device_mode_is_announced(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    """An operator must be able to tell they are not looking at real hardware."""
    window = MainWindow(mock_context)
    try:
        drain(qt_app)
        assert "mock device" in window.statusBar().currentMessage()
    finally:
        window.close()


def test_close_stops_live_capture(
    qt_app: QApplication, configured_context: ApplicationContext
) -> None:
    window = MainWindow(configured_context)
    try:
        drain(qt_app)
        window.show_view("Live events")
        window.live_view.start()
        drain(qt_app)
    finally:
        window.close()
        drain(qt_app)

    assert not window.live_view.is_capturing
