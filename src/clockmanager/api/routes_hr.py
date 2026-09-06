"""Employees, pay schedules and derived timesheets (PHASE 17).

Employees are business records above device users; timesheets are derived
on demand from immutable stored attendance and recalculable. Pay-schedule
administration needs an administrator (``MANAGE_PAYROLL``), while reading
employees and timesheets needs only a login.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from clockmanager.api import presenters, schemas
from clockmanager.api.deps import get_context, get_current_user, require_permission
from clockmanager.domain.auth import Permission
from clockmanager.domain.payroll import Employee, PayScheduleType
from clockmanager.errors import ClockManagerError
from clockmanager.services.application import ApplicationContext
from clockmanager.services.auth import AuthenticatedUser
from clockmanager.services.timesheets import TimesheetRequest

__all__ = ["router"]

router = APIRouter(tags=["hr"])

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


def _to_employee(body: schemas.EmployeeIn) -> Employee:
    try:
        return Employee(
            user_id=body.user_id,
            first_name=body.first_name,
            last_name=body.last_name,
            active=body.active,
            department=body.department,
            position=body.position,
            email=body.email,
            notes=body.notes,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# -- employees -----------------------------------------------------------------


@router.get("/api/employees", response_model=list[schemas.EmployeeOut])
def list_employees(
    request: Request,
    user: CurrentUser,
    include_inactive: bool = Query(default=True),
) -> list[schemas.EmployeeOut]:
    """Business employees with their device mappings."""
    _ = user
    context: ApplicationContext = get_context(request)
    return [
        presenters.employee(item)
        for item in context.employees.list_employees(include_inactive=include_inactive)
    ]


@router.post(
    "/api/employees", response_model=schemas.EmployeeOut, status_code=status.HTTP_201_CREATED
)
def create_employee(
    request: Request, user: CurrentUser, body: schemas.EmployeeIn
) -> schemas.EmployeeOut:
    """Create an employee. Needs ``employees.manage`` (admin, office staff)."""
    context: ApplicationContext = get_context(request)
    try:
        created = context.employees.create(_to_employee(body), requester_role=user.role)
    except ClockManagerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return presenters.employee(created)


@router.get("/api/employees/{employee_id}", response_model=schemas.EmployeeOut)
def get_employee(request: Request, user: CurrentUser, employee_id: int) -> schemas.EmployeeOut:
    """One employee with its device mappings."""
    _ = user
    context: ApplicationContext = get_context(request)
    found = context.employees.get(employee_id)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown employee id {employee_id}.",
        )
    return presenters.employee(found)


@router.put("/api/employees/{employee_id}", response_model=schemas.EmployeeOut)
def update_employee(
    request: Request, user: CurrentUser, employee_id: int, body: schemas.EmployeeIn
) -> schemas.EmployeeOut:
    """Update an employee. Needs ``employees.manage``."""
    context: ApplicationContext = get_context(request)
    try:
        updated = context.employees.update(
            employee_id, _to_employee(body), requester_role=user.role
        )
    except ClockManagerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return presenters.employee(updated)


class ActiveIn(BaseModel):
    active: bool


@router.post("/api/employees/{employee_id}/active", response_model=schemas.EmployeeOut)
def set_employee_active(
    request: Request, user: CurrentUser, employee_id: int, body: ActiveIn
) -> schemas.EmployeeOut:
    """Activate or deactivate an employee. Deactivation is a flag, never a delete."""
    context: ApplicationContext = get_context(request)
    try:
        updated = context.employees.set_active(
            employee_id, active=body.active, requester_role=user.role
        )
    except ClockManagerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return presenters.employee(updated)


class LinkIn(BaseModel):
    device_id: int
    user_id: str
    device_uid: int | None = None


@router.post("/api/employees/{employee_id}/links", response_model=schemas.EmployeeOut)
def link_device(
    request: Request, user: CurrentUser, employee_id: int, body: LinkIn
) -> schemas.EmployeeOut:
    """Map an employee to a ``(device, user ID)`` pair on another clock."""
    context: ApplicationContext = get_context(request)
    try:
        updated = context.employees.link_device(
            employee_id,
            device_id=body.device_id,
            user_id=body.user_id,
            device_uid=body.device_uid,
            requester_role=user.role,
        )
    except ClockManagerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return presenters.employee(updated)


@router.delete("/api/employees/{employee_id}/links/{device_id}", response_model=schemas.EmployeeOut)
def unlink_device(
    request: Request, user: CurrentUser, employee_id: int, device_id: int
) -> schemas.EmployeeOut:
    """Remove an employee's mapping for one device."""
    context: ApplicationContext = get_context(request)
    try:
        updated = context.employees.unlink_device(
            employee_id, device_id=device_id, requester_role=user.role
        )
    except ClockManagerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if updated is None:  # pragma: no cover - service raises for unknown employees
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown employee id {employee_id}.",
        )
    return presenters.employee(updated)


# -- pay schedules -------------------------------------------------------------


@router.get("/api/schedules", response_model=list[schemas.ScheduleOut])
def list_schedules(request: Request, user: CurrentUser) -> list[schemas.ScheduleOut]:
    """Stored pay schedules and which one is active."""
    _ = user
    context: ApplicationContext = get_context(request)
    return [presenters.schedule(item) for item in context.timesheets.list_schedules()]


@router.post(
    "/api/schedules", response_model=schemas.ScheduleOut, status_code=status.HTTP_201_CREATED
)
def create_schedule(
    request: Request, user: CurrentUser, body: schemas.ScheduleIn
) -> schemas.ScheduleOut:
    """Add a pay schedule. Administrators only (``payroll.manage``)."""
    require_permission(user, Permission.MANAGE_PAYROLL)
    context: ApplicationContext = get_context(request)
    try:
        schedule_type = PayScheduleType(body.schedule_type.strip().lower())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown schedule type {body.schedule_type!r}.",
        ) from exc
    try:
        created = context.timesheets.create_schedule(
            requester_role=user.role,
            name=body.name,
            schedule_type=schedule_type,
            anchor_date=body.anchor_date,
            timezone=body.timezone,
            day_cutoff_hour=body.day_cutoff_hour,
            duplicate_interval_seconds=body.duplicate_interval_seconds,
            max_shift_hours=body.max_shift_hours,
            display_decimal=body.display_decimal,
            daily_overtime_hours=body.daily_overtime_hours,
            weekly_overtime_hours=body.weekly_overtime_hours,
            activate=body.activate,
        )
    except (ClockManagerError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return presenters.schedule(created)


@router.post("/api/schedules/{schedule_id}/activate", response_model=schemas.ScheduleOut)
def activate_schedule(request: Request, user: CurrentUser, schedule_id: int) -> schemas.ScheduleOut:
    """Make one schedule the active one. Administrators only."""
    require_permission(user, Permission.MANAGE_PAYROLL)
    context: ApplicationContext = get_context(request)
    try:
        updated = context.timesheets.activate_schedule(schedule_id, requester_role=user.role)
    except ClockManagerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return presenters.schedule(updated)


# -- timesheets ----------------------------------------------------------------


@router.get("/api/timesheets", response_model=schemas.TimesheetOut)
def build_timesheet(
    request: Request,
    user: CurrentUser,
    employee_id: int = Query(gt=0),
    start: date = Query(),
    end: date = Query(),
) -> schemas.TimesheetOut:
    """Calculate the derived timesheet for one employee and period."""
    _ = user
    context: ApplicationContext = get_context(request)
    active = context.timesheets.ensure_default_schedule()
    try:
        sheet = context.timesheets.build(
            TimesheetRequest(employee_id=employee_id, start=start, end=end),
            schedule=active,
        )
    except ClockManagerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return presenters.timesheet(sheet, decimal=active.display_decimal)


@router.get("/api/timesheets/current", response_model=schemas.TimesheetOut)
def current_timesheet(
    request: Request,
    user: CurrentUser,
    employee_id: int = Query(gt=0),
) -> schemas.TimesheetOut:
    """The timesheet for the period containing today."""
    _ = user
    context: ApplicationContext = get_context(request)
    active = context.timesheets.ensure_default_schedule()
    try:
        sheet = context.timesheets.build_for_current_period(employee_id)
    except ClockManagerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return presenters.timesheet(sheet, decimal=active.display_decimal)
