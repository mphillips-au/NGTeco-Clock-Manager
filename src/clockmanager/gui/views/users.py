"""Users list view.

Read-only. PHASE 03 adds user management; this build only displays what the
device reports.

The credential/PIN region is never displayed. The list shows only whether a
credential is present, and labels it as an unverified indicator
(``SECURITY.md``, ``PROTOCOL.md``).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clockmanager.domain.models import DeviceUser
from clockmanager.gui.views.common import build_table, fill_table, run_off_thread, section_label
from clockmanager.services.devices import DeviceProfile, DeviceService

__all__ = ["UsersView"]

_HEADERS = ("UID", "User ID", "First name", "Last name", "Privilege", "Credential set")


class UsersView(QWidget):
    """Displays the users stored on the device."""

    def __init__(self, service: DeviceService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._users: list[DeviceUser] = []

        self._table = build_table(_HEADERS, self)

        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText("Filter by name or user ID…")
        self._filter.textChanged.connect(self._apply_filter)

        self._load_button = QPushButton("Read users from device", self)
        self._load_button.clicked.connect(self.load)

        self._status = QLabel("Press “Read users from device” to load.", self)
        self._status.setWordWrap(True)

        controls = QHBoxLayout()
        controls.addWidget(self._load_button)
        controls.addWidget(self._filter, stretch=1)

        layout = QVBoxLayout()
        layout.addWidget(section_label("Users on device", self))
        layout.addLayout(controls)
        layout.addWidget(self._status)
        layout.addWidget(self._table, stretch=1)
        self.setLayout(layout)

    def load(self) -> None:
        """Read the device user list off the UI thread."""
        self._load_button.setEnabled(False)
        self._status.setText("Reading users…")
        run_off_thread(
            self._read_users,
            on_success=self._on_users_loaded,
            on_failure=self._on_failure,
        )

    def _read_users(self) -> list[DeviceUser] | None:
        profile: DeviceProfile | None = self._service.first_enabled_profile()
        if profile is None or not profile.is_configured:
            return None
        return self._service.read_users(profile)

    def _on_users_loaded(self, users: Any) -> None:
        self._load_button.setEnabled(True)
        if users is None:
            self._status.setText("No device is configured. Add one in Device settings.")
            fill_table(self._table, [])
            return
        if not isinstance(users, list):  # pragma: no cover - defensive
            return

        self._users = users
        admins = sum(1 for user in users if user.is_admin)
        self._status.setText(
            f"{len(users)} user(s) on the device, {admins} with Admin privilege. "
            "“Credential set” indicates the device's credential region is populated; "
            "its contents are never read."
        )
        self._apply_filter()

    def _apply_filter(self) -> None:
        needle = self._filter.text().strip().lower()
        rows = [
            [
                str(user.device_uid),
                user.user_id,
                user.first_name,
                user.last_name,
                user.privilege_label,
                "Yes" if user.has_credential_data else "No",
            ]
            for user in self._users
            if not needle or needle in user.user_id.lower() or needle in user.display_name.lower()
        ]
        fill_table(self._table, rows)

    def _on_failure(self, message: str) -> None:
        self._load_button.setEnabled(True)
        self._status.setText(message)
