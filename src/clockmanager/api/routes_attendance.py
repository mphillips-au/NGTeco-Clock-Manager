"""Stored attendance reads and sync history (PHASE 17).

Everything here is a local-database read: history, employees, timesheets
and reports keep working with the clock down, exactly like the GUI's
offline behaviour. ``occurred_at`` stays naive device-local time by
design (``STATUS.md``) — it is never labelled UTC.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from clockmanager.api import presenters, schemas
from clockmanager.api.deps import get_context, get_current_user
from clockmanager.services.application import ApplicationContext
from clockmanager.services.auth import AuthenticatedUser
from clockmanager.services.devices import DeviceProfile

__all__ = ["router"]

router = APIRouter(tags=["attendance"])

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


def _require_profile(context: ApplicationContext, device_id: int) -> DeviceProfile:
    profile = context.devices.get_profile(device_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown device id {device_id}.",
        )
    return profile


@router.get("/api/attendance/recent", response_model=list[schemas.AttendanceOut])
def recent_attendance(
    request: Request,
    user: CurrentUser,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[schemas.AttendanceOut]:
    """Stored punches across all devices, newest first."""
    _ = user
    context: ApplicationContext = get_context(request)
    return [presenters.attendance(item) for item in context.sync.list_recent(limit=limit)]


@router.get("/api/devices/{device_id}/attendance", response_model=list[schemas.AttendanceOut])
def device_attendance(
    request: Request,
    user: CurrentUser,
    device_id: int,
    limit: int = Query(default=500, ge=1, le=2000),
) -> list[schemas.AttendanceOut]:
    """Stored punches for one device, newest first."""
    _ = user
    context: ApplicationContext = get_context(request)
    profile = _require_profile(context, device_id)
    return [presenters.attendance(item) for item in context.sync.list_stored(profile, limit=limit)]


@router.get("/api/sync/history", response_model=list[schemas.SyncHistoryOut])
def recent_sync_history(
    request: Request,
    user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[schemas.SyncHistoryOut]:
    """Sync runs across all devices, newest first."""
    _ = user
    context: ApplicationContext = get_context(request)
    return [presenters.sync_history_row(row) for row in context.sync.recent_history(limit=limit)]
