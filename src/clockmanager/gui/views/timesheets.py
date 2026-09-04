"""Timesheets view (PHASE 05).

Derived, recalculable timesheets over immutable stored attendance: pick an
employee and a pay period, then show one row per calendar day plus totals.
Hours render as HH:MM or decimal according to the active pay schedule. All
calculation goes through ``TimesheetService`` off the UI thread.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clockmanager.domain.payroll import format_hours
from clockmanager.gui.views.common import (
    build_table,
    fill_table,
    page_header,
    primary_button,
    run_off_thread,
    set_status,
)
from clockmanager.services.employees import EmployeeProfile, EmployeeService
from clockmanager.services.timesheets import TimesheetRequest, TimesheetService

__all__ = ["TimesheetsView"]

_HEADERS = ("Date", "First IN", "Last OUT", "Worked", "OT", "Missing", "Flags")


class TimesheetsView(QWidget):
    """Employee + pay-period timesheet browser."""

    def __init__(
        self,
        employees: EmployeeService,
        timesheets: TimesheetService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._employees = employees
        self._timesheets = timesheets
        self._profiles: list[EmployeeProfile] = []

        self._employee_box = QComboBox(self)
        self._period_box = QComboBox(self)
        self._period_box.addItem("Current pay period", "current")
        self._period_box.addItem("Previous pay period", "previous")
        self._calculate_button = primary_button("Calculate", self)
        self._calculate_button.clicked.connect(self.calculate)
        self._refresh_button = QPushButton("Refresh employees", self)
        self._refresh_button.clicked.connect(self.load_employees)

        self._table = build_table(_HEADERS, self)
        self._status = QLabel("Select an employee and calculate their timesheet.", self)
        self._status.setWordWrap(True)
        self._totals = QLabel("", self)
        self._totals.setWordWrap(True)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Employee:", self))
        controls.addWidget(self._employee_box, stretch=1)
        controls.addWidget(QLabel("Period:", self))
        controls.addWidget(self._period_box)
        controls.addWidget(self._calculate_button)
        controls.addWidget(self._refresh_button)

        layout = QVBoxLayout()
        layout.addWidget(
            page_header(
                "Timesheets",
                "Hours worked, derived from stored punches. The underlying attendance is never altered.",
            )
        )
        layout.addLayout(controls)
        layout.addWidget(self._status)
        layout.addWidget(self._totals)
        layout.addWidget(self._table, stretch=1)
        self.setLayout(layout)
        fill_table(
            self._table,
            [],
            empty_message="Pick an employee and a period, then press Calculate.",
        )

    def load_employees(self) -> None:
        run_off_thread(
            self._employees.list_employees,
            on_success=self._on_employees,
            on_failure=self._on_failure,
        )

    def calculate(self) -> None:
        index = self._employee_box.currentIndex()
        if index < 0 or index >= len(self._profiles):
            set_status(self._status, "Add an employee first, then calculate.", "warning")
            return
        profile = self._profiles[index]
        which = self._period_box.currentData()
        self._calculate_button.setEnabled(False)
        set_status(self._status, f"Calculating timesheet for {profile.display_name}…", "loading")
        run_off_thread(
            lambda: self._build(profile, which),
            on_success=self._on_calculated,
            on_failure=self._on_failure,
        )

    def _build(self, profile: EmployeeProfile, which: object) -> Any:
        schedule = self._timesheets.ensure_default_schedule()
        today = datetime.now(UTC).date()
        period = self._timesheets.period_for(today, schedule)
        if which == "previous":
            probe = period.start - timedelta(days=1)
            period = self._timesheets.period_for(probe, schedule)
        timesheet = self._timesheets.build(
            TimesheetRequest(employee_id=profile.employee_id, start=period.start, end=period.end),
            schedule,
        )
        return (profile, schedule, timesheet)

    def _on_employees(self, profiles: Any) -> None:
        if not isinstance(profiles, list):  # pragma: no cover - defensive
            return
        self._profiles = [p for p in profiles if p.active]
        self._employee_box.clear()
        for profile in self._profiles:
            self._employee_box.addItem(f"{profile.display_name} ({profile.user_id})")
        self._status.setText(
            f"{len(self._profiles)} active employee(s). Select one and calculate."
            if self._profiles
            else "No active employees. Add one in the Employees view, then refresh."
        )

    def _on_calculated(self, payload: Any) -> None:
        self._calculate_button.setEnabled(True)
        if not isinstance(payload, tuple) or len(payload) != 3:  # pragma: no cover - defensive
            return
        profile, schedule, timesheet = payload
        summary = timesheet.summary
        rows: list[list[str]] = []
        for daily in summary.days:
            flags = ",".join(
                [
                    part
                    for part in (
                        f"missing x{daily.missing_punches}" if daily.missing_punches else "",
                        f"dup x{daily.duplicate_punches}" if daily.duplicate_punches else "",
                        f"excessive x{daily.excessive_shifts}" if daily.excessive_shifts else "",
                        "overnight" if daily.overnight_shifts else "",
                    )
                    if part
                ]
            )
            rows.append(
                [
                    daily.day.isoformat(),
                    "" if daily.first_in is None else daily.first_in.strftime("%H:%M"),
                    "" if daily.last_out is None else daily.last_out.strftime("%H:%M"),
                    format_hours(daily.worked_seconds / 3600.0, decimal=schedule.display_decimal),
                    format_hours(daily.overtime_seconds / 3600.0, decimal=schedule.display_decimal),
                    str(daily.missing_punches),
                    flags,
                ]
            )
        fill_table(
            self._table,
            rows,
            empty_message="No worked days in this period for this employee.",
        )
        period = summary.period
        set_status(
            self._status,
            f"{profile.display_name}: pay period {period.start.isoformat()} to "
            f"{period.end.isoformat()} ({schedule.schedule_type}, {schedule.timezone}).",
            "success",
        )
        self._totals.setText(
            "Total "
            + format_hours(summary.total_seconds / 3600.0, decimal=schedule.display_decimal)
            + " — overtime "
            + format_hours(summary.overtime_seconds / 3600.0, decimal=schedule.display_decimal)
            + " — regular "
            + format_hours(summary.regular_seconds / 3600.0, decimal=schedule.display_decimal)
            + "."
        )

    def _on_failure(self, message: str) -> None:
        self._calculate_button.setEnabled(True)
        set_status(self._status, message, "error")
