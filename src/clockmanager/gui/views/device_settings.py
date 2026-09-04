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
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.auth import Permission, Role, normalise_role
from clockmanager.domain.models import DeviceInfo
from clockmanager.gui.views.common import role_allows, run_off_thread, section_label
from clockmanager.services.devices import (
    DEFAULT_DEVICE_PORT,
    ConnectionTestResult,
    DeviceProfile,
    DeviceService,
    DeviceStatus,
    DiscoveredDevice,
)

__all__ = ["DeviceSettingsView"]

_logger = get_logger(__name__)

_NEW_DEVICE_LABEL = "<Add a new device>"


class DeviceSettingsView(QWidget):
    """Form for the fields listed in the PHASE 02 specification."""

    def __init__(
        self,
        service: DeviceService,
        parent: QWidget | None = None,
        *,
        role: Role | str | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        #: The logged-in role. Only administrators may save or remove
        #: profiles; ``None`` keeps the legacy behaviour for tests.
        self._role = normalise_role(role) if role is not None else None
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

        may_manage = role_allows(self._role, Permission.MANAGE_DEVICE_SETTINGS)

        self._state_label = QLabel("", self)
        self._state_label.setWordWrap(True)

        self._discover_host = QLineEdit(self)
        self._discover_host.setPlaceholderText("192.168.1.50")
        self._discover_port = QSpinBox(self)
        self._discover_port.setRange(1, 65535)
        self._discover_port.setValue(DEFAULT_DEVICE_PORT)
        self._check_button = QPushButton("Check this address", self)
        self._check_button.clicked.connect(self._check_address)
        self._scan_button = QPushButton("Scan local network", self)
        self._scan_button.clicked.connect(self._scan_network)
        self._scan_button.setToolTip(
            "Probe the local subnet for devices answering on the discovery port. "
            "Read-only: nothing found is stored or changed."
        )
        self._results = QListWidget(self)
        self._results.itemSelectionChanged.connect(self._on_result_selected)
        self._register_name = QLineEdit(self)
        self._register_name.setPlaceholderText("Front door clock")
        self._register_button = QPushButton("Register as new device", self)
        self._register_button.clicked.connect(self._register_discovered)
        self._register_button.setEnabled(False)
        self._discovery_status = QLabel("", self)
        self._discovery_status.setWordWrap(True)
        self._pending: DiscoveredDevice | None = None
        self._found: list[DiscoveredDevice] = []

        discovery_form = QFormLayout()
        discovery_form.addRow("Address", self._discover_host)
        discovery_form.addRow("Port", self._discover_port)
        discovery_form.addRow("Name for new device", self._register_name)

        discovery_buttons = QHBoxLayout()
        discovery_buttons.addWidget(self._check_button)
        discovery_buttons.addWidget(self._scan_button)
        discovery_buttons.addStretch(1)
        discovery_buttons.addWidget(self._register_button)

        discovery_group = QGroupBox("Discover devices (read-only)", self)
        discovery_layout = QVBoxLayout()
        discovery_layout.addLayout(discovery_form)
        discovery_layout.addLayout(discovery_buttons)
        discovery_layout.addWidget(self._results)
        discovery_layout.addWidget(self._discovery_status)
        discovery_note = QLabel(
            "Discovery only looks: checking probes one address, scanning probes "
            "the local subnet, and identifying reads the device snapshot. "
            "Nothing found is stored until you register it with a name.",
            self,
        )
        discovery_note.setWordWrap(True)
        discovery_layout.addWidget(discovery_note)
        discovery_group.setLayout(discovery_layout)
        if not may_manage:
            discovery_group.setEnabled(False)
        locked: QLabel | None = None
        if not may_manage:
            label = self._role.label if self._role is not None else "this role"
            locked = QLabel(
                f"Your role ({label}) cannot change device settings. "
                "Only administrators may save or remove a device.",
                self,
            )
            locked.setWordWrap(True)
            self._save_button.setEnabled(False)
            self._delete_button.setEnabled(False)

        layout = QVBoxLayout()
        layout.addWidget(section_label("Device settings", self))
        layout.addWidget(group)
        layout.addLayout(buttons)
        layout.addWidget(self._status)
        layout.addWidget(self._state_label)
        layout.addWidget(notice)
        layout.addWidget(discovery_group)
        if locked is not None:
            layout.addWidget(locked)
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
            self._state_label.setText("")
        else:
            self._show_profile(profile)
            self._load_state(profile)

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
        self._delete_button.setEnabled(role_allows(self._role, Permission.MANAGE_DEVICE_SETTINGS))
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
            lambda: self._service.save_profile(profile, requester_role=self._role),
            on_success=self._on_saved,
            on_failure=self._on_failure,
        )

    def _on_saved(self, _profile: Any) -> None:
        self._set_busy(False)
        self._status.setText("Settings saved on this computer.")
        self.refresh()

    def _load_state(self, profile: DeviceProfile) -> None:
        """Show last-seen and sync state for the selected profile, off-thread."""
        self._state_label.setText("Loading device state…")
        run_off_thread(
            lambda: self._service.status(profile),
            on_success=self._on_state_loaded,
            on_failure=self._on_state_failure,
        )

    def _on_state_loaded(self, status: Any) -> None:
        if not isinstance(status, DeviceStatus):  # pragma: no cover - defensive
            return
        self._state_label.setText(status.describe())

    def _on_state_failure(self, message: str) -> None:
        self._state_label.setText(f"Could not load device state: {message}")

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
            lambda: self._service.delete_profile(device_id, requester_role=self._role),
            on_success=self._on_deleted,
            on_failure=self._on_failure,
        )

    def _on_deleted(self, _result: Any) -> None:
        self._set_busy(False)
        self._status.setText("Device removed from this computer.")
        self.refresh()

    # -- discovery (PHASE 08, read-only) ----------------------------------------

    def _check_address(self) -> None:
        host = self._discover_host.text().strip()
        port = self._discover_port.value()
        if not host:
            self._discovery_status.setText("Enter an address to check.")
            return
        self._set_busy(True)
        self._discovery_status.setText(f"Identifying {host}:{port}…")
        run_off_thread(
            lambda: self._service.identify(host, port=port),
            on_success=self._on_identified,
            on_failure=self._on_discovery_failure,
        )

    def _scan_network(self) -> None:
        self._set_busy(True)
        self._discovery_status.setText("Scanning the local network…")
        run_off_thread(
            lambda: self._service.scan_network(port=self._discover_port.value()),
            on_success=self._on_scanned,
            on_failure=self._on_discovery_failure,
        )

    def _on_identified(self, found: Any) -> None:
        self._set_busy(False)
        if not isinstance(found, DiscoveredDevice):  # pragma: no cover - defensive
            return
        self._found = [found]
        self._results.clear()
        self._results.addItem(QListWidgetItem(found.summary))
        self._results.setCurrentRow(0)
        self._offer(found)

    def _on_scanned(self, found: Any) -> None:
        self._set_busy(False)
        if not isinstance(found, list):  # pragma: no cover - defensive
            return
        self._found = [item for item in found if isinstance(item, DiscoveredDevice)]
        reachable = [item for item in self._found if item.reachable]
        self._results.clear()
        for item in self._found:
            self._results.addItem(QListWidgetItem(item.summary))
        if not self._found:
            self._discovery_status.setText(
                "No addresses to scan: the local network could not be determined. "
                "Check a manually entered address instead."
            )
        elif not reachable:
            self._discovery_status.setText(
                f"Scanned {len(self._found)} address(es): no device answered. "
                "Check a manually entered address instead."
            )
        else:
            self._discovery_status.setText(
                f"Scanned {len(self._found)} address(es): "
                f"{len(reachable)} answered. Select one to register it."
            )
        self._pending = None
        self._register_button.setEnabled(False)

    def _on_result_selected(self) -> None:
        row = self._results.currentRow()
        if 0 <= row < len(self._found):
            self._offer(self._found[row])

    def _offer(self, found: DiscoveredDevice) -> None:
        """Show what was found and offer an explicit registration."""
        self._pending = found if found.reachable else None
        if not found.reachable:
            self._discovery_status.setText(f"{found.summary}. Nothing to register.")
            self._register_button.setEnabled(False)
            return
        if found.identity is None:
            self._discovery_status.setText(
                f"{found.summary}. It answered but could not be identified, "
                "so it cannot be registered yet."
            )
            self._register_button.setEnabled(False)
            return
        self._register_name.setText(found.suggested_name)
        self._discovery_status.setText(
            f"{found.summary}. Registering stores a new profile on this "
            "computer; the device itself is never changed."
        )
        may_manage = role_allows(self._role, Permission.MANAGE_DEVICE_SETTINGS)
        self._register_button.setEnabled(may_manage)

    def _register_discovered(self) -> None:
        pending = self._pending
        if pending is None or pending.identity is None:
            return
        name = self._register_name.text().strip()
        if not name:
            self._discovery_status.setText("Give the new device a name first.")
            return
        info = DeviceInfo(identity=pending.identity)
        self._set_busy(True)
        run_off_thread(
            lambda: self._service.register_discovered(
                name=name,
                host=pending.host,
                port=pending.port,
                info=info,
                requester_role=self._role,
            ),
            on_success=self._on_registered,
            on_failure=self._on_discovery_failure,
        )

    def _on_registered(self, profile: Any) -> None:
        self._set_busy(False)
        if not isinstance(profile, DeviceProfile):  # pragma: no cover - defensive
            return
        self._pending = None
        self._register_button.setEnabled(False)
        self._discovery_status.setText(
            f"Registered {profile.name!r}. It now appears in the device list above."
        )
        self.refresh()

    def _on_discovery_failure(self, message: str) -> None:
        self._set_busy(False)
        self._discovery_status.setText(message)

    def _on_failure(self, message: str) -> None:
        self._set_busy(False)
        self._status.setText(message)

    def _set_busy(self, busy: bool) -> None:
        may_manage = role_allows(self._role, Permission.MANAGE_DEVICE_SETTINGS)
        self._save_button.setEnabled(not busy and may_manage)
        self._test_button.setEnabled(not busy)
        self._delete_button.setEnabled(not busy and may_manage)
        self._check_button.setEnabled(not busy and may_manage)
        self._scan_button.setEnabled(not busy and may_manage)
        self._register_button.setEnabled(not busy and may_manage and self._pending is not None)
        if not busy and may_manage:
            self._delete_button.setEnabled(self._current_profile() is not None)
