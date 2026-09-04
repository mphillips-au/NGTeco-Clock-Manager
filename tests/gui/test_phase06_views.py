"""Reports view smoke tests (PHASE 06).

Business logic lives in ReportService and is tested there; these check
wiring: that the view generates each report type and exports, off the UI
thread.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from clockmanager.domain.payroll import Employee
from clockmanager.domain.reports import ReportType
from clockmanager.gui.views import ReportsView
from clockmanager.services.application import ApplicationContext
from tests.gui.conftest import drain

pytestmark = pytest.mark.gui


class TestReportsView:
    def test_generates_daily_attendance(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        mock_context.employees.create(Employee(user_id="EMP-001", first_name="Ada"))
        view = ReportsView(mock_context.employees, mock_context.reports)
        view._start.setText("2026-09-07")
        view._end.setText("2026-09-13")
        view.generate()
        drain(qt_app)

        assert view._report is not None
        assert view._report.report_type is ReportType.DAILY_ATTENDANCE

    def test_bad_date_is_reported(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        view = ReportsView(mock_context.employees, mock_context.reports)
        view._start.setText("not-a-date")
        view.generate()

        assert "YYYY-MM-DD" in view._status.text()

    def test_loads_employees(self, qt_app: QApplication, mock_context: ApplicationContext) -> None:
        mock_context.employees.create(Employee(user_id="EMP-001", first_name="Ada"))
        view = ReportsView(mock_context.employees, mock_context.reports)
        view.load_employees()
        drain(qt_app)

        assert view._employee_box.count() == 2  # "All employees" plus Ada
