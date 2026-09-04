"""Reports view (PHASE 06).

Derived, read-only reporting over immutable stored attendance plus the
append-only sync/audit history: pick a report, set filters, generate, then
export to CSV/XLSX/PDF/JSON. All building and exporting goes through
``ReportService`` off the UI thread; the widget never queries the database
or touches raw attendance directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clockmanager.domain.auth import Role, normalise_role
from clockmanager.domain.reports import ExportFormat, Report, ReportFilter, ReportType
from clockmanager.gui.views.common import build_table, fill_table, run_off_thread, section_label
from clockmanager.services.employees import EmployeeService
from clockmanager.services.reports import ReportService

__all__ = ["ReportsView"]

_REPORT_ITEMS: tuple[tuple[str, ReportType], ...] = (
    ("Daily attendance", ReportType.DAILY_ATTENDANCE),
    ("Employee timesheet", ReportType.EMPLOYEE_TIMESHEET),
    ("Weekly summary", ReportType.WEEKLY_SUMMARY),
    ("Pay-period summary", ReportType.PAY_PERIOD_SUMMARY),
    ("Exceptions", ReportType.EXCEPTIONS),
    ("Device activity", ReportType.DEVICE_ACTIVITY),
    ("Sync history", ReportType.SYNC_HISTORY),
    ("Audit trail", ReportType.AUDIT),
)


class ReportsView(QWidget):
    """Report browser with filters and file exports."""

    def __init__(
        self,
        employees: EmployeeService,
        reports: ReportService,
        parent: QWidget | None = None,
        *,
        role: Role | str | None = None,
    ) -> None:
        super().__init__(parent)
        self._employees = employees
        self._reports = reports
        #: The logged-in role, carried into exports for the audit trail.
        #: Every role may export (it mutates nothing); ``None`` keeps the
        #: legacy behaviour for tests.
        self._role = normalise_role(role) if role is not None else None
        self._report: Report | None = None
        self._employee_ids: list[int] = []

        self._report_box = QComboBox(self)
        for label, kind in _REPORT_ITEMS:
            self._report_box.addItem(label, kind.value)

        self._start = QLineEdit(self)
        self._start.setPlaceholderText("Start YYYY-MM-DD")
        self._end = QLineEdit(self)
        self._end.setPlaceholderText("End YYYY-MM-DD")
        self._employee_box = QComboBox(self)
        self._department = QLineEdit(self)
        self._department.setPlaceholderText("Department (optional)")
        self._exceptions_only = QCheckBox("Exceptions only", self)

        self._generate_button = QPushButton("Generate", self)
        self._generate_button.clicked.connect(self.generate)
        self._refresh_employees_button = QPushButton("Reload employees", self)
        self._refresh_employees_button.clicked.connect(self.load_employees)

        self._export_csv = QPushButton("CSV", self)
        self._export_csv.clicked.connect(lambda: self.export(ExportFormat.CSV))
        self._export_xlsx = QPushButton("XLSX", self)
        self._export_xlsx.clicked.connect(lambda: self.export(ExportFormat.XLSX))
        self._export_pdf = QPushButton("PDF", self)
        self._export_pdf.clicked.connect(lambda: self.export(ExportFormat.PDF))
        self._export_json = QPushButton("JSON", self)
        self._export_json.clicked.connect(lambda: self.export(ExportFormat.JSON))

        self._table = build_table(("Report",), self)
        self._status = QLabel("Pick a report, set filters, then Generate.", self)
        self._status.setWordWrap(True)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("Report:", self))
        filters.addWidget(self._report_box)
        filters.addWidget(QLabel("Start:", self))
        filters.addWidget(self._start)
        filters.addWidget(QLabel("End:", self))
        filters.addWidget(self._end)

        filters2 = QHBoxLayout()
        filters2.addWidget(QLabel("Employee:", self))
        filters2.addWidget(self._employee_box, stretch=1)
        filters2.addWidget(self._department)
        filters2.addWidget(self._exceptions_only)
        filters2.addWidget(self._generate_button)
        filters2.addWidget(self._refresh_employees_button)

        exports = QHBoxLayout()
        exports.addWidget(QLabel("Export:", self))
        exports.addWidget(self._export_csv)
        exports.addWidget(self._export_xlsx)
        exports.addWidget(self._export_pdf)
        exports.addWidget(self._export_json)
        exports.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(section_label("Reports (derived — raw attendance is never changed)", self))
        layout.addLayout(filters)
        layout.addLayout(filters2)
        layout.addLayout(exports)
        layout.addWidget(self._status)
        layout.addWidget(self._table, stretch=1)
        self.setLayout(layout)
        self._set_exports_enabled(False)

    def load_employees(self) -> None:
        run_off_thread(
            self._employees.list_employees,
            on_success=self._on_employees,
            on_failure=self._on_failure,
        )

    def current_filter(self) -> ReportFilter:
        """Parse the filter widgets. Raises ValueError on bad dates."""
        start = _parse_date(self._start.text().strip(), "Start")
        end = _parse_date(self._end.text().strip(), "End")
        index = self._employee_box.currentIndex()
        employee_id = self._employee_ids[index] if 0 <= index < len(self._employee_ids) else None
        return ReportFilter(
            start=start,
            end=end,
            employee_id=employee_id,
            department=self._department.text().strip(),
            exception_only=self._exceptions_only.isChecked(),
        )

    def generate(self) -> None:
        try:
            filt = self.current_filter()
        except ValueError as exc:
            self._status.setText(str(exc))
            return
        kind = ReportType(self._report_box.currentData())
        self._generate_button.setEnabled(False)
        self._status.setText("Building report…")
        run_off_thread(
            lambda: self._build(kind, filt),
            on_success=self._on_built,
            on_failure=self._on_failure,
        )

    def export(self, fmt: ExportFormat) -> None:
        report = self._report
        if report is None:
            self._status.setText("Generate a report first, then export it.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export {fmt.value.upper()}", f"report.{fmt.value}", f"*.{fmt.value}"
        )
        if not path:
            return
        self._status.setText(f"Exporting {fmt.value.upper()}…")
        run_off_thread(
            lambda: self._do_export(report, fmt, path),
            on_success=self._on_exported,
            on_failure=self._on_failure,
        )

    def _build(self, kind: ReportType, filt: ReportFilter) -> Report:
        match kind:
            case ReportType.DAILY_ATTENDANCE:
                return self._reports.daily_attendance(filt)
            case ReportType.EMPLOYEE_TIMESHEET:
                if filt.employee_id is None:
                    raise ValueError(
                        "Employee timesheet needs an employee: Reload employees and pick one."
                    )
                if filt.start is None or filt.end is None:
                    raise ValueError("Employee timesheet needs a start and end date.")
                return self._reports.employee_timesheet(filt.employee_id, filt.start, filt.end)
            case ReportType.WEEKLY_SUMMARY:
                return self._reports.weekly_summary(filt)
            case ReportType.PAY_PERIOD_SUMMARY:
                return self._reports.pay_period_summary(filt)
            case ReportType.EXCEPTIONS:
                return self._reports.exceptions(filt)
            case ReportType.DEVICE_ACTIVITY:
                return self._reports.device_activity(filt)
            case ReportType.SYNC_HISTORY:
                return self._reports.sync_history(filt)
            case ReportType.AUDIT:
                return self._reports.audit_report(filt)

    def _do_export(self, report: Report, fmt: ExportFormat, path: str) -> str:
        data, filename, _mime = self._reports.export(report, fmt, requester_role=self._role)
        target = Path(path)
        target.write_bytes(data)
        return f"Exported {len(data)} byte(s) to {target.name or filename}."

    def _on_employees(self, profiles: Any) -> None:
        if not isinstance(profiles, list):  # pragma: no cover - defensive
            return
        actives = [p for p in profiles if p.active]
        self._employee_ids = [-1]
        self._employee_box.clear()
        self._employee_box.addItem("All employees", None)
        for profile in actives:
            self._employee_ids.append(profile.employee_id)
            self._employee_box.addItem(f"{profile.display_name} ({profile.user_id})")
        if len(self._employee_ids) == 1:
            self._employee_ids = []
            self._employee_box.clear()
            self._employee_box.addItem("No employees yet", None)

    def _on_built(self, report: Any) -> None:
        from clockmanager.domain.reports import Report as _Report

        self._generate_button.setEnabled(True)
        if not isinstance(report, _Report):  # pragma: no cover - defensive
            return
        self._report = report
        table = build_table(report.columns or ("Report",), self)
        self.layout().replaceWidget(self._table, table)  # type: ignore[union-attr]
        self._table.deleteLater()
        self._table = table
        fill_table(self._table, report.rows)
        stamp = report.generated_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        self._status.setText(f"{report.title} — {report.summary} Built {stamp}.")
        self._set_exports_enabled(True)

    def _on_exported(self, message: Any) -> None:
        self._status.setText(str(message))

    def _on_failure(self, message: str) -> None:
        self._generate_button.setEnabled(True)
        self._status.setText(message)

    def _set_exports_enabled(self, enabled: bool) -> None:
        for button in (self._export_csv, self._export_xlsx, self._export_pdf, self._export_json):
            button.setEnabled(enabled)


def _parse_date(text: str, label: str):  # type: ignore[no-untyped-def]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()  # noqa: DTZ007 - filter input is a bare calendar day
    except ValueError as exc:
        raise ValueError(f"{label} date must be YYYY-MM-DD.") from exc
