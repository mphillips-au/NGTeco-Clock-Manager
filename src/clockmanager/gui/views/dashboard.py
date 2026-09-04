"""Dashboard view: the at-a-glance state of the configured device."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clockmanager.gui.views.common import build_table, fill_table, run_off_thread, section_label
from clockmanager.services.application import ApplicationContext, ApplicationStatus
from clockmanager.services.devices import ConnectionTestResult, DeviceProfile, DeviceService

__all__ = ["DashboardView"]


class DashboardView(QWidget):
    """Shows the active device, its last known state and application status."""

    def __init__(
        self,
        context: ApplicationContext,
        service: DeviceService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._service = service
        self._profile: DeviceProfile | None = None

        self._device_summary = QLabel("Loading…", self)
        self._device_summary.setWordWrap(True)

        self._device_table = build_table(["Item", "Value"], self, sortable=False)
        device_layout = QVBoxLayout()
        device_layout.addWidget(self._device_summary)
        device_layout.addWidget(self._device_table)
        self._device_group = QGroupBox("Device", self)
        self._device_group.setLayout(device_layout)

        self._app_table = build_table(["Item", "Value"], self, sortable=False)
        app_layout = QVBoxLayout()
        app_layout.addWidget(self._app_table)
        app_group = QGroupBox("Application", self)
        app_group.setLayout(app_layout)

        self._connect_button = QPushButton("Check device connection", self)
        self._connect_button.clicked.connect(self.check_connection)
        self._refresh_button = QPushButton("Refresh", self)
        self._refresh_button.clicked.connect(self.refresh)

        buttons = QHBoxLayout()
        buttons.addWidget(self._connect_button)
        buttons.addWidget(self._refresh_button)
        buttons.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(section_label("Dashboard", self))
        layout.addLayout(buttons)
        layout.addWidget(self._device_group, stretch=1)
        layout.addWidget(app_group, stretch=1)
        self.setLayout(layout)

        self.refresh()

    def refresh(self) -> None:
        """Reload the active profile and application status off the UI thread."""
        self._refresh_button.setEnabled(False)
        run_off_thread(
            self._collect,
            on_success=self._on_loaded,
            on_failure=self._on_failure,
        )

    def _collect(self) -> tuple[DeviceProfile | None, ApplicationStatus]:
        return self._service.first_enabled_profile(), self._context.status()

    def _on_loaded(self, payload: Any) -> None:
        self._refresh_button.setEnabled(True)
        if not isinstance(payload, tuple):  # pragma: no cover - defensive
            return
        profile, status = payload
        self._profile = profile

        fill_table(self._app_table, [list(row) for row in status.as_rows()])

        if profile is None:
            self._device_summary.setText("No device is configured yet. Add one in Device settings.")
            self._connect_button.setEnabled(False)
            fill_table(self._device_table, [])
            return

        self._connect_button.setEnabled(profile.is_configured)
        self._device_summary.setText(
            f"{profile.name} at {profile.endpoint}"
            if profile.is_configured
            else f"{profile.name} has no address configured."
        )
        fill_table(
            self._device_table,
            [
                ["Name", profile.name],
                ["Address", profile.endpoint],
                ["Model", profile.model or "Not yet read from device"],
                ["Platform", profile.platform or "Not yet read from device"],
                ["Firmware", profile.firmware_version or "Not yet read from device"],
                ["Serial number", profile.serial_number or "Not yet read from device"],
                ["Communication password", "Set" if profile.has_communication_password else "None"],
                ["Auto reconnect", "On" if profile.auto_reconnect else "Off"],
                ["Sync interval", f"{profile.sync_interval_seconds} s"],
                ["Enabled", "Yes" if profile.enabled else "No"],
            ],
        )

    def check_connection(self) -> None:
        profile = self._profile
        if profile is None:
            return
        self._connect_button.setEnabled(False)
        self._device_summary.setText(f"Connecting to {profile.endpoint}…")
        run_off_thread(
            lambda: self._service.test_connection(profile),
            on_success=self._on_connection_checked,
            on_failure=self._on_failure,
        )

    def _on_connection_checked(self, result: Any) -> None:
        self._connect_button.setEnabled(True)
        if not isinstance(result, ConnectionTestResult):  # pragma: no cover - defensive
            return

        self._device_summary.setText(result.summary)
        if result.info is not None:
            fill_table(self._device_table, [list(row) for row in result.info.as_rows()])
            self.refresh()

    def _on_failure(self, message: str) -> None:
        self._refresh_button.setEnabled(True)
        self._connect_button.setEnabled(True)
        self._device_summary.setText(message)
