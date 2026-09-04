"""Device settings view.

Edits the locally stored device profile. Saving writes to the application
database only — PHASE 02 performs no device writes, so nothing here changes
anything on the clock itself.

The communication password is masked and is never written to a log or shown in
a status message (``SECURITY.md``).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.gui.views.common import run_off_thread, section_label
from clockmanager.services.devices import (
    DEFAULT_DEVICE_PORT,
    ConnectionTestResult,
    DeviceProfile,
    DeviceService,
)

__all__ = ["DeviceSettingsView"]

_logger = get_logger(__name__)

_NEW_DEVICE_LABEL = "<Add a new device>"


class DeviceSettingsView(QWidget):
    """Form for the fields listed in the PHASE 02 specification."""

    def __init__(self, service: DeviceService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._profiles: list[DeviceProfile] = []
        self._loading = False

        self._selector = QComboBox(self)
        self._selector.currentIndexChanged.connect(self._on_selection_changed)

        self._name = QLineEdit(self)
        self._name.setPlaceholderText("Front door clock")

        self._host = QLineEdit(self)
        self._host.setPlaceholderText("192.168.1.50")

        self._port = QSpinBox(self)
        self._port.setRange(1, 65535)
        self._port.setValue(DEFAULT_DEVICE_PORT)

        self._password = QLineEdit(self)
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setPlaceholderText("0 if the device has no password")
        self._password.setToolTip(
            "Device communication password. Stored locally and never shown or logged."
        )

        self._timeout = QDoubleSpinBox(self)
        self._timeout.setRange(0.5, 120.0)
        self._timeout.setSingleStep(0.5)
        self._timeout.setSuffix(" s")
        self._timeout.setValue(10.0)

        self._auto_reconnect = QCheckBox("Reconnect automatically after a dropped connection", self)
        self._auto_reconnect.setChecked(True)

        self._sync_interval = QSpinBox(self)
        self._sync_interval.setRange(30, 86400)
        self._sync_interval.setSingleStep(30)
        self._sync_interval.setSuffix(" s")
        self._sync_interval.setValue(300)
        self._sync_interval.setToolTip(
            "How often attendance will be synchronised once synchronisation is "
            "implemented (PHASE 04)."
        )

        self._enabled = QCheckBox("Device is enabled", self)
        self._enabled.setChecked(True)

        form = QFormLayout()
        form.addRow("Device", self._selector)
        form.addRow("Name", self._name)
        form.addRow("IP address or hostname", self._host)
        form.addRow("Port", self._port)
        form.addRow("Communication password", self._password)
        form.addRow("Timeout", self._timeout)
        form.addRow("Sync interval", self._sync_interval)
        form.addRow("", self._auto_reconnect)
        form.addRow("", self._enabled)

        group = QGroupBox("Connection settings", self)
        group.setLayout(form)

        self._save_button = QPushButton("Save", self)
        self._save_button.clicked.connect(self._save)
        self._test_button = QPushButton("Test connection", self)
        self._test_button.clicked.connect(self._test_connection)
        self._delete_button = QPushButton("Remove device", self)
        self._delete_button.clicked.connect(self._delete)

        buttons = QHBoxLayout()
        buttons.addWidget(self._save_button)
        buttons.addWidget(self._test_button)
        buttons.addStretch(1)
        buttons.addWidget(self._delete_button)

        self._status = QLabel("", self)
        self._status.setWordWrap(True)

        notice = QLabel(
            "Saving stores these settings on this computer. It does not change "
            "anything on the clock — this build never writes to a device.",
            self,
        )
        notice.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(section_label("Device settings", self))
        layout.addWidget(group)
        layout.addLayout(buttons)
        layout.addWidget(self._status)
        layout.addWidget(notice)
        layout.addStretch(1)
        self.setLayout(layout)

        self.refresh()

    # -- data -----------------------------------------------------------------

    def refresh(self) -> None:
        """Reload stored profiles into the selector."""
        run_off_thread(
            self._service.list_profiles,
            on_success=self._on_profiles_loaded,
            on_failure=self._on_failure,
        )

    def _on_profiles_loaded(self, profiles: Any) -> None:
        if not isinstance(profiles, list):  # pragma: no cover - defensive
            return
        self._profiles = profiles

        self._loading = True
        self._selector.clear()
        for profile in self._profiles:
            suffix = "" if profile.enabled else "  (disabled)"
            self._selector.addItem(f"{profile.name}{suffix}")
        self._selector.addItem(_NEW_DEVICE_LABEL)
        self._loading = False

        self._selector.setCurrentIndex(0 if self._profiles else self._selector.count() - 1)
        self._on_selection_changed()

    def _current_profile(self) -> DeviceProfile | None:
        index = self._selector.currentIndex()
        if 0 <= index < len(self._profiles):
            return self._profiles[index]
        return None

    def _on_selection_changed(self) -> None:
        if self._loading:
            return
        profile = self._current_profile()
        if profile is None:
            self._show_blank_form()
        else:
            self._show_profile(profile)

    def _show_blank_form(self) -> None:
        self._name.setText("")
        self._host.setText("")
        self._port.setValue(DEFAULT_DEVICE_PORT)
        self._password.setText("")
        self._timeout.setValue(10.0)
        self._auto_reconnect.setChecked(True)
        self._sync_interval.setValue(300)
        self._enabled.setChecked(True)
        self._delete_button.setEnabled(False)
        self._status.setText("")

    def _show_profile(self, profile: DeviceProfile) -> None:
        self._name.setText(profile.name)
        self._host.setText(profile.host)
        self._port.setValue(profile.port)
        # The stored password is never echoed back into the form. An empty box
        # means "leave the saved password unchanged".
        self._password.setText("")
        self._password.setPlaceholderText(
            "Unchanged (a password is set)"
            if profile.has_communication_password
            else "0 if the device has no password"
        )
        self._timeout.setValue(profile.timeout_seconds)
        self._auto_reconnect.setChecked(profile.auto_reconnect)
        self._sync_interval.setValue(profile.sync_interval_seconds)
        self._enabled.setChecked(profile.enabled)
        self._delete_button.setEnabled(True)
        self._status.setText("")

    def _form_profile(self) -> DeviceProfile:
        existing = self._current_profile()
        password = existing.communication_password if existing is not None else 0
        typed = self._password.text().strip()
        if typed:
            password = int(typed) if typed.isdigit() else -1

        return DeviceProfile(
            device_id=existing.device_id if existing is not None else None,
            name=self._name.text().strip(),
            host=self._host.text().strip(),
            port=self._port.value(),
            communication_password=password,
            timeout_seconds=self._timeout.value(),
            auto_reconnect=self._auto_reconnect.isChecked(),
            sync_interval_seconds=self._sync_interval.value(),
            enabled=self._enabled.isChecked(),
        )

    # -- actions --------------------------------------------------------------

    def _save(self) -> None:
        typed = self._password.text().strip()
        if typed and not typed.isdigit():
            self._status.setText("Communication password must be a number.")
            return

        profile = self._form_profile()
        problems = profile.validate()
        if problems:
            self._status.setText(" ".join(problems))
            return

        self._set_busy(True)
        run_off_thread(
            lambda: self._service.save_profile(profile),
            on_success=self._on_saved,
            on_failure=self._on_failure,
        )

    def _on_saved(self, _profile: Any) -> None:
        self._set_busy(False)
        self._status.setText("Settings saved on this computer.")
        self.refresh()

    def _test_connection(self) -> None:
        profile = self._form_profile()
        problems = profile.validate()
        if problems:
            self._status.setText(" ".join(problems))
            return

        self._set_busy(True)
        self._status.setText(f"Connecting to {profile.endpoint}…")
        run_off_thread(
            lambda: self._service.test_connection(profile),
            on_success=self._on_tested,
            on_failure=self._on_failure,
        )

    def _on_tested(self, result: Any) -> None:
        self._set_busy(False)
        if not isinstance(result, ConnectionTestResult):  # pragma: no cover - defensive
            return
        self._status.setText(result.summary)

    def _delete(self) -> None:
        profile = self._current_profile()
        if profile is None or profile.device_id is None:
            return

        confirmed = QMessageBox.question(
            self,
            "Remove device",
            f"Remove {profile.name!r} from this computer?\n\n"
            "This deletes the locally stored settings and any locally stored "
            "users and attendance for this device. Nothing on the clock itself "
            "is changed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        device_id = profile.device_id
        self._set_busy(True)
        run_off_thread(
            lambda: self._service.delete_profile(device_id),
            on_success=self._on_deleted,
            on_failure=self._on_failure,
        )

    def _on_deleted(self, _result: Any) -> None:
        self._set_busy(False)
        self._status.setText("Device removed from this computer.")
        self.refresh()

    def _on_failure(self, message: str) -> None:
        self._set_busy(False)
        self._status.setText(message)

    def _set_busy(self, busy: bool) -> None:
        for widget in (self._save_button, self._test_button, self._delete_button):
            widget.setEnabled(not busy)
        if not busy:
            self._delete_button.setEnabled(self._current_profile() is not None)
