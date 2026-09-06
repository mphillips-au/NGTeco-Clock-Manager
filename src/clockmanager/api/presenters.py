"""Domain-to-schema converters for the web/API boundary (PHASE 17).

One place that turns service results into response shapes, so the routes
stay thin. Nothing here touches a device or a database.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from clockmanager.api import schemas
from clockmanager.domain.models import (
    DeviceInfo,
    DeviceStorage,
    DeviceUser,
    StorageCounter,
    describe_privilege,
)
from clockmanager.domain.payroll import Timesheet, format_hours
from clockmanager.domain.reports import Report
from clockmanager.services.audit import AuditEntry
from clockmanager.services.auth import AuthenticatedUser
from clockmanager.services.devices import DeviceInspection, DeviceProfile, DeviceStatus
from clockmanager.services.employees import EmployeeProfile
from clockmanager.services.sync import StoredAttendance, SyncResult
from clockmanager.services.timesheets import PayScheduleProfile

__all__ = [
    "SyncHistoryRow",
    "api_user",
    "attendance",
    "device",
    "device_info",
    "device_inspection",
    "device_status",
    "device_user",
    "employee",
    "report",
    "schedule",
    "sync_history_row",
    "sync_result",
    "timesheet",
]


class SyncHistoryRow(Protocol):
    """Structural shape of one stored sync-history row.

    The concrete type lives in persistence, which this layer must not
    import (``ARCHITECTURE.md``), so routes pass rows here structurally.
    """

    device_id: int | None
    mode: str
    source: str
    events_seen: int
    events_new: int
    events_duplicate: int
    outcome: str
    error: str
    started_at: datetime
    finished_at: datetime | None


def api_user(user: AuthenticatedUser) -> schemas.ApiUser:
    """Public identity for ``user``. Carries no credential."""
    return schemas.ApiUser(
        username=user.username,
        display_name=user.label,
        role=user.role.value,
        role_label=user.role.label,
        active=user.active,
        last_login_at=user.last_login_at,
    )


def device(profile: DeviceProfile) -> schemas.DeviceOut:
    """A device profile without its communication password (never exposed)."""
    return schemas.DeviceOut(
        device_id=profile.device_id,
        name=profile.name,
        host=profile.host,
        port=profile.port,
        timeout_seconds=profile.timeout_seconds,
        auto_reconnect=profile.auto_reconnect,
        sync_interval_seconds=profile.sync_interval_seconds,
        enabled=profile.enabled,
        has_communication_password=profile.has_communication_password,
        model=profile.model,
        platform=profile.platform,
        firmware_version=profile.firmware_version,
        serial_number=profile.serial_number,
        last_seen_at=profile.last_seen_at,
    )


def device_inspection(inspection: DeviceInspection) -> schemas.DeviceInspectionOut:
    """Everything the device said about itself, sanitized by construction.

    Options come from the protocol allow-list (credential-shaped names are
    refused before a request is built); fingerprints are slot metadata
    only — no template byte reaches this type; operation codes are raw
    integers, never guessed names.
    """
    info = inspection.info
    return schemas.DeviceInspectionOut(
        profile_name=inspection.profile_name,
        endpoint=inspection.endpoint,
        ok=inspection.ok,
        info=None if info is None else device_info(info),
        storage=None if inspection.storage is None else _storage(inspection.storage),
        options=[
            schemas.DeviceOptionOut(
                name=option.name,
                label=option.label,
                group=option.group,
                value=option.value,
                display_value=option.display_value,
                answered=option.answered,
                note=option.note,
            )
            for option in inspection.options
        ],
        fingerprints=[
            schemas.FingerprintSlotOut(
                device_uid=slot.device_uid,
                finger_index=slot.finger_index,
                valid=slot.is_valid,
                template_bytes=slot.template_bytes,
            )
            for slot in inspection.fingerprints
        ],
        operation_log=[
            schemas.OperationLogEntryOut(
                index=entry.index,
                operation=entry.operation,
                operation_label=entry.operation_label,
                operator_uid=entry.operator_uid,
                occurred_at=entry.occurred_at,
                parameters=list(entry.parameters),
            )
            for entry in inspection.operation_log
        ],
        notes=list(inspection.notes),
        error=inspection.error,
        summary=inspection.summary,
    )


def device_info(info: DeviceInfo) -> schemas.DeviceInfoOut:
    """A point-in-time device snapshot with its capacity counters."""
    return schemas.DeviceInfoOut(
        name=info.identity.name,
        model=info.identity.model,
        platform=info.identity.platform,
        firmware_version=info.identity.firmware_version,
        serial_number=info.identity.serial_number,
        device_time=info.device_time,
        user_count=info.user_count,
        attendance_count=info.attendance_count,
        fingerprint_count=info.fingerprint_count,
        face_count=info.face_count,
        storage=None if info.storage is None else _storage(info.storage),
    )


def _storage(storage: DeviceStorage) -> schemas.DeviceStorageOut:
    return schemas.DeviceStorageOut(
        users=_counter(storage.users),
        fingerprints=_counter(storage.fingerprints),
        attendance=_counter(storage.attendance),
        faces=_counter(storage.faces),
        operation_log_records=storage.operation_log_records,
    )


def _counter(counter: StorageCounter) -> schemas.StorageCounterOut:
    return schemas.StorageCounterOut(
        label=counter.label,
        used=counter.used,
        capacity=counter.capacity,
        free=counter.free,
        describe=counter.describe(),
    )


def device_status(status: DeviceStatus) -> schemas.DeviceStatusOut:
    """Locally known device state (local reads only, no hardware)."""
    return schemas.DeviceStatusOut(
        device=device(status.profile),
        stored_events=status.stored_events,
        last_success_at=status.last_success_at,
        last_outcome=status.last_outcome,
        last_error=status.last_error,
    )


def device_user(user: DeviceUser) -> schemas.UserOut:
    """A device user. The credential region is absent by construction."""
    return schemas.UserOut(
        device_uid=user.device_uid,
        user_id=user.user_id,
        first_name=user.first_name,
        last_name=user.last_name,
        display_name=user.display_name,
        privilege=user.privilege,
        privilege_label=describe_privilege(user.privilege),
        has_credential_data=user.has_credential_data,
    )


def attendance(stored: StoredAttendance) -> schemas.AttendanceOut:
    """One stored punch with its IN/OUT direction derived from ``punch``."""
    return schemas.AttendanceOut(
        record_id=stored.record_id,
        device_id=stored.device_id,
        device_uid=stored.device_uid,
        user_id=stored.user_id,
        employee_name=stored.employee_name,
        display_name=stored.display_name,
        occurred_at=stored.occurred_at,
        punch=stored.punch,
        direction=stored.direction_label,
        status=stored.status,
        received_at=stored.received_at,
        source=stored.source,
        event_key=stored.event_key,
    )


def sync_history_row(row: SyncHistoryRow) -> schemas.SyncHistoryOut:
    """One sync-history row. Local reads only, never hardware."""
    return schemas.SyncHistoryOut(
        device_id=row.device_id,
        mode=row.mode,
        source=row.source,
        seen=row.events_seen,
        new=row.events_new,
        duplicate=row.events_duplicate,
        outcome=row.outcome,
        error=row.error or "",
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def sync_result(result: SyncResult) -> schemas.SyncResultOut:
    """The outcome of one sync run (a device failure is data, not a raise)."""
    return schemas.SyncResultOut(
        ok=result.ok,
        device_id=result.device_id,
        device_name=result.device_name,
        source=result.source,
        seen=result.seen,
        new=result.new,
        duplicate=result.duplicate,
        is_recovery=result.is_recovery,
        error=result.error,
        error_type=result.error_type,
        summary=result.summary,
    )


def employee(profile: EmployeeProfile) -> schemas.EmployeeOut:
    """A business employee with its device mappings."""
    return schemas.EmployeeOut(
        employee_id=profile.employee_id,
        user_id=profile.user_id,
        first_name=profile.first_name,
        last_name=profile.last_name,
        display_name=profile.display_name,
        active=profile.active,
        department=profile.department,
        position=profile.position,
        email=profile.email,
        notes=profile.notes,
        device_user_ids=list(profile.device_user_ids),
    )


def schedule(profile: PayScheduleProfile) -> schemas.ScheduleOut:
    """A stored pay schedule with its rules."""
    return schemas.ScheduleOut(
        schedule_id=profile.schedule_id,
        name=profile.name,
        schedule_type=profile.schedule_type,
        anchor_date=profile.anchor_date,
        timezone=profile.timezone,
        day_cutoff_hour=profile.day_cutoff_hour,
        duplicate_interval_seconds=profile.duplicate_interval_seconds,
        max_shift_hours=profile.max_shift_hours,
        display_decimal=profile.display_decimal,
        daily_overtime_hours=profile.daily_overtime_hours,
        weekly_overtime_hours=profile.weekly_overtime_hours,
        is_active=profile.is_active,
    )


def timesheet(sheet: Timesheet, *, decimal: bool) -> schemas.TimesheetOut:
    """A derived timesheet as display-ready days plus period totals."""
    period = sheet.summary.period
    days = [
        {
            "day": day.day.isoformat(),
            "first_in": day.first_in.isoformat() if day.first_in is not None else None,
            "last_out": day.last_out.isoformat() if day.last_out is not None else None,
            "worked_seconds": day.worked_seconds,
            "worked_display": format_hours(day.worked_hours, decimal=decimal),
            "regular_seconds": day.regular_seconds,
            "overtime_seconds": day.overtime_seconds,
            "missing_punches": day.missing_punches,
            "duplicate_punches": day.duplicate_punches,
            "excessive_shifts": day.excessive_shifts,
            "overnight_shifts": day.overnight_shifts,
        }
        for day in sheet.summary.days
    ]
    return schemas.TimesheetOut(
        employee_user_id=sheet.employee_user_id,
        period_start=period.start,
        period_end=period.end,
        total_seconds=sheet.summary.total_seconds,
        overtime_seconds=sheet.summary.overtime_seconds,
        total_display=format_hours(sheet.summary.total_hours, decimal=decimal),
        overtime_display=format_hours(sheet.summary.overtime_hours, decimal=decimal),
        days=days,
    )


def report(built: Report) -> schemas.ReportOut:
    """A derived report as column dictionaries (display-safe by construction)."""
    return schemas.ReportOut(
        report_type=built.report_type.value,
        title=built.title,
        columns=list(built.columns),
        rows=built.as_dicts(),
        generated_at=built.generated_at,
        summary=built.summary,
    )


def audit_entry(entry: AuditEntry) -> schemas.AuditOut:
    """One audit row. Details are redacted at write time by the service."""
    return schemas.AuditOut(
        occurred_at=entry.occurred_at,
        actor=entry.actor,
        action=entry.action,
        outcome=entry.outcome,
        detail=entry.detail,
        device_name=entry.device_name,
        target=entry.target,
        target_uid=entry.target_uid,
    )
