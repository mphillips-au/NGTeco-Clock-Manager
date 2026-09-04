"""Employees and Timesheets view smoke tests (PHASE 05).

Business logic lives in the service layer and is tested there; these check
wiring: that each view loads, calculates, filters, and never blocks the UI
thread.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from PySide6.QtWidgets import QApplication

from clockmanager.domain.payroll import Employee
from clockmanager.gui.views import EmployeesView, TimesheetsView
from clockmanager.persistence.models import AttendanceEventRecord, DeviceRecord
from clockmanager.services.application import ApplicationContext
from tests.gui.conftest import drain

pytestmark = pytest.mark.gui


def _table_text(table) -> str:  # type: ignore[no-untyped-def]
    return "\n".join(
        table.item(row, column).text() if table.item(row, column) else ""
        for row in range(table.rowCount())
        for column in range(table.columnCount())
    )


def _seed_punches(context: ApplicationContext, *, user_id: str) -> None:
    """Store one 09:00-17:00 day directly, bypassing the device.

    The day is today: the view calculates the current pay period, so fixed
    dates would fall outside it.
    """
    today = datetime.now(UTC).date()
    with context.database.session() as session:
        device = DeviceRecord(name="Bench clock")
        session.add(device)
        session.flush()
        device_id = device.id
        for hour, punch in ((9, 0), (17, 1)):
            # Naive: device-local wall time is naive by construction (STATUS.md).
            moment = datetime(today.year, today.month, today.day, hour, 0)  # noqa: DTZ001
            session.add(
                AttendanceEventRecord(
                    device_id=device_id,
                    device_uid=None,
                    user_id=user_id,
                    occurred_at=moment,
                    punch=punch,
                    status=0,
                    received_at=moment,
                    source="historical",
                    event_key=f"{user_id}-{moment.isoformat()}-{punch}",
                    employee_name=None,
                )
            )


class TestEmployeesView:
    def test_loads_and_filters_employees(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        mock_context.employees.create(Employee(user_id="EMP-001", first_name="Ada"))
        mock_context.employees.create(Employee(user_id="EMP-002", first_name="Grace"))
        view = EmployeesView(mock_context.employees)
        view.load()
        drain(qt_app)

        assert "2 employee(s)" in view._status.text()
        view._filter.setText("ada")
        assert view._table.rowCount() == 1

    def test_form_rejects_an_empty_user_id(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        from clockmanager.gui.views.employees import EmployeeFormDialog

        dialog = EmployeeFormDialog()
        drain(qt_app)
        # An empty form cannot produce an employee: the domain rule refuses it.
        with pytest.raises(ValueError, match="user_id"):
            Employee(user_id="   ")
        dialog.close()


class TestTimesheetsView:
    def test_calculates_a_timesheet(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        profile = mock_context.employees.create(Employee(user_id="EMP-001", first_name="Ada"))
        _seed_punches(mock_context, user_id="EMP-001")
        view = TimesheetsView(mock_context.employees, mock_context.timesheets)
        view.load_employees()
        drain(qt_app)

        assert view._employee_box.count() == 1
        view.calculate()
        drain(qt_app)

        assert "EMP-001" in view._status.text() or "Ada" in view._status.text()
        assert "8:00" in view._totals.text()
        assert profile.user_id in view._status.text() or "pay period" in view._status.text()

    def test_reports_when_no_employee_exists(
        self, qt_app: QApplication, mock_context: ApplicationContext
    ) -> None:
        view = TimesheetsView(mock_context.employees, mock_context.timesheets)
        view.load_employees()
        drain(qt_app)
        view.calculate()
        drain(qt_app)

        assert "Add an employee first" in view._status.text()
