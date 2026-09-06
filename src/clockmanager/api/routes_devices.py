"""Devices, discovery, device users, sync and live state (PHASE 17).

Every device contact happens server-side through the application services.
The browser never opens TCP 4370 itself — that is the whole point of this
boundary (``SECURITY.md``: "Future web").

Reads need only a login. Anything that changes a stored profile, probes
the network or triggers device I/O needs the permission named on the
route; the service layer refuses regardless.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from clockmanager.api import presenters, schemas
from clockmanager.api.deps import get_context, get_current_user, require_permission
from clockmanager.domain.auth import Permission
from clockmanager.domain.users import CredentialAction, UserDraft
from clockmanager.errors import ClockManagerError, DeviceError
from clockmanager.services.application import ApplicationContext
from clockmanager.services.auth import AuthenticatedUser
from clockmanager.services.devices import DeviceProfile

__all__ = ["router"]

router = APIRouter(tags=["devices"])

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


def _require_profile(context: ApplicationContext, device_id: int) -> DeviceProfile:
    profile = context.devices.get_profile(device_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown device id {device_id}.",
        )
    return profile


def _to_profile(body: schemas.DeviceIn, *, device_id: int | None) -> DeviceProfile:
    try:
        return DeviceProfile(
            device_id=device_id,
            name=body.name.strip(),
            host=body.host.strip(),
            port=body.port,
            timeout_seconds=body.timeout_seconds,
            auto_reconnect=body.auto_reconnect,
            sync_interval_seconds=body.sync_interval_seconds,
            enabled=body.enabled,
            communication_password=body.communication_password or 0,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# -- device profiles -----------------------------------------------------------


@router.get("/api/devices", response_model=list[schemas.DeviceOut])
def list_devices(request: Request, user: CurrentUser) -> list[schemas.DeviceOut]:
    """Stored device profiles. No hardware is touched."""
    _ = user
    context: ApplicationContext = get_context(request)
    return [presenters.device(profile) for profile in context.devices.list_profiles()]


@router.post("/api/devices", response_model=schemas.DeviceOut, status_code=status.HTTP_201_CREATED)
def create_device(request: Request, user: CurrentUser, body: schemas.DeviceIn) -> schemas.DeviceOut:
    """Store a device profile (local database only). Administrators only."""
    require_permission(user, Permission.MANAGE_DEVICE_SETTINGS)
    context: ApplicationContext = get_context(request)
    try:
        saved = context.devices.save_profile(
            _to_profile(body, device_id=None), requester_role=user.role
        )
    except ClockManagerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return presenters.device(saved)


@router.get("/api/devices/statuses", response_model=list[schemas.DeviceStatusOut])
def device_statuses(request: Request, user: CurrentUser) -> list[schemas.DeviceStatusOut]:
    """Locally known state for every stored profile. No hardware is touched."""
    _ = user
    context: ApplicationContext = get_context(request)
    return [presenters.device_status(item) for item in context.devices.statuses()]


@router.get("/api/devices/{device_id}", response_model=schemas.DeviceOut)
def get_device(request: Request, user: CurrentUser, device_id: int) -> schemas.DeviceOut:
    """One stored device profile."""
    _ = user
    context: ApplicationContext = get_context(request)
    return presenters.device(_require_profile(context, device_id))


@router.put("/api/devices/{device_id}", response_model=schemas.DeviceOut)
def update_device(
    request: Request, user: CurrentUser, device_id: int, body: schemas.DeviceIn
) -> schemas.DeviceOut:
    """Update a stored profile (local database only). Administrators only.

    Omit ``communication_password`` (or send null) to keep the stored
    secret; sending ``0`` clears it.
    """
    require_permission(user, Permission.MANAGE_DEVICE_SETTINGS)
    context: ApplicationContext = get_context(request)
    existing = _require_profile(context, device_id)
    password = (
        existing.communication_password
        if body.communication_password is None
        else body.communication_password
    )
    candidate = schemas.DeviceIn(
        name=body.name,
        host=body.host,
        port=body.port,
        timeout_seconds=body.timeout_seconds,
        auto_reconnect=body.auto_reconnect,
        sync_interval_seconds=body.sync_interval_seconds,
        enabled=body.enabled,
        communication_password=password,
    )
    try:
        saved = context.devices.save_profile(
            _to_profile(candidate, device_id=device_id), requester_role=user.role
        )
    except ClockManagerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return presenters.device(saved)


@router.delete("/api/devices/{device_id}")
def delete_device(request: Request, user: CurrentUser, device_id: int) -> dict[str, bool]:
    """Remove a stored profile and its local data. Never touches a device."""
    require_permission(user, Permission.MANAGE_DEVICE_SETTINGS)
    context: ApplicationContext = get_context(request)
    _require_profile(context, device_id)
    context.devices.delete_profile(device_id, requester_role=user.role)
    return {"deleted": True}


@router.get("/api/devices/{device_id}/status", response_model=schemas.DeviceStatusOut)
def device_status(request: Request, user: CurrentUser, device_id: int) -> schemas.DeviceStatusOut:
    """Locally known state for one stored profile. No hardware is touched."""
    _ = user
    context: ApplicationContext = get_context(request)
    return presenters.device_status(context.devices.status(_require_profile(context, device_id)))


class ConnectionTestOut(BaseModel):
    ok: bool
    summary: str
    error_type: str | None = None


@router.get("/api/devices/{device_id}/inspect", response_model=schemas.DeviceInspectionOut)
def inspect_device(
    request: Request, user: CurrentUser, device_id: int
) -> schemas.DeviceInspectionOut:
    """Read everything the device can say about itself, in one connection.

    Identity, capacities, allow-listed settings, fingerprint enrolments
    (slot metadata only, never templates) and the device's own operation
    log. Read-only end to end; a failed connection arrives as a failed
    inspection, and an unreadable section arrives as a note.
    """
    _ = user
    context: ApplicationContext = get_context(request)
    return presenters.device_inspection(
        context.devices.inspect(_require_profile(context, device_id))
    )


@router.post("/api/devices/{device_id}/test", response_model=ConnectionTestOut)
def test_connection(request: Request, user: CurrentUser, device_id: int) -> ConnectionTestOut:
    """Connect, read the device identity and disconnect. Administrators only."""
    require_permission(user, Permission.MANAGE_DEVICE_SETTINGS)
    context: ApplicationContext = get_context(request)
    result = context.devices.test_connection(_require_profile(context, device_id))
    return ConnectionTestOut(ok=result.ok, summary=result.summary, error_type=result.error_type)


# -- discovery (read-only; registration is the only storing path) --------------


class ProbeOut(BaseModel):
    host: str
    port: int
    reachable: bool


@router.get("/api/discovery/probe", response_model=ProbeOut)
def probe_host(
    request: Request,
    user: CurrentUser,
    host: str = Query(min_length=1),
    port: int = Query(default=4370, ge=1, le=65535),
) -> ProbeOut:
    """Check whether ``host:port`` answers TCP. Sends no command."""
    require_permission(user, Permission.MANAGE_DEVICE_SETTINGS)
    context: ApplicationContext = get_context(request)
    found = context.devices.probe_host(host, port=port)
    return ProbeOut(host=found.host, port=found.port, reachable=found.reachable)


class ScanIn(BaseModel):
    hosts: list[str] = Field(max_length=1024)


class DiscoveredOut(BaseModel):
    host: str
    port: int
    reachable: bool
    model: str | None = None
    serial_number: str | None = None


@router.post("/api/discovery/scan", response_model=list[DiscoveredOut])
def scan_hosts(request: Request, user: CurrentUser, body: ScanIn) -> list[DiscoveredOut]:
    """Probe up to 1024 addresses for TCP 4370. Sends no command."""
    require_permission(user, Permission.MANAGE_DEVICE_SETTINGS)
    context: ApplicationContext = get_context(request)
    try:
        found = context.devices.scan_network(body.hosts)
    except ClockManagerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return [
        DiscoveredOut(
            host=item.host,
            port=item.port,
            reachable=item.reachable,
            model=item.identity.model if item.identity is not None else None,
            serial_number=item.identity.serial_number if item.identity is not None else None,
        )
        for item in found
    ]


class IdentifyOut(BaseModel):
    host: str
    port: int
    ok: bool
    model: str | None = None
    platform: str | None = None
    firmware_version: str | None = None
    serial_number: str | None = None
    error: str = ""


@router.get("/api/discovery/identify", response_model=IdentifyOut)
def identify_device(
    request: Request,
    user: CurrentUser,
    host: str = Query(min_length=1),
    port: int = Query(default=4370, ge=1, le=65535),
) -> IdentifyOut:
    """Connect, read a snapshot, always disconnect. Stores nothing."""
    require_permission(user, Permission.MANAGE_DEVICE_SETTINGS)
    context: ApplicationContext = get_context(request)
    try:
        found = context.devices.identify(host, port=port)
    except ClockManagerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not found.reachable or found.identity is None:
        return IdentifyOut(host=host, port=port, ok=False, error=found.error)
    return IdentifyOut(
        host=host,
        port=port,
        ok=True,
        model=found.identity.model,
        platform=found.identity.platform,
        firmware_version=found.identity.firmware_version,
        serial_number=found.identity.serial_number,
    )


class RegisterIn(BaseModel):
    name: str
    host: str
    port: int = 4370


@router.post(
    "/api/discovery/register",
    response_model=schemas.DeviceOut,
    status_code=status.HTTP_201_CREATED,
)
def register_discovered(request: Request, user: CurrentUser, body: RegisterIn) -> schemas.DeviceOut:
    """Store a discovered device as a named profile. The only storing path."""
    require_permission(user, Permission.MANAGE_DEVICE_SETTINGS)
    context: ApplicationContext = get_context(request)
    try:
        saved = context.devices.register_discovered(
            name=body.name, host=body.host, port=body.port, requester_role=user.role
        )
    except ClockManagerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return presenters.device(saved)


# -- users on a device ---------------------------------------------------------


@router.get("/api/devices/{device_id}/users", response_model=list[schemas.UserOut])
def list_device_users(request: Request, user: CurrentUser, device_id: int) -> list[schemas.UserOut]:
    """Read every user from the device. Credential contents are excluded."""
    _ = user
    context: ApplicationContext = get_context(request)
    return [
        presenters.device_user(item)
        for item in context.users.list_users(_require_profile(context, device_id))
    ]


@router.get(
    "/api/devices/{device_id}/users/enrolment", response_model=list[schemas.EnrolledUserOut]
)
def list_enrolment(
    request: Request, user: CurrentUser, device_id: int
) -> list[schemas.EnrolledUserOut]:
    """Users plus fingerprint-slot metadata (counts only, never templates)."""
    _ = user
    context: ApplicationContext = get_context(request)
    return [
        schemas.EnrolledUserOut(
            user=presenters.device_user(item.user),
            fingers=list(item.fingers),
            fingerprints_known=item.fingerprints_known,
        )
        for item in context.users.list_enrolment(_require_profile(context, device_id))
    ]


@router.get("/api/devices/{device_id}/users/{device_uid}", response_model=schemas.UserOut)
def get_device_user(
    request: Request, user: CurrentUser, device_id: int, device_uid: int
) -> schemas.UserOut:
    """Read one user by device UID."""
    _ = user
    context: ApplicationContext = get_context(request)
    found = context.users.find_user(_require_profile(context, device_id), device_uid)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No user with device UID {device_uid} is on the device.",
        )
    return presenters.device_user(found)


def _to_draft(body: schemas.UserDraftIn) -> UserDraft:
    try:
        action = CredentialAction(body.credential_action.strip().lower())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown credential_action {body.credential_action!r}.",
        ) from exc
    try:
        return UserDraft(
            user_id=body.user_id,
            first_name=body.first_name,
            last_name=body.last_name,
            privilege=body.privilege,
            device_uid=body.device_uid,
            credential_action=action,
            password=body.password,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/api/devices/{device_id}/users", response_model=schemas.UserOut)
def save_device_user(
    request: Request, user: CurrentUser, device_id: int, body: schemas.UserDraftIn
) -> schemas.UserOut:
    """Create or update one user on the device, with read-back verification.

    The service refuses unless this installation unlocks device writing;
    every attempt is audited. ``password`` is write-only and never returned.
    """
    context: ApplicationContext = get_context(request)
    try:
        outcome = context.users.save_user(
            _require_profile(context, device_id),
            _to_draft(body),
            requester_role=user.role,
        )
    except DeviceError:
        raise
    except ClockManagerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return presenters.device_user(outcome.user)


@router.delete("/api/devices/{device_id}/users/{device_uid}", response_model=schemas.UserOut)
def delete_device_user(
    request: Request,
    user: CurrentUser,
    device_id: int,
    device_uid: int,
    confirmed: bool = Query(default=False),
) -> schemas.UserOut:
    """Delete one user from the device. Needs ``?confirmed=true``."""
    context: ApplicationContext = get_context(request)
    if not confirmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deleting a user requires explicit confirmation.",
        )
    try:
        deleted = context.users.delete_user(
            _require_profile(context, device_id),
            device_uid,
            confirmed=True,
            requester_role=user.role,
        )
    except DeviceError:
        raise
    except ClockManagerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return presenters.device_user(deleted)


# -- sync ----------------------------------------------------------------------


class SyncIn(BaseModel):
    mode: str = "manual"


@router.post("/api/devices/{device_id}/sync", response_model=schemas.SyncResultOut)
def run_sync(
    request: Request, user: CurrentUser, device_id: int, body: SyncIn
) -> schemas.SyncResultOut:
    """Re-read the device log and store only what is new.

    ``mode`` is ``manual`` (default), ``initial``, ``incremental`` or
    ``recovery``. A device failure arrives as a failed result, not a raise.
    """
    require_permission(user, Permission.SYNC_ATTENDANCE)
    context: ApplicationContext = get_context(request)
    profile = _require_profile(context, device_id)
    mode = body.mode.strip().lower()
    if mode == "manual":
        result = context.sync.manual_sync(profile, requester_role=user.role)
    elif mode == "initial":
        result = context.sync.initial_sync(profile)
    elif mode == "incremental":
        result = context.sync.incremental_sync(profile)
    elif mode == "recovery":
        result = context.sync.recover(profile)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown sync mode {body.mode!r}.",
        )
    return presenters.sync_result(result)


@router.get("/api/devices/{device_id}/sync/history", response_model=list[schemas.SyncHistoryOut])
def sync_history(
    request: Request,
    user: CurrentUser,
    device_id: int,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[schemas.SyncHistoryOut]:
    """Sync runs for one device, newest first. Never touches hardware."""
    _ = user
    context: ApplicationContext = get_context(request)
    profile = _require_profile(context, device_id)
    return [presenters.sync_history_row(row) for row in context.sync.history(profile, limit=limit)]


# -- live state ----------------------------------------------------------------


@router.get("/api/live/status", response_model=list[schemas.LiveStatusOut])
def live_status(request: Request, user: CurrentUser) -> list[schemas.LiveStatusOut]:
    """Per-device live state: stored counts, live-sourced counts, last sync.

    This is the stored live state, not an open socket: live-capture punches
    are recorded by the service and re-read here, so a browser refresh never
    misses one and no browser ever holds a device connection.
    """
    _ = user
    context: ApplicationContext = get_context(request)
    rows: list[schemas.LiveStatusOut] = []
    for profile in context.devices.list_profiles():
        if profile.device_id is None:
            continue
        try:
            stored = context.sync.list_stored(profile, limit=10_000)
        except ClockManagerError:
            stored = []
        summary = context.sync.summary(profile)
        rows.append(
            schemas.LiveStatusOut(
                device_id=profile.device_id,
                device_name=profile.name,
                stored_events=len(stored),
                live_events_stored=sum(1 for item in stored if item.source == "live"),
                last_success_at=summary.last_success_at,
                last_outcome=summary.last_outcome,
                last_error=summary.last_error,
            )
        )
    return rows


@router.get("/api/live/recent", response_model=list[schemas.AttendanceOut])
def live_recent(
    request: Request, user: CurrentUser, limit: int = Query(default=100, ge=1, le=1000)
) -> list[schemas.AttendanceOut]:
    """Stored punches captured through live capture, newest first."""
    _ = user
    context: ApplicationContext = get_context(request)
    recent = context.sync.list_recent(limit=1000)
    live = [item for item in recent if item.source == "live"][:limit]
    return [presenters.attendance(item) for item in live]
