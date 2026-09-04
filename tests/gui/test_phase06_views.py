"""Reports view smoke tests (PHASE 06).

Business logic lives in ReportService and is tested there; these check
wiring: that the view generates each report type and exports, off the UI
thread.
"""

from __future__ import annotations

from datetime import date

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from clockmanager.domain.payroll import Employee
from clockmanager.domain.reports import ReportType
from clockmanager.gui.views import ReportsView
from clockmanager.gui.views.reports import resolve_range
from clockmanager.services.application import ApplicationContext
from tests.gui.conftest import drain

pytestmark = pytest.mark.gui


class TestReportsView:
    def test_generates_daily_attendance(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        mock_context.employees.create(Employee(user_id="EMP-001", first_name="Ada"))
        view = ReportsView(mock_context.employees, mock_context.reports)
        view._range_box.setCurrentIndex(view._range_box.findData("custom"))
        view._start.setDate(QDate(2026, 9, 7))
        view._end.setDate(QDate(2026, 9, 13))
        view.generate()
        drain(qt_app)

        assert view._report is not None
        assert view._report.report_type is ReportType.DAILY_ATTENDANCE

    def test_backwards_range_is_refused(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        """The calendar makes a malformed date impossible; an impossible
        *range* is still the operator's to get wrong, so it is reported."""
        view = ReportsView(mock_context.employees, mock_context.reports)
        view._range_box.setCurrentIndex(view._range_box.findData("custom"))
        view._start.setDate(QDate(2026, 9, 13))
        view._end.setDate(QDate(2026, 9, 7))
        view.generate()

        assert "on or after" in view._status.text()
        assert view._report is None

    def test_named_range_fills_the_pickers(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        view = ReportsView(mock_context.employees, mock_context.reports)
        view._range_box.setCurrentIndex(view._range_box.findData("today"))

        assert view._start.date() == QDate.currentDate()
        assert view._end.date() == QDate.currentDate()

    def test_all_dates_clears_the_filter(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        view = ReportsView(mock_context.employees, mock_context.reports)
        view._range_box.setCurrentIndex(view._range_box.findData("all"))
        report_filter = view.current_filter()

        assert report_filter.start is None
        assert report_filter.end is None

    def test_loads_employees(self, qt_app: QApplication, mock_context: ApplicationContext) -> None:
        mock_context.employees.create(Employee(user_id="EMP-001", first_name="Ada"))
        view = ReportsView(mock_context.employees, mock_context.reports)
        view.load_employees()
        drain(qt_app)

        assert view._employee_box.count() == 2  # "All employees" plus Ada
        assert view.current_filter().employee_id is None

        view._employee_box.setCurrentIndex(1)
        assert view.current_filter().employee_id is not None


class TestResolveRange:
    """The named ranges are calendar arithmetic, so they are tested directly."""

    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("today", (date(2026, 9, 9), date(2026, 9, 9))),
            ("yesterday", (date(2026, 9, 8), date(2026, 9, 8))),
            # 2026-09-09 is a Wednesday.
            ("this-week", (date(2026, 9, 7), date(2026, 9, 9))),
            ("last-week", (date(2026, 8, 31), date(2026, 9, 6))),
            ("this-month", (date(2026, 9, 1), date(2026, 9, 9))),
            ("last-30", (date(2026, 8, 11), date(2026, 9, 9))),
        ],
    )
    def test_named_ranges(self, key: str, expected: tuple[date, date]) -> None:
        assert resolve_range(key, date(2026, 9, 9)) == expected

    def test_unknown_range_is_none(self) -> None:
        assert resolve_range("all", date(2026, 9, 9)) is None
