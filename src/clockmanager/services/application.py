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
from clockmanager.services.devices import DeviceService, build_device, build_mock_device

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
        ]


@dataclass(frozen=True, slots=True)
class ApplicationContext:
    """Everything a front end needs, with no knowledge of how it was built."""

    config: AppConfig
    database: Database
    log_file: Path
    schema_version: int

    @property
    def devices(self) -> DeviceService:
        """Device application service, wired to the configured device factory."""
        factory = build_mock_device if self.config.use_mock_device else build_device
        return DeviceService(self.database, device_factory=factory)

    def status(self) -> ApplicationStatus:
        """Collect a display-ready status snapshot."""
        with self.database.session() as session:
            known_devices = DeviceRepository(session).count()

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
    )
