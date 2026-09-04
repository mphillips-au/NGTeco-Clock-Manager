"""Domain models and business rules.

This layer is pure Python: no PySide6, no SQLAlchemy, no sockets.
"""

from __future__ import annotations

from clockmanager.domain.models import (
    AttendanceEvent,
    DeviceIdentity,
    DeviceInfo,
    DeviceUser,
    Privilege,
    PunchDirection,
    describe_privilege,
    describe_punch,
)
from clockmanager.domain.payroll import (
    DailySummary,
    Employee,
    PayPeriod,
    PaySchedule,
    PayScheduleType,
    PeriodSummary,
    PunchInput,
    Timesheet,
    TimesheetRules,
    build_timesheet,
    format_hours,
    interpret_naive,
    pay_period_for,
    pay_periods_between,
)
from clockmanager.domain.reports import (
    ExportFormat,
    Report,
    ReportFilter,
    ReportType,
    export_report,
)
from clockmanager.domain.users import (
    CredentialAction,
    UserChange,
    UserDraft,
    UserWriteOutcome,
    describe_changes,
)

__all__ = [
    "AttendanceEvent",
    "CredentialAction",
    "DailySummary",
    "DeviceIdentity",
    "DeviceInfo",
    "DeviceUser",
    "Employee",
    "ExportFormat",
    "PayPeriod",
    "PaySchedule",
    "PayScheduleType",
    "PeriodSummary",
    "Privilege",
    "PunchDirection",
    "PunchInput",
    "Report",
    "ReportFilter",
    "ReportType",
    "Timesheet",
    "TimesheetRules",
    "UserChange",
    "UserDraft",
    "UserWriteOutcome",
    "build_timesheet",
    "describe_changes",
    "describe_privilege",
    "describe_punch",
    "export_report",
    "format_hours",
    "interpret_naive",
    "pay_period_for",
    "pay_periods_between",
]
