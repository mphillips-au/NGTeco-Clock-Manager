"""PHASE 06: derived reports and exports over immutable stored data."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date, datetime

import pytest

from clockmanager.domain.payroll import Employee
from clockmanager.domain.reports import (
    ExportFormat,
    Report,
    ReportFilter,
    ReportType,
    export_report,
    report_to_csv_text,
    report_to_json_text,
    report_to_pdf_bytes,
    report_to_xlsx_bytes,
)
from clockmanager.errors import ClockManagerError
from clockmanager.persistence.models import AttendanceEventRecord, DeviceRecord
from clockmanager.services.application import ApplicationContext


def _device_id(context: ApplicationContext) -> int:
    with context.database.session() as session:
        device = DeviceRecord(name="Test clock")
        session.add(device)
        session.flush()
        return device.id


def _store(
    context: ApplicationContext,
    device_id: int,
    *,
    user_id: str,
    moment: datetime,
    punch: int,
    status: int = 0,
    source: str = "historical",
) -> None:
    with context.database.session() as session:
        session.add(
            AttendanceEventRecord(
                device_id=device_id,
                device_uid=None,
                user_id=user_id,
                occurred_at=moment,  # naive device-local wall time, by design
                punch=punch,
                status=status,
                received_at=moment,
                source=source,
                event_key=f"{user_id}-{moment.isoformat()}-{punch}-{status}-{device_id}",
                employee_name=None,
            )
        )


def _seed_day(context: ApplicationContext) -> tuple[int, object]:
    device_id = _device_id(context)
    profile = context.employees.create(
        Employee(user_id="EMP-001", first_name="Ada", last_name="Lovelace", department="IT")
    )
    monday_in = datetime(2026, 9, 7, 9, 0)  # noqa: DTZ001
    monday_out = datetime(2026, 9, 7, 17, 0)  # noqa: DTZ001
    _store(context, device_id, user_id="EMP-001", moment=monday_in, punch=0)
    _store(context, device_id, user_id="EMP-001", moment=monday_out, punch=1)
    return device_id, profile


def test_filter_rejects_inverted_dates() -> None:
    with pytest.raises(ValueError, match="must not precede"):
        ReportFilter(start=date(2026, 9, 8), end=date(2026, 9, 7))


def test_daily_attendance_lists_punches(context: ApplicationContext) -> None:
    _seed_day(context)
    report = context.reports.daily_attendance(
        ReportFilter(start=date(2026, 9, 7), end=date(2026, 9, 7))
    )
    assert report.report_type is ReportType.DAILY_ATTENDANCE
    assert len(report.rows) == 2
    assert report.columns[:3] == ("Date", "Time", "User ID")
    directions = [row[6] for row in report.rows]
    assert directions == ["IN", "OUT"]


def test_reports_do_not_mutate_attendance(context: ApplicationContext) -> None:
    device_id, _ = _seed_day(context)
    filt = ReportFilter(start=date(2026, 9, 7), end=date(2026, 9, 13))
    with context.database.session() as session:
        from clockmanager.persistence.repositories import AttendanceRepository

        before = AttendanceRepository(session).count()
    context.reports.daily_attendance(filt)
    context.reports.device_activity(filt)
    context.reports.sync_history(filt)
    context.reports.audit_report(filt)
    context.reports.weekly_summary(filt)
    context.reports.pay_period_summary(filt)
    context.reports.exceptions(filt)
    with context.database.session() as session:
        from clockmanager.persistence.repositories import AttendanceRepository

        after = AttendanceRepository(session).count()
    assert before == after == 2
    assert device_id > 0


def test_employee_timesheet_matches_service(context: ApplicationContext) -> None:
    _, profile = _seed_day(context)
    report = context.reports.employee_timesheet(
        profile.employee_id, date(2026, 9, 7), date(2026, 9, 13)
    )
    assert report.report_type is ReportType.EMPLOYEE_TIMESHEET
    assert "8:00" in report.rows[0][3] or "8.00" in report.rows[0][3]


def test_weekly_and_pay_period_aggregate(context: ApplicationContext) -> None:
    _seed_day(context)
    filt = ReportFilter(start=date(2026, 9, 9), end=date(2026, 9, 9))
    weekly = context.reports.weekly_summary(filt)
    assert weekly.report_type is ReportType.WEEKLY_SUMMARY
    assert len(weekly.rows) == 1
    assert weekly.rows[0][0] == "EMP-001"
    pay = context.reports.pay_period_summary(filt)
    assert pay.report_type is ReportType.PAY_PERIOD_SUMMARY
    assert len(pay.rows) == 1


def test_department_filter_scopes_reports(context: ApplicationContext) -> None:
    _seed_day(context)
    context.employees.create(Employee(user_id="EMP-002", department="HR"))
    filt = ReportFilter(start=date(2026, 9, 7), end=date(2026, 9, 7), department="it")
    report = context.reports.daily_attendance(filt)
    assert {row[2] for row in report.rows} == {"EMP-001"}
    weekly = context.reports.weekly_summary(
        ReportFilter(start=date(2026, 9, 9), end=date(2026, 9, 9), department="HR")
    )
    assert [row[0] for row in weekly.rows] == ["EMP-002"]


def test_exceptions_flags_missing_out(context: ApplicationContext) -> None:
    device_id = _device_id(context)
    context.employees.create(Employee(user_id="EMP-003"))
    _store(context, device_id, user_id="EMP-003", moment=datetime(2026, 9, 8, 9, 0), punch=0)  # noqa: DTZ001
    report = context.reports.exceptions(ReportFilter(start=date(2026, 9, 8), end=date(2026, 9, 8)))
    assert report.report_type is ReportType.EXCEPTIONS
    assert any(row[1] == "EMP-003" and "missing" in row[4] for row in report.rows)


def test_exceptions_lists_unknown_punch(context: ApplicationContext) -> None:
    device_id = _device_id(context)
    context.employees.create(Employee(user_id="EMP-004"))
    _store(context, device_id, user_id="EMP-004", moment=datetime(2026, 9, 8, 9, 0), punch=7)  # noqa: DTZ001
    report = context.reports.exceptions(ReportFilter(start=date(2026, 9, 8), end=date(2026, 9, 8)))
    assert any(row[1] == "EMP-004" and "unknown punch" in row[4] for row in report.rows)
    daily = context.reports.daily_attendance(
        ReportFilter(start=date(2026, 9, 8), end=date(2026, 9, 8), exception_only=True)
    )
    assert any("Unknown" in row[6] for row in daily.rows)


def test_device_activity_counts(context: ApplicationContext) -> None:
    _seed_day(context)
    report = context.reports.device_activity(
        ReportFilter(start=date(2026, 9, 7), end=date(2026, 9, 7))
    )
    assert report.report_type is ReportType.DEVICE_ACTIVITY
    assert len(report.rows) == 1
    assert report.rows[0][3] == "1"  # IN
    assert report.rows[0][4] == "1"  # OUT


def test_sync_history_and_audit_reports(context: ApplicationContext) -> None:
    _seed_day(context)
    sync = context.reports.sync_history(ReportFilter())
    assert sync.report_type is ReportType.SYNC_HISTORY
    assert sync.columns[0] == "Started"
    audit = context.reports.audit_report(ReportFilter())
    assert audit.report_type is ReportType.AUDIT
    assert any("employee.create" in row[2] for row in audit.rows)


def test_exports_round_trip() -> None:
    report = Report(
        report_type=ReportType.DAILY_ATTENDANCE,
        title="Daily attendance",
        columns=("Date", "User ID"),
        rows=(("2026-09-07", "EMP-001"),),
        summary="1 punch(es).",
    )
    csv_text = report_to_csv_text(report)
    parsed = list(csv.reader(io.StringIO(csv_text)))
    assert parsed[0] == ["Date", "User ID"]
    assert parsed[1] == ["2026-09-07", "EMP-001"]

    payload = json.loads(report_to_json_text(report))
    assert payload["report_type"] == "daily_attendance"
    assert payload["rows"] == [{"Date": "2026-09-07", "User ID": "EMP-001"}]

    xlsx = report_to_xlsx_bytes(report)
    with zipfile.ZipFile(io.BytesIO(xlsx)) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "EMP-001" in sheet
    assert "Date" in sheet

    pdf = report_to_pdf_bytes(report)
    assert pdf.startswith(b"%PDF-1.4")
    assert b"Daily attendance" in pdf

    for fmt in ExportFormat:
        data, suffix, mime = export_report(report, fmt)
        assert len(data) > 0
        assert mime
        assert suffix == fmt.value


def test_export_is_audited(context: ApplicationContext) -> None:
    _seed_day(context)
    report = context.reports.daily_attendance(
        ReportFilter(start=date(2026, 9, 7), end=date(2026, 9, 7))
    )
    before = {e.action for e in context.audit.recent()}
    context.reports.export(report, ExportFormat.CSV)
    after = {e.action for e in context.audit.recent()}
    assert "report.export" not in before
    assert "report.export" in after


def test_report_carries_no_secrets(context: ApplicationContext) -> None:
    _seed_day(context)
    filt = ReportFilter(start=date(2026, 9, 7), end=date(2026, 9, 13))
    for report in (
        context.reports.daily_attendance(filt),
        context.reports.device_activity(filt),
        context.reports.sync_history(ReportFilter()),
        context.reports.audit_report(ReportFilter()),
    ):
        text = " ".join(report.columns) + " " + " ".join(" ".join(row) for row in report.rows)
        lowered = text.lower()
        assert "communication_password" not in lowered
        assert "event_key" not in lowered
        assert "credential" not in lowered


def test_status_and_punch_filters(context: ApplicationContext) -> None:
    device_id = _device_id(context)
    context.employees.create(Employee(user_id="EMP-005"))
    _store(
        context,
        device_id,
        user_id="EMP-005",
        moment=datetime(2026, 9, 7, 9, 0),  # noqa: DTZ001
        punch=0,
        status=3,
    )
    only_in = context.reports.daily_attendance(
        ReportFilter(start=date(2026, 9, 7), end=date(2026, 9, 7), punch=1)
    )
    assert all(row[6] == "OUT" for row in only_in.rows) or not only_in.rows
    by_status = context.reports.daily_attendance(
        ReportFilter(start=date(2026, 9, 7), end=date(2026, 9, 7), status=3)
    )
    assert {row[2] for row in by_status.rows} == {"EMP-005"}


def test_employee_timesheet_needs_employee_and_dates(context: ApplicationContext) -> None:
    with pytest.raises(ClockManagerError, match="Unknown employee"):
        context.reports.employee_timesheet(9999, date(2026, 9, 7), date(2026, 9, 13))
    with pytest.raises(ClockManagerError, match="must not precede"):
        context.reports.employee_timesheet(1, date(2026, 9, 13), date(2026, 9, 7))
