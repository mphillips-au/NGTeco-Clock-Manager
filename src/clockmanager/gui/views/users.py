"""Users view.

Lists the users on the device and, when device writing has been enabled,
manages them.

Everything destructive goes through the same path: the application re-reads the
device, shows the operator exactly which user is affected and what the change
does to attendance history, and only then sends anything. The buttons are
disabled with an explanation when writing is switched off, rather than failing
after the operator has filled in a form.

The credential/PIN region is never displayed. The list shows only whether a
credential is present, and labels it as an unverified indicator
(``SECURITY.md``, ``PROTOCOL.md``).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.auth import Permission, Role, normalise_role
from clockmanager.domain.models import DeviceUser
from clockmanager.domain.users import UserDraft, UserWriteOutcome
from clockmanager.gui.views.common import (
    build_table,
    fill_table,
    role_allows,
    run_off_thread,
    section_label,
)
from clockmanager.gui.views.user_form import UserFormDialog
from clockmanager.services.devices import DeviceProfile, DeviceService
from clockmanager.services.users import DeleteImpact, UserService

__all__ = ["UsersView"]

_logger = get_logger(__name__)

_HEADERS = ("UID", "User ID", "First name", "Last name", "Privilege", "PIN set")


class UsersView(QWidget):
    """Displays and manages the users stored on the device."""

    def __init__(
        self,
        service: DeviceService,
        users: UserService,
        parent: QWidget | None = None,
        *,
        role: Role | str | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._users_service = users
        #: The logged-in role. ``None`` is the pre-login/test path and keeps
        #: the legacy behaviour; the service layer refuses regardless.
        self._role = normalise_role(role) if role is not None else None
        self._users: list[DeviceUser] = []
        self._visible: list[DeviceUser] = []

        self._table = build_table(_HEADERS, self)
        self._table.itemSelectionChanged.connect(self._update_buttons)
        self._table.doubleClicked.connect(self._edit_user)

        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText("Search by name or user ID…")
        self._filter.textChanged.connect(self._apply_filter)

        self._admins_only = QCheckBox("Admins only", self)
        self._admins_only.toggled.connect(self._apply_filter)

        self._load_button = QPushButton("Read users from device", self)
        self._load_button.clicked.connect(self.load)

        self._add_button = QPushButton("Add user…", self)
        self._add_button.clicked.connect(self._add_user)

        self._edit_button = QPushButton("Edit…", self)
        self._edit_button.clicked.connect(self._edit_user)

        self._delete_button = QPushButton("Delete…", self)
        self._delete_button.clicked.connect(self._delete_user)

        self._status = QLabel("Press “Read users from device” to load.", self)
        self._status.setWordWrap(True)

        self._write_notice = QLabel("", self)
        self._write_notice.setWordWrap(True)

        controls = QHBoxLayout()
        controls.addWidget(self._load_button)
        controls.addWidget(self._filter, stretch=1)
        controls.addWidget(self._admins_only)

        actions = QHBoxLayout()
        actions.addWidget(self._add_button)
        actions.addWidget(self._edit_button)
        actions.addStretch(1)
        actions.addWidget(self._delete_button)

        layout = QVBoxLayout()
        layout.addWidget(section_label("Users on device", self))
        layout.addLayout(controls)
        layout.addWidget(self._status)
        layout.addWidget(self._table, stretch=1)
        layout.addLayout(actions)
        layout.addWidget(self._write_notice)
        self.setLayout(layout)

        self._refresh_write_availability()
        self._update_buttons()

    # -- state ----------------------------------------------------------------

    def _refresh_write_availability(self) -> None:
        if not role_allows(self._role, Permission.MANAGE_DEVICE_USERS):
            label = self._role.label if self._role is not None else "this role"
            self._write_notice.setText(
                f"Your role ({label}) cannot change device users. "
                "Only administrators may add, edit or delete them."
            )
            return
        availability = self._users_service.write_availability()
        self._write_notice.setText(
            availability.reason
            if availability.reason
            else "Device writing is enabled. Changes are verified by reading them back."
        )

    def _profile(self) -> DeviceProfile | None:
        profile = self._service.first_enabled_profile()
        if profile is None or not profile.is_configured:
            return None
        return profile

    def _selected_user(self) -> DeviceUser | None:
        rows = {index.row() for index in self._table.selectionModel().selectedRows()}
        if len(rows) != 1:
            return None
        row = rows.pop()
        # The table sorts, so map back through the UID column rather than the
        # row index, which no longer matches the underlying list order.
        item = self._table.item(row, 0)
        if item is None:
            return None
        try:
            uid = int(item.text())
        except ValueError:  # pragma: no cover - defensive
            return None
        return next((user for user in self._users if user.device_uid == uid), None)

    def _update_buttons(self) -> None:
        availability = self._users_service.write_availability()
        permitted = role_allows(self._role, Permission.MANAGE_DEVICE_USERS)
        has_selection = self._selected_user() is not None
        self._add_button.setEnabled(availability.users and permitted)
        self._edit_button.setEnabled(availability.users and permitted and has_selection)
        self._delete_button.setEnabled(availability.users and permitted and has_selection)

    # -- reads ----------------------------------------------------------------

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
        profile = self._profile()
        if profile is None:
            return None
        return self._users_service.list_users(profile)

    def _on_users_loaded(self, users: Any) -> None:
        self._load_button.setEnabled(True)
        if users is None:
            self._status.setText("No device is configured. Add one in Device settings.")
            self._users = []
            fill_table(self._table, [])
            self._update_buttons()
            return
        if not isinstance(users, list):  # pragma: no cover - defensive
            return

        self._users = users
        admins = sum(1 for user in users if user.is_admin)
        self._status.setText(
            f"{len(users)} user(s) on the device, {admins} with Admin privilege. "
            "“PIN set” indicates the device's credential region is populated; "
            "its contents are never read."
        )
        self._apply_filter()
        self._refresh_write_availability()

    def _apply_filter(self) -> None:
        needle = self._filter.text().strip().lower()
        admins_only = self._admins_only.isChecked()
        self._visible = [
            user
            for user in self._users
            if (not admins_only or user.is_admin)
            and (
                not needle or needle in user.user_id.lower() or needle in user.display_name.lower()
            )
        ]
        fill_table(
            self._table,
            [
                [
                    str(user.device_uid),
                    user.user_id,
                    user.first_name,
                    user.last_name,
                    user.privilege_label,
                    "Yes" if user.has_credential_data else "No",
                ]
                for user in self._visible
            ],
        )
        self._update_buttons()

    # -- writes ---------------------------------------------------------------

    def _add_user(self) -> None:
        self._open_form(None)

    def _edit_user(self) -> None:
        user = self._selected_user()
        if user is not None:
            self._open_form(user)

    def _open_form(self, user: DeviceUser | None) -> None:
        profile = self._profile()
        if profile is None:
            self._status.setText("No device is configured. Add one in Device settings.")
            return

        dialog = UserFormDialog(self._users_service.write_availability(), user=user, parent=self)
        if dialog.exec() != UserFormDialog.DialogCode.Accepted:
            return

        draft = dialog.draft()
        if not self._confirm_save(draft, user):
            return

        self._set_busy(True)
        self._status.setText("Writing to the device and verifying…")
        run_off_thread(
            lambda: self._users_service.save_user(profile, draft, requester_role=self._role),
            on_success=self._on_saved,
            on_failure=self._on_failure,
        )

    def _confirm_save(self, draft: UserDraft, user: DeviceUser | None) -> bool:
        """Show what will change and require a deliberate confirmation."""
        from clockmanager.domain.users import describe_changes

        changes = describe_changes(user, draft)
        if not changes:
            self._status.setText("Nothing to change.")
            return False

        body = "\n".join(f"• {change}" for change in changes)
        answer = QMessageBox.question(
            self,
            "Write to the device",
            f"Write these changes to the clock?\n\n{body}\n\n"
            "The record is read back and compared after writing.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_saved(self, outcome: Any) -> None:
        self._set_busy(False)
        if not isinstance(outcome, UserWriteOutcome):  # pragma: no cover - defensive
            return
        self._status.setText(outcome.summary)
        self.load()

    def _delete_user(self) -> None:
        user = self._selected_user()
        profile = self._profile()
        if user is None or profile is None:
            return

        self._set_busy(True)
        self._status.setText("Checking what deleting this user would affect…")
        device_uid = user.device_uid
        run_off_thread(
            lambda: self._users_service.describe_delete(profile, device_uid),
            on_success=self._on_delete_impact,
            on_failure=self._on_failure,
        )

    def _on_delete_impact(self, impact: Any) -> None:
        self._set_busy(False)
        if not isinstance(impact, DeleteImpact):  # pragma: no cover - defensive
            return

        profile = self._profile()
        if profile is None:  # pragma: no cover - defensive
            return

        # The impact was read live, so the operator confirms against the
        # device's current state rather than the possibly stale table.
        answer = QMessageBox.warning(
            self,
            "Delete user",
            impact.warning,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._status.setText("Deletion cancelled. Nothing was sent to the device.")
            return

        device_uid = impact.user.device_uid
        self._set_busy(True)
        self._status.setText("Deleting and verifying…")
        run_off_thread(
            lambda: self._users_service.delete_user(
                profile, device_uid, confirmed=True, requester_role=self._role
            ),
            on_success=self._on_deleted,
            on_failure=self._on_failure,
        )

    def _on_deleted(self, deleted: Any) -> None:
        self._set_busy(False)
        if isinstance(deleted, DeviceUser):
            self._status.setText(
                f"Deleted {deleted.display_name} (user ID {deleted.user_id}). "
                "Attendance history on the device was not removed."
            )
        self.load()

    # -- shared ---------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        for widget in (
            self._load_button,
            self._add_button,
            self._edit_button,
            self._delete_button,
        ):
            widget.setEnabled(not busy)
        if not busy:
            self._update_buttons()

    def _on_failure(self, message: str) -> None:
        self._set_busy(False)
        self._load_button.setEnabled(True)
        self._status.setText(message)
