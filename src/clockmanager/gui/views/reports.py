"""Reports view (PHASE 06).

Derived, read-only reporting over immutable stored attendance plus the
append-only sync/audit history: pick a report, set filters, generate, then
export to CSV/XLSX/PDF/JSON. All building and exporting goes through
``ReportService`` off the UI thread; the widget never queries the database
or touches raw attendance directly.
"""

from __future__ import annotations

from datetime import UTC, date, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clockmanager.domain.auth import Role, normalise_role
from clockmanager.domain.reports import ExportFormat, Report, ReportFilter, ReportType
from clockmanager.gui.views.common import (
    build_table,
    fill_table,
    muted_label,
    notify,
    page_header,
    primary_button,
    run_off_thread,
    set_status,
)
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


#: Named ranges, in the order an operator reaches for them. ``None`` means
#: "no date filter at all"; every other entry is resolved against today.
_RANGE_ITEMS: tuple[tuple[str, str], ...] = (
    ("All dates", "all"),
    ("Today", "today"),
    ("Yesterday", "yesterday"),
    ("This week", "this-week"),
    ("Last week", "last-week"),
    ("This month", "this-month"),
    ("Last 30 days", "last-30"),
    ("Custom range", "custom"),
)


def resolve_range(key: str, today: date) -> tuple[date, date] | None:
    """Turn a named range into concrete dates.

    Kept as a free function so the calendar arithmetic is testable without a
    widget, and so "this week" means the same thing everywhere.
    """
    match key:
        case "today":
            return today, today
        case "yesterday":
            yesterday = today - timedelta(days=1)
            return yesterday, yesterday
        case "this-week":
            start = today - timedelta(days=today.weekday())
            return start, today
        case "last-week":
            start = today - timedelta(days=today.weekday() + 7)
            return start, start + timedelta(days=6)
        case "this-month":
            return today.replace(day=1), today
        case "last-30":
            return today - timedelta(days=29), today
        case _:
            return None


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

        self._report_box = QComboBox(self)
        for label, kind in _REPORT_ITEMS:
            self._report_box.addItem(label, kind.value)

        # A named range is what an operator actually asks for ("last week");
        # the two pickers stay visible so the exact days are never a mystery,
        # and touching one switches the range to Custom.
        self._range_box = QComboBox(self)
        for label, key in _RANGE_ITEMS:
            self._range_box.addItem(label, key)
        self._range_box.setAccessibleName("Date range")
        self._range_box.currentIndexChanged.connect(self._on_range_changed)

        self._start = _date_edit("Start date", self)
        self._end = _date_edit("End date", self)
        for picker in (self._start, self._end):
            picker.dateChanged.connect(self._on_date_edited)

        self._employee_box = QComboBox(self)
        self._employee_box.setAccessibleName("Employee")
        self._department = QLineEdit(self)
        self._department.setPlaceholderText("Any department")
        self._department.setAccessibleName("Department")
        self._department.setClearButtonEnabled(True)
        self._exceptions_only = QCheckBox("Exceptions only", self)

        self._generate_button = primary_button("Generate report", self)
        self._generate_button.clicked.connect(self.generate)
        self._generate_button.setShortcut("Ctrl+Return")
        self._generate_button.setToolTip("Build the report from these filters (Ctrl+Enter).")
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
        self._status = QLabel("Pick a report, set the dates, then Generate.", self)
        self._status.setWordWrap(True)

        # Filters read as a form, in two columns, rather than one long bar
        # whose fields shrink until the labels no longer fit.
        left = QFormLayout()
        left.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        left.addRow("Report", self._report_box)
        left.addRow("Date range", self._range_box)
        dates = QHBoxLayout()
        dates.addWidget(self._start)
        dates.addWidget(QLabel("to", self))
        dates.addWidget(self._end)
        dates.addStretch(1)
        left.addRow("Between", dates)

        right = QFormLayout()
        right.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        right.addRow("Employee", self._employee_box)
        right.addRow("Department", self._department)
        right.addRow("", self._exceptions_only)

        columns = QHBoxLayout()
        columns.addLayout(left, stretch=1)
        columns.addSpacing(24)
        columns.addLayout(right, stretch=1)

        actions = QHBoxLayout()
        actions.addWidget(self._refresh_employees_button)
        actions.addStretch(1)
        actions.addWidget(QLabel("Export:", self))
        actions.addWidget(self._export_csv)
        actions.addWidget(self._export_xlsx)
        actions.addWidget(self._export_pdf)
        actions.addWidget(self._export_json)
        actions.addSpacing(12)
        actions.addWidget(self._generate_button)

        filter_box = QGroupBox("Filters", self)
        filter_layout = QVBoxLayout()
        filter_layout.addLayout(columns)
        filter_layout.addLayout(actions)
        filter_box.setLayout(filter_layout)

        layout = QVBoxLayout()
        layout.addWidget(
            page_header(
                "Reports",
                "Derived from stored attendance. Generating or exporting never "
                "changes a punch or touches the clock.",
            )
        )
        layout.addWidget(filter_box)
        layout.addWidget(self._status)
        layout.addWidget(self._table, stretch=1)
        layout.addWidget(
            muted_label("Every export is written to the audit log with the operator and format.")
        )
        self.setLayout(layout)
        self._set_exports_enabled(False)
        self._on_range_changed()
        fill_table(
            self._table,
            [],
            empty_message="No report yet. Choose a report and press “Generate report”.",
        )

    def load_employees(self) -> None:
        run_off_thread(
            self._employees.list_employees,
            on_success=self._on_employees,
            on_failure=self._on_failure,
        )

    def current_filter(self) -> ReportFilter:
        """Read the filter widgets. Raises ValueError on an impossible range."""
        if str(self._range_box.currentData()) == "all":
            start: date | None = None
            end: date | None = None
        else:
            start = _to_date(self._start.date())
            end = _to_date(self._end.date())
            if end < start:
                raise ValueError("The end date must be on or after the start date.")
        selected = self._employee_box.currentData()
        employee_id = selected if isinstance(selected, int) else None
        return ReportFilter(
            start=start,
            end=end,
            employee_id=employee_id,
            department=self._department.text().strip(),
            exception_only=self._exceptions_only.isChecked(),
        )

    def _on_range_changed(self) -> None:
        """Apply the named range to the pickers and enable them accordingly."""
        key = str(self._range_box.currentData())
        custom = key == "custom"
        enabled = key != "all"
        for picker in (self._start, self._end):
            picker.setEnabled(enabled)
        if custom or not enabled:
            return
        resolved = resolve_range(key, date.today())  # noqa: DTZ011 - business day
        if resolved is None:  # pragma: no cover - defensive
            return
        start, end = resolved
        for picker, value in ((self._start, start), (self._end, end)):
            picker.blockSignals(True)
            picker.setDate(QDate(value.year, value.month, value.day))
            picker.blockSignals(False)

    def _on_date_edited(self) -> None:
        """Editing a date by hand means the range is no longer a named one."""
        if str(self._range_box.currentData()) in {"all", "custom"}:
            return
        index = self._range_box.findData("custom")
        if index >= 0:
            self._range_box.blockSignals(True)
            self._range_box.setCurrentIndex(index)
            self._range_box.blockSignals(False)

    def generate(self) -> None:
        try:
            filt = self.current_filter()
        except ValueError as exc:
            set_status(self._status, str(exc), "error")
            return
        kind = ReportType(self._report_box.currentData())
        self._generate_button.setEnabled(False)
        set_status(self._status, "Building report…", "loading")
        run_off_thread(
            lambda: self._build(kind, filt),
            on_success=self._on_built,
            on_failure=self._on_failure,
        )

    def export(self, fmt: ExportFormat) -> None:
        report = self._report
        if report is None:
            set_status(self._status, "Generate a report first, then export it.", "warning")
            return
        suggested = _default_filename(report, fmt)
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export {fmt.value.upper()}", suggested, f"*.{fmt.value}"
        )
        if not path:
            return
        set_status(self._status, f"Exporting {fmt.value.upper()}…", "loading")
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
        return f"Saved {_readable_size(len(data))} to {target.name or filename}."

    def _on_employees(self, profiles: Any) -> None:
        if not isinstance(profiles, list):  # pragma: no cover - defensive
            return
        actives = [p for p in profiles if p.active]
        self._employee_box.clear()
        self._employee_box.addItem("All employees", None)
        for profile in actives:
            self._employee_box.addItem(
                f"{profile.display_name} ({profile.user_id})", profile.employee_id
            )
        if not actives:
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
        fill_table(
            self._table,
            report.rows,
            empty_message="No rows match these filters. Widen the date range or clear them.",
        )
        stamp = report.generated_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        set_status(
            self._status,
            f"{report.title} — {report.summary} Built {stamp}.",
            "success",
        )
        self._set_exports_enabled(True)

    def _on_exported(self, message: Any) -> None:
        set_status(self._status, str(message), "success")
        notify(self, str(message))

    def _on_failure(self, message: str) -> None:
        self._generate_button.setEnabled(True)
        set_status(self._status, message, "error")
        notify(self, message, kind="error")

    def _set_exports_enabled(self, enabled: bool) -> None:
        for button in (self._export_csv, self._export_xlsx, self._export_pdf, self._export_json):
            button.setEnabled(enabled)


def _default_filename(report: Report, fmt: ExportFormat) -> str:
    """A dated, human filename so exports do not overwrite each other."""
    slug = "".join(c if c.isalnum() else "-" for c in report.title.lower()).strip("-")
    day = report.generated_at.astimezone(UTC).strftime("%Y-%m-%d")
    return f"{slug or 'report'}-{day}.{fmt.value}"


def _to_date(value: QDate) -> date:
    """Convert a Qt date to a plain calendar date."""
    return date(value.year(), value.month(), value.day())


def _date_edit(name: str, parent: QWidget) -> QDateEdit:
    """A calendar-backed date field: no format for the operator to get wrong."""
    edit = QDateEdit(parent)
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("yyyy-MM-dd")
    edit.setDate(QDate.currentDate())
    edit.setAccessibleName(name)
    edit.setMinimumWidth(130)
    return edit


def _readable_size(size: int) -> str:
    """File size in the units a person uses when checking an export saved."""
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
