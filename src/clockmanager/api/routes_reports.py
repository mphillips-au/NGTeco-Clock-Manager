"""Derived reports, exports and the audit log (PHASE 17).

Reports are read-only derivations over immutable stored attendance: building
or exporting one never mutates a punch. Every export is audited as
``report.export`` by the service. The audit log itself is append-only and
needs ``audit.view``.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status

from clockmanager.api import presenters, schemas
from clockmanager.api.deps import get_context, get_current_user, require_permission
from clockmanager.domain.auth import Permission
from clockmanager.domain.reports import ExportFormat, Report, ReportFilter, ReportType
from clockmanager.errors import ClockManagerError
from clockmanager.services.application import ApplicationContext
from clockmanager.services.auth import AuthenticatedUser

__all__ = ["router"]

router = APIRouter(tags=["reports"])

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]

_MIME: dict[str, str] = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "json": "application/json",
}


def _filter(
    *,
    start: date | None,
    end: date | None,
    employee_id: int | None,
    user_id: str | None,
    device_id: int | None,
    department: str,
    punch: int | None,
    status_value: int | None,
    exception_only: bool,
) -> ReportFilter:
    try:
        return ReportFilter(
            start=start,
            end=end,
            employee_id=employee_id,
            user_id=user_id,
            device_id=device_id,
            department=department,
            punch=punch,
            status=status_value,
            exception_only=exception_only,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _build(context: ApplicationContext, report_type: ReportType, filt: ReportFilter) -> Report:
    service = context.reports
    service = context.reports
    if report_type is ReportType.DAILY_ATTENDANCE:
        return service.daily_attendance(filt)
    if report_type is ReportType.EMPLOYEE_TIMESHEET:
        if filt.start is None or filt.end is None or filt.employee_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="employee_timesheet needs start, end and employee_id.",
            )
        return service.employee_timesheet(filt.employee_id, filt.start, filt.end)
    if report_type is ReportType.WEEKLY_SUMMARY:
        return service.weekly_summary(filt)
    if report_type is ReportType.PAY_PERIOD_SUMMARY:
        return service.pay_period_summary(filt)
    if report_type is ReportType.EXCEPTIONS:
        return service.exceptions(filt)
    if report_type is ReportType.DEVICE_ACTIVITY:
        return service.device_activity(filt)
    if report_type is ReportType.SYNC_HISTORY:
        return service.sync_history(filt)
    if report_type is ReportType.AUDIT:
        return service.audit_report(filt)
    raise HTTPException(  # pragma: no cover - exhaustive over ReportType
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unknown report type {report_type.value!r}.",
    )


@router.get("/api/reports/{report_type}", response_model=schemas.ReportOut)
def get_report(
    request: Request,
    user: CurrentUser,
    report_type: Annotated[ReportType, Path()],
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    employee_id: int | None = Query(default=None, gt=0),
    user_id: str | None = Query(default=None),
    device_id: int | None = Query(default=None, gt=0),
    department: str = Query(default=""),
    punch: int | None = Query(default=None),
    status_value: int | None = Query(default=None, alias="status"),
    exception_only: bool = Query(default=False),
) -> schemas.ReportOut:
    """Build one of the eight derived reports as JSON."""
    _ = user
    context: ApplicationContext = get_context(request)
    filt = _filter(
        start=start,
        end=end,
        employee_id=employee_id,
        user_id=user_id,
        device_id=device_id,
        department=department,
        punch=punch,
        status_value=status_value,
        exception_only=exception_only,
    )
    try:
        built = _build(context, report_type, filt)
    except ClockManagerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return presenters.report(built)


@router.get("/api/reports/{report_type}/export")
def export_report(
    request: Request,
    user: CurrentUser,
    report_type: Annotated[ReportType, Path()],
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    employee_id: int | None = Query(default=None, gt=0),
    user_id: str | None = Query(default=None),
    device_id: int | None = Query(default=None, gt=0),
    department: str = Query(default=""),
    punch: int | None = Query(default=None),
    status_value: int | None = Query(default=None, alias="status"),
    exception_only: bool = Query(default=False),
    fmt: ExportFormat = Query(default=ExportFormat.JSON),
) -> Response:
    """Build a report and return it as a file (csv/xlsx/pdf/json).

    Every role holds the export permission: an export changes nothing but
    its own audited ``report.export`` entry.
    """
    context: ApplicationContext = get_context(request)
    filt = _filter(
        start=start,
        end=end,
        employee_id=employee_id,
        user_id=user_id,
        device_id=device_id,
        department=department,
        punch=punch,
        status_value=status_value,
        exception_only=exception_only,
    )
    try:
        built = _build(context, report_type, filt)
        data, filename, _mime = context.reports.export(built, fmt, requester_role=user.role)
    except ClockManagerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type=_MIME.get(fmt.value, "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/audit", response_model=list[schemas.AuditOut])
def recent_audit(
    request: Request,
    user: CurrentUser,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[schemas.AuditOut]:
    """The most recent audit entries, newest first. Needs ``audit.view``."""
    require_permission(user, Permission.VIEW_AUDIT)
    context: ApplicationContext = get_context(request)
    return [presenters.audit_entry(item) for item in context.audit.recent(limit=limit)]


@router.get("/api/audit/count")
def audit_count(request: Request, user: CurrentUser) -> dict[str, int]:
    """How many audit entries are stored."""
    require_permission(user, Permission.VIEW_AUDIT)
    context: ApplicationContext = get_context(request)
    return {"count": context.audit.count()}
