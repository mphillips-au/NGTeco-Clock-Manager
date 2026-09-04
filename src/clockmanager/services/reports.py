"""Derived reports and exports (PHASE 06).

The GUI calls this; it never touches SQLAlchemy or the protocol layer
directly (``ARCHITECTURE.md``). Everything here is synchronous and
PySide6-free, so the future headless service exports through exactly the
same code.

Reports are read-only derivations over immutable stored attendance and the
append-only sync/audit history: building or exporting a report never
inserts, updates or deletes a punch, a sync row or an audit row.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.auth import Permission, Role, require
from clockmanager.domain.models import describe_punch
from clockmanager.domain.payroll import (
    PayPeriod,
    PunchInput,
    Timesheet,
    build_timesheet,
    format_hours,
    interpret_naive,
)
from clockmanager.domain.reports import (
    ExportFormat,
    Report,
    ReportFilter,
    ReportType,
    export_report,
)
from clockmanager.errors import ClockManagerError
from clockmanager.persistence.database import Database
from clockmanager.persistence.models import AttendanceEventRecord
from clockmanager.persistence.repositories import (
    AttendanceRepository,
    AuditRepository,
    DeviceRepository,
    SyncHistoryRepository,
)
from clockmanager.services.audit import AuditAction, AuditOutcome, AuditService
from clockmanager.services.employees import EmployeeProfile, EmployeeService
from clockmanager.services.timesheets import TimesheetRequest, TimesheetService

__all__ = ["ReportService"]

_logger = get_logger(__name__)

_REPORT_LIMIT = 5000


class ReportService:
    """Builds derived reports and exports them to CSV/XLSX/PDF/JSON."""

    def __init__(
        self,
        database: Database,
        employees: EmployeeService,
        timesheets: TimesheetService,
        audit: AuditService,
    ) -> None:
        self._database = database
        self._employees = employees
        self._timesheets = timesheets
        self._audit = audit

    # -- shared helpers -----------------------------------------------------

    def _window(self, filt: ReportFilter) -> tuple[datetime | None, datetime | None]:
        """Inclusive date window as naive device-local datetimes."""
        start = (
            datetime.combine(filt.start, time.min).replace(tzinfo=None)
            if filt.start is not None
            else None
        )
        end = (
            datetime.combine(filt.end, time.max).replace(tzinfo=None)
            if filt.end is not None
            else None
        )
        return start, end

    def _employee_scope(
        self, filt: ReportFilter
    ) -> tuple[list[EmployeeProfile], dict[str, EmployeeProfile]]:
        """Employees in scope plus a user-ID -> employee index.

        The index covers canonical user IDs and every linked device user ID
        so stored punches resolve to the right employee even when clocks use
        different IDs for the same person.
        """
        employees = self._employees.list_employees(include_inactive=filt.include_inactive)
        if filt.employee_id is not None:
            employees = [e for e in employees if e.employee_id == filt.employee_id]
        if filt.user_id:
            wanted = filt.user_id.strip()
            employees = [e for e in employees if e.user_id == wanted or wanted in e.device_user_ids]
        if filt.department.strip():
            needle = filt.department.strip().lower()
            employees = [e for e in employees if e.department.lower() == needle]
        index: dict[str, EmployeeProfile] = {}
        for profile in employees:
            index.setdefault(profile.user_id, profile)
            for alias in profile.device_user_ids:
                index.setdefault(alias, profile)
        return employees, index

    def _punches(self, filt: ReportFilter) -> list[AttendanceEventRecord]:
        start, end = self._window(filt)
        user_ids: list[str] | None = None
        if filt.employee_id is not None or filt.user_id or filt.department.strip():
            _, index = self._employee_scope(filt)
            user_ids = sorted(index.keys())
            if not user_ids:
                return []
        with self._database.session() as session:
            return AttendanceRepository(session).list_in_range(
                start=start,
                end=end,
                device_id=filt.device_id,
                user_ids=user_ids,
                punch=filt.punch,
                status=filt.status,
                limit=_REPORT_LIMIT,
            )

    def _device_names(self) -> dict[int, str]:
        with self._database.session() as session:
            return {row.id: row.name for row in DeviceRepository(session).list_all()}

    # -- attendance reports ---------------------------------------------------

    def daily_attendance(self, filt: ReportFilter) -> Report:
        """One row per stored punch in the window, oldest first."""
        rows_data = self._punches(filt)
        _, index = self._employee_scope(filt)
        rows: list[tuple[str, ...]] = []
        for row in rows_data:
            profile = index.get(row.user_id)
            department = profile.department if profile is not None else ""
            display = profile.display_name if profile is not None else (row.employee_name or "")
            rows.append(
                (
                    row.occurred_at.strftime("%Y-%m-%d"),
                    row.occurred_at.strftime("%H:%M:%S"),
                    row.user_id,
                    display,
                    department,
                    str(row.device_id),
                    describe_punch(row.punch),
                    str(row.status),
                    row.source,
                )
            )
        if filt.exception_only:
            # A bare punch list carries no pairing flags; "exceptions only"
            # on this report keeps only unknown punch values.
            filtered: tuple[tuple[str, ...], ...] = tuple(
                r for r in rows if r[6].startswith("Unknown")
            )
        else:
            filtered = tuple(rows)
        return Report(
            report_type=ReportType.DAILY_ATTENDANCE,
            title=f"Daily attendance ({filt.describe()})",
            columns=(
                "Date",
                "Time",
                "User ID",
                "Employee",
                "Department",
                "Device",
                "Direction",
                "Status",
                "Source",
            ),
            rows=filtered,
            summary=f"{len(filtered)} punch(es).",
        )

    def employee_timesheet(self, employee_id: int, start: date, end: date) -> Report:
        """Per-day derived timesheet for one employee (recalculable)."""
        if end < start:
            raise ClockManagerError("Report end must not precede start.")
        profile = self._employees.get(employee_id)
        if profile is None:
            raise ClockManagerError(f"Unknown employee id {employee_id}")
        schedule = self._timesheets.ensure_default_schedule()
        timesheet = self._timesheets.build(
            TimesheetRequest(employee_id=employee_id, start=start, end=end), schedule
        )
        rows = tuple(
            (
                daily.day.isoformat(),
                "" if daily.first_in is None else daily.first_in.strftime("%H:%M"),
                "" if daily.last_out is None else daily.last_out.strftime("%H:%M"),
                format_hours(daily.worked_seconds / 3600.0, decimal=schedule.display_decimal),
                format_hours(daily.overtime_seconds / 3600.0, decimal=schedule.display_decimal),
                str(daily.missing_punches),
                _flags_text(daily),
            )
            for daily in timesheet.summary.days
        )
        total = timesheet.summary
        return Report(
            report_type=ReportType.EMPLOYEE_TIMESHEET,
            title=f"Timesheet for {profile.display_name} ({start.isoformat()} to {end.isoformat()})",
            columns=("Date", "First IN", "Last OUT", "Worked", "OT", "Missing", "Flags"),
            rows=rows,
            summary=(
                f"Total {format_hours(total.total_seconds / 3600.0, decimal=schedule.display_decimal)}; "
                f"overtime {format_hours(total.overtime_seconds / 3600.0, decimal=schedule.display_decimal)}."
            ),
        )

    def weekly_summary(self, filt: ReportFilter) -> Report:
        """One row per in-scope employee for the week containing ``start``."""
        if filt.start is None:
            raise ClockManagerError("Weekly summary needs a start date.")
        # A week is Monday-Sunday regardless of pay cadence.
        monday = filt.start - timedelta(days=filt.start.weekday())
        sunday = monday + timedelta(days=6)
        return self._period_table(
            filt,
            ReportType.WEEKLY_SUMMARY,
            f"Weekly summary ({monday.isoformat()} to {sunday.isoformat()})",
            monday,
            sunday,
        )

    def pay_period_summary(self, filt: ReportFilter) -> Report:
        """One row per in-scope employee for the pay period containing ``start``."""
        if filt.start is None:
            raise ClockManagerError("Pay-period summary needs a start date.")
        schedule = self._timesheets.ensure_default_schedule()
        period = self._timesheets.period_for(filt.start, schedule)
        return self._period_table(
            filt,
            ReportType.PAY_PERIOD_SUMMARY,
            f"Pay-period summary ({period.start.isoformat()} to {period.end.isoformat()})",
            period.start,
            period.end,
        )

    def _period_table(
        self, filt: ReportFilter, kind: ReportType, title: str, start: date, end: date
    ) -> Report:
        employees, _ = self._employee_scope(filt)
        schedule = self._timesheets.ensure_default_schedule()
        rows: list[tuple[str, ...]] = []
        for profile in employees:
            try:
                timesheet = self._timesheets.build(
                    TimesheetRequest(employee_id=profile.employee_id, start=start, end=end),
                    schedule,
                )
            except ClockManagerError:
                continue
            total = timesheet.summary
            missing = sum(d.missing_punches for d in total.days)
            rows.append(
                (
                    profile.user_id,
                    profile.display_name,
                    profile.department,
                    format_hours(total.total_seconds / 3600.0, decimal=schedule.display_decimal),
                    format_hours(total.overtime_seconds / 3600.0, decimal=schedule.display_decimal),
                    format_hours(total.regular_seconds / 3600.0, decimal=schedule.display_decimal),
                    str(missing),
                )
            )
        return Report(
            report_type=kind,
            title=title,
            columns=(
                "User ID",
                "Employee",
                "Department",
                "Worked",
                "Overtime",
                "Regular",
                "Missing",
            ),
            rows=tuple(rows),
            summary=f"{len(rows)} employee(s).",
        )

    # -- exceptions -----------------------------------------------------------

    def exceptions(self, filt: ReportFilter) -> Report:
        """Days with missing/duplicate/excessive/overnight/unknown punches.

        Derived from the same pairing rules as timesheets, so the flags here
        always agree with what the Timesheets view shows. Unknown punch
        values (not 0/1) can never be paired and are listed as their own
        rows.
        """
        if filt.start is None or filt.end is None:
            raise ClockManagerError("Exceptions report needs a start and end date.")
        employees, index = self._employee_scope(filt)
        schedule = self._timesheets.ensure_default_schedule()
        pay_schedule = schedule.to_schedule()
        rules = schedule.to_rules()
        zone_name = pay_schedule.timezone
        rows: list[tuple[str, ...]] = []
        for profile in employees:
            user_ids = [
                profile.user_id,
                *[u for u in profile.device_user_ids if u != profile.user_id],
            ]
            window_start = datetime.combine(filt.start - timedelta(days=1), time.min).replace(
                tzinfo=None
            )
            window_end = datetime.combine(filt.end + timedelta(days=1), time.max).replace(
                tzinfo=None
            )
            with self._database.session() as session:
                stored = AttendanceRepository(session).list_for_users_in_range(
                    user_ids=user_ids, start=window_start, end=window_end
                )
            punches = [
                PunchInput(
                    occurred_at=interpret_naive(row.occurred_at, zone_name),
                    punch=row.punch,
                    status=row.status,
                )
                for row in stored
            ]
            period = PayPeriod(start=filt.start, end=filt.end)
            sheet: Timesheet = build_timesheet(
                employee_user_id=profile.user_id,
                punches=punches,
                period=period,
                schedule=pay_schedule,
                rules=rules,
            )
            for daily in sheet.summary.days:
                flags = _flags_text(daily)
                unknown = sum(1 for row in stored if row.punch not in (0, 1))
                _ = unknown  # counted per day below via the punch query
                if flags or _day_has_unknown(stored, daily.day):
                    if _day_has_unknown(stored, daily.day) and not flags:
                        flags = "unknown punch"
                    elif _day_has_unknown(stored, daily.day):
                        flags = f"{flags},unknown punch"
                    rows.append(
                        (
                            daily.day.isoformat(),
                            profile.user_id,
                            profile.display_name,
                            profile.department,
                            flags,
                            str(daily.missing_punches),
                            str(daily.duplicate_punches),
                        )
                    )
        _ = index
        return Report(
            report_type=ReportType.EXCEPTIONS,
            title=f"Exceptions ({filt.describe()})",
            columns=("Date", "User ID", "Employee", "Department", "Flags", "Missing", "Duplicates"),
            rows=tuple(rows),
            summary=f"{len(rows)} exception day(s).",
        )

    # -- operations reports -----------------------------------------------------

    def device_activity(self, filt: ReportFilter) -> Report:
        """Per-device, per-day punch counts (IN/OUT/unknown)."""
        rows_data = self._punches(filt)
        names = self._device_names()
        buckets: dict[tuple[str, int], list[int]] = {}
        for row in rows_data:
            key = (row.occurred_at.strftime("%Y-%m-%d"), row.device_id)
            counts = buckets.setdefault(key, [0, 0, 0])
            if row.punch == 0:
                counts[0] += 1
            elif row.punch == 1:
                counts[1] += 1
            else:
                counts[2] += 1
        rows = tuple(
            (
                day,
                names.get(device_id, f"device #{device_id}"),
                str(device_id),
                str(counts[0]),
                str(counts[1]),
                str(counts[2]),
                str(counts[0] + counts[1] + counts[2]),
            )
            for (day, device_id), counts in sorted(buckets.items())
        )
        return Report(
            report_type=ReportType.DEVICE_ACTIVITY,
            title=f"Device activity ({filt.describe()})",
            columns=("Date", "Device", "Device ID", "IN", "OUT", "Unknown", "Total"),
            rows=rows,
            summary=f"{len(rows)} device-day(s).",
        )

    def sync_history(self, filt: ReportFilter) -> Report:
        """Sync runs in the window (append-only history, newest first)."""
        start_dt = (
            datetime.combine(filt.start, time.min, tzinfo=UTC) if filt.start is not None else None
        )
        end_dt = datetime.combine(filt.end, time.max, tzinfo=UTC) if filt.end is not None else None
        with self._database.session() as session:
            runs = SyncHistoryRepository(session).list_filtered(
                device_id=filt.device_id, since=start_dt, until=end_dt, limit=_REPORT_LIMIT
            )
        rows = tuple(
            (
                _format_aware(run.started_at),
                run.device_name or "",
                "" if run.device_id is None else str(run.device_id),
                run.mode,
                run.source,
                str(run.events_seen),
                str(run.events_new),
                str(run.events_duplicate),
                run.outcome,
                run.error,
            )
            for run in runs
        )
        return Report(
            report_type=ReportType.SYNC_HISTORY,
            title=f"Sync history ({filt.describe()})",
            columns=(
                "Started",
                "Device",
                "Device ID",
                "Mode",
                "Source",
                "Seen",
                "New",
                "Duplicates",
                "Outcome",
                "Error",
            ),
            rows=rows,
            summary=f"{len(rows)} sync run(s).",
        )

    def audit_report(self, filt: ReportFilter) -> Report:
        """Audit entries in the window (append-only log, newest first)."""
        start_dt = (
            datetime.combine(filt.start, time.min, tzinfo=UTC) if filt.start is not None else None
        )
        end_dt = datetime.combine(filt.end, time.max, tzinfo=UTC) if filt.end is not None else None
        with self._database.session() as session:
            entries = AuditRepository(session).list_filtered(
                since=start_dt, until=end_dt, limit=_REPORT_LIMIT
            )
        rows = tuple(
            (
                _format_aware(entry.occurred_at),
                entry.actor,
                entry.action,
                entry.outcome,
                entry.device_name or "",
                entry.target or "",
                entry.detail,
            )
            for entry in entries
        )
        return Report(
            report_type=ReportType.AUDIT,
            title=f"Audit trail ({filt.describe()})",
            columns=("When", "Actor", "Action", "Outcome", "Device", "Target", "Detail"),
            rows=rows,
            summary=f"{len(rows)} audit entr(ies).".replace(
                "(ies)", "y" if len(rows) == 1 else "ies"
            ),
        )

    # -- exports ------------------------------------------------------------------

    def export(
        self,
        report: Report,
        fmt: ExportFormat,
        *,
        audit_detail: str = "",
        requester_role: Role | str | None = None,
    ) -> tuple[bytes, str, str]:
        """Export a report and audit the export. Never mutates raw data.

        ``requester_role`` enforces PHASE 07 roles. Every role holds the
        export permission (an export changes nothing but its own audit row);
        ``None`` keeps the legacy path for callers without an identity.
        """
        if requester_role is not None:
            require(requester_role, Permission.EXPORT_REPORTS)
        data, suffix, mime = export_report(report, fmt)
        safe_title = "".join(
            ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in report.report_type.value
        )
        filename = f"{safe_title}.{suffix}"
        self._audit.record(
            AuditAction.REPORT_EXPORT,
            AuditOutcome.SUCCEEDED,
            target=report.report_type.value,
            detail=audit_detail
            or f"Exported {report.title} as {suffix.upper()} ({len(report.rows)} rows)",
        )
        _logger.info(
            "Exported report",
            extra={"report": report.report_type.value, "format": suffix, "rows": len(report.rows)},
        )
        return data, filename, mime


def _flags_text(daily: object) -> str:
    """Compact flag list for one DailySummary (``missing x2,dup x1``)."""
    missing = int(getattr(daily, "missing_punches", 0))
    duplicate = int(getattr(daily, "duplicate_punches", 0))
    excessive = int(getattr(daily, "excessive_shifts", 0))
    overnight = int(getattr(daily, "overnight_shifts", 0))
    parts: list[str] = []
    if missing:
        parts.append(f"missing x{missing}")
    if duplicate:
        parts.append(f"dup x{duplicate}")
    if excessive:
        parts.append(f"excessive x{excessive}")
    if overnight:
        parts.append("overnight")
    return ",".join(parts)


def _day_has_unknown(rows: list[AttendanceEventRecord], day: date) -> bool:
    return any(row.occurred_at.date() == day and row.punch not in (0, 1) for row in rows)


def _format_aware(moment: datetime | None) -> str:
    if moment is None:
        return ""
    value = moment
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
