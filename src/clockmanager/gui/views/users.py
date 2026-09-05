"""Users view.

Lists the users on the device and, when device writing has been enabled,
manages them.

Everything destructive goes through the same path: the application re-reads the
device, shows the operator exactly which user is affected and what the change
does to attendance history, and only then sends anything. The buttons are
disabled with an explanation when writing is switched off, rather than failing
after the operator has filled in a form.

The credential/PIN region is never displayed. The list shows only whether a
credential is present (``SECURITY.md``, ``PROTOCOL.md``).

Fingerprint enrolments are shown the same way: a count of enrolled fingers,
read by enumerating the device's fingerprint store. No template is read,
displayed, exported or stored -- the protocol parser discards template bytes
before they leave the protocol layer.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
    confirm,
    fill_table,
    muted_label,
    notify,
    page_header,
    primary_button,
    role_allows,
    run_off_thread,
    set_status,
    tint_cell,
)
from clockmanager.gui.views.user_form import UserFormDialog
from clockmanager.services.devices import DeviceProfile, DeviceService
from clockmanager.services.users import DeleteImpact, EnrolledUser, UserService

__all__ = ["UsersView"]

_logger = get_logger(__name__)

_HEADERS = (
    "UID",
    "User ID",
    "First name",
    "Last name",
    "Privilege",
    "PIN set",
    "Fingerprints",
)


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
        #: Enrolled finger count per device UID, from the last read. Empty
        #: until the device has been read; a device that will not enumerate
        #: fingerprints leaves it empty and the column says "Unknown".
        self._enrolment: dict[int, EnrolledUser] = {}

        # Names take the spare width; identifiers and flags stay narrow.
        self._table = build_table(_HEADERS, self, stretch_columns=(2, 3))
        self._table.itemSelectionChanged.connect(self._update_buttons)
        self._table.doubleClicked.connect(self._edit_user)

        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText("Search by name or user ID…  (Ctrl+F)")
        self._filter.setClearButtonEnabled(True)
        self._filter.setAccessibleName("Search users by name or user ID")
        self._filter.textChanged.connect(self._apply_filter)

        focus_search = QShortcut("Ctrl+F", self)
        focus_search.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        focus_search.activated.connect(self._filter.setFocus)

        self._privilege = QComboBox(self)
        self._privilege.addItem("Everyone", "")
        self._privilege.addItem("Employees", "Employee")
        self._privilege.addItem("Admins", "Admin")
        self._privilege.setAccessibleName("Filter by privilege")
        self._privilege.currentIndexChanged.connect(self._apply_filter)

        self._pin_only = QCheckBox("With PIN only", self)
        self._pin_only.setToolTip(
            "Show only users whose credential region is populated on the device."
        )
        self._pin_only.setAccessibleName("Show only users with a PIN set")
        self._pin_only.toggled.connect(self._apply_filter)

        self._no_credential = QCheckBox("Cannot clock in", self)
        self._no_credential.setToolTip(
            "Show only users with no PIN and no enrolled fingerprint. A face may "
            "still be enrolled: the device reports a face count but gives no way "
            "to attribute a face to a user, so this cannot account for faces."
        )
        self._no_credential.setAccessibleName("Show only users with no PIN and no fingerprint")
        self._no_credential.toggled.connect(self._apply_filter)

        self._load_button = primary_button("Read users from device", self)
        self._load_button.clicked.connect(self.load)
        self._load_button.setShortcut("F5")

        self._add_button = QPushButton("Add user…", self)
        self._add_button.clicked.connect(self._add_user)

        self._edit_button = QPushButton("Edit…", self)
        self._edit_button.clicked.connect(self._edit_user)

        self._delete_button = QPushButton("Delete…", self)
        self._delete_button.clicked.connect(self._delete_user)
        self._delete_button.setShortcut("Delete")

        self._status = QLabel("Press “Read users from device” to load.", self)
        self._status.setWordWrap(True)
        set_status(self._status, "Press “Read users from device” to load.", "info")

        self._write_notice = muted_label("", self)

        controls = QHBoxLayout()
        controls.addWidget(self._load_button)
        controls.addWidget(self._filter, stretch=1)
        controls.addWidget(self._privilege)
        controls.addWidget(self._pin_only)
        controls.addWidget(self._no_credential)

        actions = QHBoxLayout()
        actions.addWidget(self._add_button)
        actions.addWidget(self._edit_button)
        actions.addStretch(1)
        actions.addWidget(self._delete_button)

        layout = QVBoxLayout()
        layout.addWidget(
            page_header(
                "Users",
                "The people enrolled on the clock, their privilege, and how each "
                "of them can identify themselves.",
            )
        )
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
        set_status(self._status, "Reading users…", "loading")
        run_off_thread(
            self._read_users,
            on_success=self._on_users_loaded,
            on_failure=self._on_failure,
        )

    def _read_users(self) -> list[EnrolledUser] | None:
        profile = self._profile()
        if profile is None:
            return None
        return self._users_service.list_enrolment(profile)

    def _on_users_loaded(self, users: Any) -> None:
        self._load_button.setEnabled(True)
        if users is None:
            set_status(
                self._status,
                "No device is configured. Add one in Device settings.",
                "warning",
            )
            self._users = []
            self._enrolment = {}
            fill_table(
                self._table,
                [],
                empty_message="No device configured. Open Device settings to add one.",
            )
            self._update_buttons()
            return
        if not isinstance(users, list):  # pragma: no cover - defensive
            return

        self._enrolment = {entry.user.device_uid: entry for entry in users}
        self._users = [entry.user for entry in users]
        self._apply_filter()
        self._refresh_write_availability()

    def _apply_filter(self) -> None:
        needle = self._filter.text().strip().lower()
        privilege = str(self._privilege.currentData() or "")
        pin_only = self._pin_only.isChecked()
        no_credential = self._no_credential.isChecked()
        self._visible = [
            user
            for user in self._users
            if (not privilege or user.privilege_label == privilege)
            and (not pin_only or user.has_credential_data)
            and (not no_credential or not self._can_identify(user))
            and (
                not needle or needle in user.user_id.lower() or needle in user.display_name.lower()
            )
        ]
        if not self._users:
            set_status(self._status, "Press “Read users from device” to load.", "info")
        else:
            admins = sum(1 for user in self._users if user.is_admin)
            summary = (
                f"{len(self._users)} user(s) on the device, {admins} with Admin privilege"
                f"{self._enrolment_summary()}. “PIN set” indicates the device's "
                "credential region is populated; neither a PIN nor a fingerprint "
                "template is ever read."
            )
            if len(self._visible) != len(self._users):
                summary += f" Showing {len(self._visible)} matching."
            elif not self._visible:
                summary = "No users match the current search. Clear the search to see everyone."
            set_status(self._status, summary, "info")
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
                    self._fingerprint_label(user),
                ]
                for user in self._visible
            ],
            empty_message=(
                "No users loaded yet. Press “Read users from device”."
                if not self._users
                else "No users match these filters. Clear the search or change the privilege filter."
            ),
        )
        self._style_rows()
        self._update_buttons()

    # -- enrolment ------------------------------------------------------------

    def _fingerprint_label(self, user: DeviceUser) -> str:
        """How many fingers this user has enrolled, or that it is not known.

        "Unknown" and "None" are deliberately different words. A device that
        would not enumerate its fingerprint store has told us nothing, and
        showing that as "None" would say every user is unenrolled.
        """
        entry = self._enrolment.get(user.device_uid)
        return entry.fingerprint_label if entry is not None else "Unknown"

    def _can_identify(self, user: DeviceUser) -> bool:
        entry = self._enrolment.get(user.device_uid)
        return entry.can_identify if entry is not None else user.has_credential_data

    def _enrolment_summary(self) -> str:
        """A clause about fingerprint enrolment, or nothing when it is unknown."""
        known = [entry for entry in self._enrolment.values() if entry.fingerprints_known]
        if len(known) != len(self._users) or not self._users:
            return ""
        with_finger = sum(1 for entry in known if entry.fingerprint_count)
        stranded = sum(1 for entry in known if not entry.can_identify)
        clause = f", {with_finger} with a fingerprint enrolled"
        if stranded:
            clause += f", {stranded} with neither a PIN nor a fingerprint"
        return clause

    def _style_rows(self) -> None:
        """Tint privilege and enrolment cells so admins and PIN state scan easily.

        Colours come from the active theme, never from a literal, so the
        indicators stay legible in dark mode.
        """
        for row in range(self._table.rowCount()):
            privilege = self._table.item(row, 4)
            if privilege is not None and privilege.text() == "Admin":
                tint_cell(self._table, row, 4, "accent")
            enrolled = self._table.item(row, 5)
            if enrolled is not None and enrolled.text() == "Yes":
                tint_cell(self._table, row, 5, "success")
            fingers = self._table.item(row, 6)
            if fingers is not None and fingers.text() not in ("None", "Unknown", ""):
                tint_cell(self._table, row, 6, "success")

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
        set_status(self._status, "Writing to the device and verifying…", "loading")
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
            set_status(self._status, "Nothing to change.", "info")
            return False

        body = "\n".join(f"• {change}" for change in changes)
        return confirm(
            self,
            "Write to the device",
            f"Write these changes to the clock?\n\n{body}",
            detail="The record is read back and compared after writing.",
            confirm_label="Write to device",
            destructive=True,
        )

    def _on_saved(self, outcome: Any) -> None:
        self._set_busy(False)
        if not isinstance(outcome, UserWriteOutcome):  # pragma: no cover - defensive
            return
        set_status(self._status, outcome.summary, "success")
        notify(self, outcome.summary)
        self.load()

    def _delete_user(self) -> None:
        user = self._selected_user()
        profile = self._profile()
        if user is None or profile is None:
            return

        self._set_busy(True)
        set_status(self._status, "Checking what deleting this user would affect…", "loading")
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
        if not confirm(
            self,
            "Delete user",
            impact.warning,
            confirm_label="Delete user",
            destructive=True,
        ):
            set_status(self._status, "Deletion cancelled. Nothing was sent to the device.", "info")
            return

        device_uid = impact.user.device_uid
        self._set_busy(True)
        set_status(self._status, "Deleting and verifying…", "loading")
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
            set_status(
                self._status,
                f"Deleted {deleted.display_name} (user ID {deleted.user_id}). "
                "Attendance history on the device was not removed.",
                "success",
            )
            notify(self, f"Deleted {deleted.display_name}.")
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
        set_status(self._status, message, "error")
