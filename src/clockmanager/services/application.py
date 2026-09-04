"""Application bootstrap and lifetime.

The GUI, the CLI and (later) the headless Linux service all start the
application the same way: build the configuration, install logging, open the
database, and hand back an :class:`ApplicationContext`.

This module must remain free of PySide6 so the same bootstrap serves the future
Synology service.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from clockmanager import APPLICATION_NAME, __version__
from clockmanager.config import AppConfig, load_config
from clockmanager.diagnostics.logging_setup import configure_logging, get_logger
from clockmanager.persistence.database import (
    Database,
    create_database,
    initialise_database,
    schema_metadata,
)
from clockmanager.persistence.repositories import DeviceRepository
from clockmanager.services.audit import AuditService
from clockmanager.services.devices import (
    DeviceFactory,
    DeviceService,
    MockDeviceFactory,
    build_device,
)
from clockmanager.services.employees import EmployeeService
from clockmanager.services.reports import ReportService
from clockmanager.services.sync import SyncService
from clockmanager.services.timesheets import TimesheetService
from clockmanager.services.users import UserService

__all__ = ["ApplicationContext", "ApplicationStatus", "bootstrap"]

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ApplicationStatus:
    """A safe, display-ready snapshot of application state.

    Contains no credential, no device address and no raw protocol data, so it
    can be shown in any GUI view or written to a log.
    """

    application_name: str
    version: str
    python_version: str
    platform_summary: str
    data_dir: Path
    config_file: Path
    database_file: Path
    log_file: Path
    schema_version: int
    known_devices: int
    developer_mode: bool
    using_mock_device: bool = False
    device_writes_enabled: bool = False
    credential_writes_enabled: bool = False
    audit_entries: int = 0

    def as_rows(self) -> list[tuple[str, str]]:
        """Label/value pairs for diagnostics display."""
        return [
            ("Application", f"{self.application_name} {self.version}"),
            ("Python", self.python_version),
            ("Platform", self.platform_summary),
            ("Data folder", str(self.data_dir)),
            ("Configuration file", str(self.config_file)),
            ("Database file", str(self.database_file)),
            ("Log file", str(self.log_file)),
            ("Database schema", str(self.schema_version)),
            ("Known devices", str(self.known_devices)),
            ("Developer mode", "On" if self.developer_mode else "Off"),
            ("Device source", "Mock device" if self.using_mock_device else "Real hardware"),
            (
                "Device writing",
                "Enabled (unverified path)" if self.device_writes_enabled else "Disabled",
            ),
            (
                "Credential writing",
                "Enabled (unverified layout)" if self.credential_writes_enabled else "Disabled",
            ),
            ("Audit entries", str(self.audit_entries)),
        ]


@dataclass(frozen=True, slots=True)
class ApplicationContext:
    """Everything a front end needs, with no knowledge of how it was built."""

    config: AppConfig
    database: Database
    log_file: Path
    schema_version: int
    #: Built once per context. The mock factory is stateful, so rebuilding it
    #: per call would discard every write made through it.
    device_factory: DeviceFactory = build_device

    @property
    def devices(self) -> DeviceService:
        """Device application service, wired to the configured device factory."""
        return DeviceService(self.database, device_factory=self.device_factory)

    @property
    def audit(self) -> AuditService:
        """Append-only audit log service."""
        return AuditService(self.database)

    @property
    def users(self) -> UserService:
        """User management service, with device writing gated by configuration."""
        return UserService(
            self.devices,
            self.audit,
            writes_enabled=self.config.enable_device_writes,
            credential_writes_enabled=self.config.enable_credential_writes,
        )

    @property
    def sync(self) -> SyncService:
        """Attendance synchronisation service (PHASE 04)."""
        return SyncService(self.database, self.devices)

    @property
    def employees(self) -> EmployeeService:
        """Employee business records (PHASE 05)."""
        return EmployeeService(self.database, self.audit)

    @property
    def timesheets(self) -> TimesheetService:
        """Derived timesheets over immutable attendance (PHASE 05)."""
        return TimesheetService(self.database, self.employees)

    @property
    def reports(self) -> ReportService:
        """Derived reports and exports over immutable data (PHASE 06)."""
        return ReportService(self.database, self.employees, self.timesheets, self.audit)

    def status(self) -> ApplicationStatus:
        """Collect a display-ready status snapshot."""
        with self.database.session() as session:
            known_devices = DeviceRepository(session).count()
        audit_entries = self.audit.count()

        return ApplicationStatus(
            application_name=APPLICATION_NAME,
            version=__version__,
            python_version=platform.python_version(),
            platform_summary=f"{platform.system()} {platform.release()}",
            data_dir=self.config.paths.data_dir,
            config_file=self.config.paths.config_file,
            database_file=self.config.paths.database_file,
            log_file=self.log_file,
            schema_version=self.schema_version,
            known_devices=known_devices,
            developer_mode=self.config.developer_mode,
            using_mock_device=self.config.use_mock_device,
            device_writes_enabled=self.config.enable_device_writes,
            credential_writes_enabled=self.config.enable_credential_writes,
            audit_entries=audit_entries,
        )

    def schema_info(self) -> dict[str, str]:
        """Return stored database metadata (diagnostics)."""
        return schema_metadata(self.database)

    def shutdown(self) -> None:
        """Release resources. Safe to call more than once."""
        self.database.dispose()
        _logger.info("Application shut down")

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.shutdown()


def bootstrap(
    *,
    data_dir: Path | None = None,
    config: AppConfig | None = None,
    database_url: str | None = None,
) -> ApplicationContext:
    """Build configuration, logging and the database, in that order."""
    resolved_config = config if config is not None else load_config(data_dir=data_dir)
    resolved_config.paths.ensure()

    log_file = configure_logging(resolved_config)
    _logger.info(
        "Starting %s %s",
        APPLICATION_NAME,
        __version__,
        extra={
            "python_version": platform.python_version(),
            "executable": sys.executable,
            "data_dir": str(resolved_config.paths.data_dir),
        },
    )

    database = create_database(resolved_config, url=database_url)
    schema_version = initialise_database(database)

    return ApplicationContext(
        config=resolved_config,
        database=database,
        log_file=log_file,
        schema_version=schema_version,
        device_factory=MockDeviceFactory() if resolved_config.use_mock_device else build_device,
    )
