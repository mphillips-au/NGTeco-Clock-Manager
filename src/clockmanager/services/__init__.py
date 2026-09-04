"""Application services: the only entry point the GUI is allowed to use."""

from __future__ import annotations

from clockmanager.services.application import ApplicationContext, ApplicationStatus, bootstrap
from clockmanager.services.audit import AuditAction, AuditEntry, AuditOutcome, AuditService
from clockmanager.services.devices import (
    DEFAULT_DEVICE_PORT,
    ConnectionTestResult,
    DeviceProfile,
    DeviceService,
    MockDeviceFactory,
    build_device,
    build_mock_device,
)
from clockmanager.services.sync import StoredAttendance, SyncResult, SyncService, SyncSummary
from clockmanager.services.users import DeleteImpact, UserService, WriteAvailability

__all__ = [
    "DEFAULT_DEVICE_PORT",
    "ApplicationContext",
    "ApplicationStatus",
    "AuditAction",
    "AuditEntry",
    "AuditOutcome",
    "AuditService",
    "ConnectionTestResult",
    "DeleteImpact",
    "DeviceProfile",
    "DeviceService",
    "MockDeviceFactory",
    "StoredAttendance",
    "SyncResult",
    "SyncService",
    "SyncSummary",
    "UserService",
    "WriteAvailability",
    "bootstrap",
    "build_device",
    "build_mock_device",
]
