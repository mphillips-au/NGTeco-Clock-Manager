"""Device settings view.

Edits the locally stored device profile. Saving writes to the application
database only — nothing on this screen changes anything on the clock itself.

The "Device information" tab reads the clock and shows what it says about
itself: its settings, how full it is, which users have a fingerprint enrolled
and its own log of keypad activity (PHASE 15). Every one of those is a read.
There is deliberately no way to write a device setting from here: writing an
option has never been exercised on this hardware, and a wrong IP address or
matching threshold cannot be undone over the protocol.

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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.auth import Permission, Role, normalise_role
from clockmanager.domain.models import DeviceInfo
from clockmanager.gui.views.common import (
    build_table,
    fill_table,
    muted_label,
    page_header,
    primary_button,
    role_allows,
    run_off_thread,
    set_status,
)
from clockmanager.services.devices import (
    DEFAULT_DEVICE_PORT,
    ConnectionTestResult,
    DeviceInspection,
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
        # A form is read down its left edge; stretching seven fields across a
        # wide monitor makes it harder to read, not easier.
        group.setMaximumWidth(620)

        self._save_button = primary_button("Save", self)
        self._save_button.clicked.connect(self._save)
        self._test_button = QPushButton("Test connection", self)
        self._test_button.clicked.connect(self._test_connection)
        self._delete_button = QPushButton("Remove device", self)
        self._delete_button.setObjectName("Danger")
        self._delete_button.clicked.connect(self._delete)

        buttons = QHBoxLayout()
        buttons.addWidget(self._save_button)
        buttons.addWidget(self._test_button)
        buttons.addStretch(1)
        buttons.addWidget(self._delete_button)

        self._status = QLabel("", self)
        self._status.setWordWrap(True)

        # Deliberately says only what this screen does. Whether the build can
        # write device *users* is a separate setting, reported on the Users
        # screen and in Help ▸ About; claiming "never writes" here was wrong
        # whenever device writing was enabled.
        notice = muted_label(
            "Saving stores these settings on this computer. It does not reconfigure "
            "the clock or change anything stored on it.",
            self,
        )

        may_manage = role_allows(self._role, Permission.MANAGE_DEVICE_SETTINGS)

        self._state_label = QLabel("", self)
        self._state_label.setWordWrap(True)

        # -- Device information (PHASE 15). Read-only, and gathered in one
        # connection so the tab costs the device a single session.
        self._info_status = QLabel("", self)
        self._info_status.setWordWrap(True)
        self._read_info_button = primary_button("Read information from device", self)
        self._read_info_button.clicked.connect(self._read_device_information)
        self._capacity_table = build_table(
            ("Store", "Usage"), self, sortable=False, stretch_columns=(1,)
        )
        self._settings_table = build_table(
            ("Group", "Setting", "Value", "Note"), self, sortable=False, stretch_columns=(3,)
        )
        self._enrolment_table = build_table(
            ("Device UID", "Finger", "State", "Template size"), self, stretch_columns=(2,)
        )
        self._oplog_table = build_table(("When", "Operation", "Detail"), self, stretch_columns=(2,))

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
            self._save_button.setEnabled(False)
            self._delete_button.setEnabled(False)

        layout = QVBoxLayout()
        layout.addWidget(
            page_header(
                "Device settings",
                "How this computer reaches the clock. Saving stores the settings "
                "locally; it never reconfigures the device.",
            )
        )
        tabs = QTabWidget(self)
        tabs.setAccessibleName("Device settings sections")

        connection_tab = QWidget(self)
        connection_layout = QVBoxLayout()
        connection_layout.addWidget(group)
        # The action row and its explanation keep the form's width so the
        # column reads as one block rather than trailing off to the right.
        action_row = QWidget(self)
        action_row.setMaximumWidth(620)
        action_row.setLayout(buttons)
        connection_layout.addWidget(action_row)
        self._status.setMaximumWidth(620)
        notice.setMaximumWidth(620)
        connection_layout.addWidget(self._status)
        connection_layout.addWidget(notice)
        locked: QLabel | None = None
        if not may_manage:
            label = self._role.label if self._role is not None else "this role"
            locked = QLabel(
                f"Your role ({label}) cannot change device settings. "
                "Only administrators may save or remove a device.",
                self,
            )
            locked.setWordWrap(True)
            connection_layout.addWidget(locked)
            self._save_button.setEnabled(False)
            self._delete_button.setEnabled(False)
        connection_layout.addStretch(1)
        connection_tab.setLayout(connection_layout)
        tabs.addTab(connection_tab, "Connection")

        state_tab = QWidget(self)
        state_layout = QVBoxLayout()
        self._reload_state_button = QPushButton("Reload device state", self)
        self._reload_state_button.clicked.connect(self._reload_state)
        state_layout.addWidget(self._state_label)
        state_layout.addWidget(self._reload_state_button)
        state_note = QLabel(
            "Last contact, stored counts and recent sync history for the selected "
            "device. Reads local storage only; the clock is never contacted here.",
            self,
        )
        state_note.setWordWrap(True)
        state_layout.addWidget(state_note)
        state_layout.addStretch(1)
        state_tab.setLayout(state_layout)
        tabs.addTab(state_tab, "Device state")

        tabs.addTab(self._build_information_tab(), "Device information")

        discovery_tab = QWidget(self)
        discovery_layout = QVBoxLayout()
        discovery_layout.addWidget(discovery_group)
        discovery_tab.setLayout(discovery_layout)
        tabs.addTab(discovery_tab, "Discovery")

        layout.addWidget(tabs, stretch=1)
        self.setLayout(layout)

        self.refresh()

    # -- device information (PHASE 15, read-only) -------------------------------

    def _build_information_tab(self) -> QWidget:
        """What the clock says about itself. Reads only, in one connection."""
        tab = QWidget(self)
        layout = QVBoxLayout()

        header = muted_label(
            "Everything on this tab is read from the clock and nothing is written "
            "back. Settings cannot be changed from here: writing a device setting "
            "has never been proven on this model, and a wrong value for an address "
            "or a matching threshold cannot be undone remotely.",
            self,
        )
        layout.addWidget(header)

        actions = QHBoxLayout()
        actions.addWidget(self._read_info_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addWidget(self._info_status)

        sections = QTabWidget(self)
        sections.setAccessibleName("Device information sections")

        capacity_tab = QWidget(self)
        capacity_layout = QVBoxLayout()
        capacity_layout.addWidget(self._capacity_table, stretch=1)
        capacity_layout.addWidget(
            muted_label(
                "How full the clock is, as it reports itself. The device also "
                "reports a card counter; it is not shown, because nothing "
                "establishes what it counts.",
                self,
            )
        )
        capacity_tab.setLayout(capacity_layout)
        sections.addTab(capacity_tab, "Capacity")

        settings_tab = QWidget(self)
        settings_layout = QVBoxLayout()
        settings_layout.addWidget(self._settings_table, stretch=1)
        settings_layout.addWidget(
            muted_label(
                "Settings the clock will answer for. A setting shown as not "
                "available is one this model's firmware does not have, which is a "
                "normal answer and not a fault.",
                self,
            )
        )
        settings_tab.setLayout(settings_layout)
        sections.addTab(settings_tab, "Settings")

        enrolment_tab = QWidget(self)
        enrolment_layout = QVBoxLayout()
        enrolment_layout.addWidget(self._enrolment_table, stretch=1)
        enrolment_layout.addWidget(
            muted_label(
                "Which fingerprints the clock holds, listed by the device UID they "
                "belong to. The fingerprints themselves are never read: only their "
                "size in bytes is reported, and no template leaves the device.",
                self,
            )
        )
        enrolment_tab.setLayout(enrolment_layout)
        sections.addTab(enrolment_tab, "Fingerprints")

        oplog_tab = QWidget(self)
        oplog_layout = QVBoxLayout()
        oplog_layout.addWidget(self._oplog_table, stretch=1)
        oplog_layout.addWidget(
            muted_label(
                "The clock's own record of what was done at its keypad — a "
                "different question from this application's audit trail, which "
                "records only what this application did. Operations are shown as "
                "numbers because their meanings are not established for this model.",
                self,
            )
        )
        oplog_tab.setLayout(oplog_layout)
        sections.addTab(oplog_tab, "Device log")

        layout.addWidget(sections, stretch=1)
        tab.setLayout(layout)
        return tab

    def _read_device_information(self) -> None:
        """Read the selected clock in one connection, off the UI thread."""
        profile = self._current_profile()
        if profile is None:
            set_status(
                self._info_status,
                "Save this device first, then read its information.",
                "warning",
            )
            return
        self._read_info_button.setEnabled(False)
        set_status(self._info_status, f"Reading {profile.endpoint}…", "loading")
        run_off_thread(
            lambda: self._service.inspect(profile),
            on_success=self._on_information_read,
            on_failure=self._on_information_failure,
        )

    def _on_information_read(self, inspection: Any) -> None:
        self._read_info_button.setEnabled(True)
        if not isinstance(inspection, DeviceInspection):  # pragma: no cover - defensive
            return
        if not inspection.ok:
            set_status(self._info_status, inspection.summary, "error")
            self._clear_information_tables("The device could not be read.")
            return

        summary = inspection.summary
        if inspection.notes:
            summary += " " + " ".join(inspection.notes)
        set_status(self._info_status, summary, "warning" if inspection.notes else "success")

        identity_rows = [] if inspection.info is None else inspection.info.as_rows()
        fill_table(
            self._capacity_table,
            [[label, value] for label, value in identity_rows],
            empty_message="The device did not report its capacities.",
        )
        fill_table(
            self._settings_table,
            [list(row) for row in inspection.option_rows()],
            empty_message="This device did not answer any settings read.",
        )
        fill_table(
            self._enrolment_table,
            [
                [
                    str(slot.device_uid),
                    str(slot.finger_index),
                    "Valid" if slot.is_valid else "Invalid",
                    f"{slot.template_bytes} bytes (not read)",
                ]
                for slot in inspection.fingerprints
            ],
            empty_message="No fingerprints are enrolled on this device.",
        )
        fill_table(
            self._oplog_table,
            [
                [
                    entry.occurred_label,
                    entry.operation_label,
                    f"operator UID {entry.operator_uid}, parameters {entry.parameters}",
                ]
                for entry in reversed(inspection.operation_log)
            ],
            empty_message="The device reported no keypad activity.",
        )

    def _clear_information_tables(self, message: str) -> None:
        for table in (
            self._capacity_table,
            self._settings_table,
            self._enrolment_table,
            self._oplog_table,
        ):
            fill_table(table, [], empty_message=message)

    def _on_information_failure(self, message: str) -> None:
        self._read_info_button.setEnabled(True)
        set_status(self._info_status, f"Could not read the device: {message}", "error")

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
            set_status(self._status, "Communication password must be a number.", "error")
            return

        profile = self._form_profile()
        problems = profile.validate()
        if problems:
            set_status(self._status, " ".join(problems), "error")
            return

        self._set_busy(True)
        set_status(self._status, "Saving…", "loading")
        run_off_thread(
            lambda: self._service.save_profile(profile, requester_role=self._role),
            on_success=self._on_saved,
            on_failure=self._on_failure,
        )

    def _on_saved(self, _profile: Any) -> None:
        self._set_busy(False)
        set_status(self._status, "Settings saved on this computer.", "success")
        self.refresh()

    def _reload_state(self) -> None:
        """Reload the Device state tab for the selected profile."""
        profile = self._current_profile()
        if profile is None:
            set_status(self._state_label, "Add a device first, then review its state.", "info")
            return
        self._load_state(profile)

    def _load_state(self, profile: DeviceProfile) -> None:
        """Show last-seen and sync state for the selected profile, off-thread."""
        set_status(self._state_label, "Loading device state…", "loading")
        run_off_thread(
            lambda: self._service.status(profile),
            on_success=self._on_state_loaded,
            on_failure=self._on_state_failure,
        )

    def _on_state_loaded(self, status: Any) -> None:
        if not isinstance(status, DeviceStatus):  # pragma: no cover - defensive
            return
        set_status(self._state_label, status.describe(), "info")

    def _on_state_failure(self, message: str) -> None:
        set_status(self._state_label, f"Could not load device state: {message}", "error")

    def _test_connection(self) -> None:
        profile = self._form_profile()
        problems = profile.validate()
        if problems:
            set_status(self._status, " ".join(problems), "error")
            return

        self._set_busy(True)
        set_status(self._status, f"Connecting to {profile.endpoint}…", "loading")
        run_off_thread(
            lambda: self._service.test_connection(profile),
            on_success=self._on_tested,
            on_failure=self._on_failure,
        )

    def _on_tested(self, result: Any) -> None:
        self._set_busy(False)
        if not isinstance(result, ConnectionTestResult):  # pragma: no cover - defensive
            return
        set_status(self._status, result.summary, "success" if result.ok else "error")

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
        set_status(self._status, "Device removed from this computer.", "success")
        self.refresh()

    # -- discovery (PHASE 08, read-only) ----------------------------------------

    def _check_address(self) -> None:
        host = self._discover_host.text().strip()
        port = self._discover_port.value()
        if not host:
            set_status(self._discovery_status, "Enter an address to check.", "warning")
            return
        self._set_busy(True)
        set_status(self._discovery_status, f"Identifying {host}:{port}…", "loading")
        run_off_thread(
            lambda: self._service.identify(host, port=port),
            on_success=self._on_identified,
            on_failure=self._on_discovery_failure,
        )

    def _scan_network(self) -> None:
        self._set_busy(True)
        set_status(self._discovery_status, "Scanning the local network…", "loading")
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
            set_status(
                self._discovery_status,
                "No addresses to scan: the local network could not be determined. "
                "Check a manually entered address instead.",
                "warning",
            )
        elif not reachable:
            set_status(
                self._discovery_status,
                f"Scanned {len(self._found)} address(es): no device answered. "
                "Check a manually entered address instead.",
                "warning",
            )
        else:
            set_status(
                self._discovery_status,
                f"Scanned {len(self._found)} address(es): "
                f"{len(reachable)} answered. Select one to register it.",
                "success",
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
            set_status(self._discovery_status, f"{found.summary}. Nothing to register.", "warning")
            self._register_button.setEnabled(False)
            return
        if found.identity is None:
            set_status(
                self._discovery_status,
                f"{found.summary}. It answered but could not be identified, "
                "so it cannot be registered yet.",
                "warning",
            )
            self._register_button.setEnabled(False)
            return
        self._register_name.setText(found.suggested_name)
        set_status(
            self._discovery_status,
            f"{found.summary}. Registering stores a new profile on this "
            "computer; the device itself is never changed.",
            "info",
        )
        may_manage = role_allows(self._role, Permission.MANAGE_DEVICE_SETTINGS)
        self._register_button.setEnabled(may_manage)

    def _register_discovered(self) -> None:
        pending = self._pending
        if pending is None or pending.identity is None:
            return
        name = self._register_name.text().strip()
        if not name:
            set_status(self._discovery_status, "Give the new device a name first.", "warning")
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
        set_status(
            self._discovery_status,
            f"Registered {profile.name!r}. It now appears in the device list above.",
            "success",
        )
        self.refresh()

    def _on_discovery_failure(self, message: str) -> None:
        self._set_busy(False)
        set_status(self._discovery_status, message, "error")

    def _on_failure(self, message: str) -> None:
        self._set_busy(False)
        set_status(self._status, message, "error")

    def _set_busy(self, busy: bool) -> None:
        may_manage = role_allows(self._role, Permission.MANAGE_DEVICE_SETTINGS)
        self._save_button.setEnabled(not busy and may_manage)
        self._test_button.setEnabled(not busy)
        self._delete_button.setEnabled(not busy and may_manage)
        self._reload_state_button.setEnabled(not busy)
        self._check_button.setEnabled(not busy and may_manage)
        self._scan_button.setEnabled(not busy and may_manage)
        self._register_button.setEnabled(not busy and may_manage and self._pending is not None)
        if not busy and may_manage:
            self._delete_button.setEnabled(self._current_profile() is not None)
