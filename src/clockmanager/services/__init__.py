"""Application services: the only entry point the GUI is allowed to use."""

from __future__ import annotations

from clockmanager.services.application import ApplicationContext, ApplicationStatus, bootstrap
from clockmanager.services.audit import AuditAction, AuditEntry, AuditOutcome, AuditService
from clockmanager.services.auth import AuthenticatedUser, AuthService, AuthSession
from clockmanager.services.backup import (
    BackupService,
    BackupSummary,
    OfflineDeviceState,
    OfflineReport,
    RestorePreview,
    RestoreResult,
)
from clockmanager.services.devices import (
    DEFAULT_DEVICE_PORT,
    ConnectionTestResult,
    DeviceProfile,
    DeviceService,
    DeviceStatus,
    DiscoveredDevice,
    MockDeviceFactory,
    build_device,
    build_mock_device,
)
from clockmanager.services.diagnostics import (
    CapabilityReport,
    ConnectionReport,
    ConnectionStep,
    DiagnosticsService,
    LiveWindow,
    ProtocolTrace,
)
from clockmanager.services.employees import EmployeeProfile, EmployeeService
from clockmanager.services.reports import ReportService
from clockmanager.services.sync import StoredAttendance, SyncResult, SyncService, SyncSummary
from clockmanager.services.timesheets import PayScheduleProfile, TimesheetRequest, TimesheetService
from clockmanager.services.users import DeleteImpact, UserService, WriteAvailability

__all__ = [
    "DEFAULT_DEVICE_PORT",
    "ApplicationContext",
    "ApplicationStatus",
    "AuditAction",
    "AuditEntry",
    "AuditOutcome",
    "AuditService",
    "AuthService",
    "AuthSession",
    "AuthenticatedUser",
    "BackupService",
    "BackupSummary",
    "CapabilityReport",
    "ConnectionReport",
    "ConnectionStep",
    "ConnectionTestResult",
    "DeleteImpact",
    "DeviceProfile",
    "DeviceService",
    "DeviceStatus",
    "DiagnosticsService",
    "DiscoveredDevice",
    "EmployeeProfile",
    "EmployeeService",
    "LiveWindow",
    "MockDeviceFactory",
    "OfflineDeviceState",
    "OfflineReport",
    "PayScheduleProfile",
    "ProtocolTrace",
    "ReportService",
    "RestorePreview",
    "RestoreResult",
    "StoredAttendance",
    "SyncResult",
    "SyncService",
    "SyncSummary",
    "TimesheetRequest",
    "TimesheetService",
    "UserService",
    "WriteAvailability",
    "bootstrap",
    "build_device",
    "build_mock_device",
]
