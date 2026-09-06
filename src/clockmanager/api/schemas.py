"""HTTP request/response shapes for the web/API boundary (PHASE 17).

Pydantic models only — no service, protocol or database behaviour. Secrets
are write-only by construction: no response schema carries a password,
a PIN or the device communication password.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "AccountCreate",
    "ApiUser",
    "AttendanceOut",
    "AuditOut",
    "DeviceIn",
    "DeviceInfoOut",
    "DeviceInspectionOut",
    "DeviceOptionOut",
    "DeviceOut",
    "DeviceStatusOut",
    "DeviceStorageOut",
    "EmployeeIn",
    "EmployeeOut",
    "EnrolledUserOut",
    "FingerprintSlotOut",
    "HealthOut",
    "LiveStatusOut",
    "LoginIn",
    "OperationLogEntryOut",
    "ReportOut",
    "RoleOut",
    "ScheduleIn",
    "ScheduleOut",
    "SetupIn",
    "StorageCounterOut",
    "SyncHistoryOut",
    "SyncResultOut",
    "TimesheetOut",
    "TokenOut",
    "UserDraftIn",
    "UserOut",
]


class SetupIn(BaseModel):
    username: str
    display_name: str = ""
    password: str = Field(min_length=1)


class LoginIn(BaseModel):
    username: str
    password: str = Field(min_length=1)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: ApiUser


class ApiUser(BaseModel):
    username: str
    display_name: str
    role: str
    role_label: str
    active: bool
    last_login_at: datetime | None = None


class AccountCreate(BaseModel):
    username: str
    display_name: str = ""
    role: str
    password: str = Field(min_length=1)


class RoleOut(BaseModel):
    role: str
    label: str
    permissions: list[str]


class HealthOut(BaseModel):
    application: str
    version: str
    schema_version: int
    known_devices: int
    needs_setup: bool


class DeviceIn(BaseModel):
    name: str
    host: str = ""
    port: int = 4370
    timeout_seconds: float = 10.0
    auto_reconnect: bool = True
    sync_interval_seconds: int = 300
    enabled: bool = True
    #: Write-only. Omit (or null) on update to keep the stored value.
    communication_password: int | None = None


class DeviceOut(BaseModel):
    device_id: int | None = None
    name: str
    host: str
    port: int
    timeout_seconds: float
    auto_reconnect: bool
    sync_interval_seconds: int
    enabled: bool
    #: Whether a communication password is stored — never the value.
    has_communication_password: bool
    model: str | None = None
    platform: str | None = None
    firmware_version: str | None = None
    serial_number: str | None = None
    last_seen_at: datetime | None = None


class DeviceStatusOut(BaseModel):
    device: DeviceOut
    stored_events: int
    last_success_at: datetime | None = None
    last_outcome: str | None = None
    last_error: str | None = None


class StorageCounterOut(BaseModel):
    label: str
    used: int | None = None
    capacity: int | None = None
    free: int | None = None
    describe: str


class DeviceStorageOut(BaseModel):
    users: StorageCounterOut
    fingerprints: StorageCounterOut
    attendance: StorageCounterOut
    faces: StorageCounterOut
    operation_log_records: int | None = None


class DeviceInfoOut(BaseModel):
    name: str
    model: str | None = None
    platform: str | None = None
    firmware_version: str | None = None
    serial_number: str | None = None
    device_time: datetime | None = None
    user_count: int | None = None
    attendance_count: int | None = None
    fingerprint_count: int | None = None
    face_count: int | None = None
    storage: DeviceStorageOut | None = None


class DeviceOptionOut(BaseModel):
    name: str
    label: str
    group: str
    value: str | None = None
    display_value: str
    answered: bool
    note: str = ""


class FingerprintSlotOut(BaseModel):
    device_uid: int
    finger_index: int
    valid: bool
    template_bytes: int


class OperationLogEntryOut(BaseModel):
    index: int
    operation: int
    operation_label: str
    operator_uid: int
    occurred_at: datetime | None = None
    parameters: list[int]


class DeviceInspectionOut(BaseModel):
    profile_name: str
    endpoint: str
    ok: bool
    info: DeviceInfoOut | None = None
    storage: DeviceStorageOut | None = None
    options: list[DeviceOptionOut]
    fingerprints: list[FingerprintSlotOut]
    operation_log: list[OperationLogEntryOut]
    notes: list[str]
    error: str = ""
    summary: str


class UserOut(BaseModel):
    device_uid: int
    user_id: str
    first_name: str
    last_name: str
    display_name: str
    privilege: int
    privilege_label: str
    has_credential_data: bool


class EnrolledUserOut(BaseModel):
    user: UserOut
    fingers: list[int]
    fingerprints_known: bool


class UserDraftIn(BaseModel):
    user_id: str
    first_name: str = ""
    last_name: str = ""
    privilege: int = 0
    #: Null means "create"; set to update the user with this device UID.
    device_uid: int | None = None
    credential_action: str = "preserve"
    #: Write-only PIN. Never returned, never logged.
    password: str | None = None


class AttendanceOut(BaseModel):
    record_id: int
    device_id: int
    device_uid: int | None = None
    user_id: str
    employee_name: str | None = None
    display_name: str
    occurred_at: datetime
    punch: int
    direction: str
    status: int
    received_at: datetime
    source: str
    event_key: str


class SyncHistoryOut(BaseModel):
    device_id: int | None = None
    mode: str
    source: str
    seen: int
    new: int
    duplicate: int
    outcome: str
    error: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None


class SyncResultOut(BaseModel):
    ok: bool
    device_id: int
    device_name: str
    source: str
    seen: int
    new: int
    duplicate: int
    is_recovery: bool
    error: str = ""
    error_type: str | None = None
    summary: str


class LiveStatusOut(BaseModel):
    device_id: int
    device_name: str
    stored_events: int
    live_events_stored: int
    last_success_at: datetime | None = None
    last_outcome: str | None = None
    last_error: str | None = None


class EmployeeIn(BaseModel):
    user_id: str
    first_name: str = ""
    last_name: str = ""
    active: bool = True
    department: str = ""
    position: str = ""
    email: str = ""
    notes: str = ""


class EmployeeOut(BaseModel):
    employee_id: int
    user_id: str
    first_name: str
    last_name: str
    display_name: str
    active: bool
    department: str
    position: str
    email: str
    notes: str
    device_user_ids: list[str]


class ScheduleIn(BaseModel):
    name: str
    schedule_type: str = "weekly"
    anchor_date: date
    timezone: str = "UTC"
    day_cutoff_hour: int = 0
    duplicate_interval_seconds: int = 60
    max_shift_hours: float = 16.0
    display_decimal: bool = False
    daily_overtime_hours: float | None = None
    weekly_overtime_hours: float | None = None
    activate: bool = False


class ScheduleOut(BaseModel):
    schedule_id: int
    name: str
    schedule_type: str
    anchor_date: date
    timezone: str
    day_cutoff_hour: int
    duplicate_interval_seconds: int
    max_shift_hours: float
    display_decimal: bool
    daily_overtime_hours: float | None = None
    weekly_overtime_hours: float | None = None
    is_active: bool


class TimesheetOut(BaseModel):
    employee_user_id: str
    period_start: date
    period_end: date
    total_seconds: int
    overtime_seconds: int
    total_display: str
    overtime_display: str
    days: list[dict[str, Any]]


class ReportOut(BaseModel):
    report_type: str
    title: str
    columns: list[str]
    rows: list[dict[str, str]]
    generated_at: datetime
    summary: str


class AuditOut(BaseModel):
    occurred_at: datetime
    actor: str
    action: str
    outcome: str
    detail: str
    device_name: str | None = None
    target: str | None = None
    target_uid: int | None = None
