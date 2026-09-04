"""Add/edit user dialog.

Collects a :class:`~clockmanager.domain.users.UserDraft` and validates it with
the domain rules before the dialog will close, so an invalid draft never
reaches the device layer. The widget holds no business rules of its own.

The PIN field is masked, is never populated from the device, and its value
lives only inside the returned draft (``SECURITY.md``). When PIN writing is not
unlocked the field is disabled and says why, rather than silently failing on
save.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from clockmanager.domain.models import DeviceUser, Privilege
from clockmanager.domain.users import (
    FIRST_NAME_MAX_BYTES,
    LAST_NAME_MAX_BYTES,
    PASSWORD_MAX_BYTES,
    USER_ID_MAX_BYTES,
    CredentialAction,
    UserDraft,
)
from clockmanager.services.users import WriteAvailability

__all__ = ["UserFormDialog"]


class UserFormDialog(QDialog):
    """Collects the fields of one MB1 user record."""

    def __init__(
        self,
        availability: WriteAvailability,
        *,
        user: DeviceUser | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._user = user
        self._availability = availability
        self.setWindowTitle("Add user" if user is None else f"Edit {user.display_name}")
        self.setModal(True)

        self._user_id = QLineEdit(self)
        self._user_id.setMaxLength(USER_ID_MAX_BYTES)
        self._user_id.setPlaceholderText("EMP-001")

        self._first_name = QLineEdit(self)
        self._first_name.setMaxLength(FIRST_NAME_MAX_BYTES)

        self._last_name = QLineEdit(self)
        self._last_name.setMaxLength(LAST_NAME_MAX_BYTES)

        self._privilege = QComboBox(self)
        # Only the privilege values verified on the real device are offered.
        self._privilege.addItem("Employee", int(Privilege.EMPLOYEE))
        self._privilege.addItem("Admin", int(Privilege.ADMIN))

        self._credential_action = QComboBox(self)
        for action in CredentialAction:
            self._credential_action.addItem(action.label, action.value)
        self._credential_action.currentIndexChanged.connect(self._on_credential_action_changed)

        self._password = QLineEdit(self)
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setMaxLength(PASSWORD_MAX_BYTES)
        self._password.setPlaceholderText("Never shown once stored on the device")
        self._password.setEnabled(False)

        form = QFormLayout()
        form.addRow("User ID", self._user_id)
        form.addRow("First name", self._first_name)
        form.addRow("Last name", self._last_name)
        form.addRow("Privilege", self._privilege)
        form.addRow("PIN", self._credential_action)
        form.addRow("New PIN", self._password)

        self._message = QLabel("", self)
        self._message.setWordWrap(True)

        self._notice = QLabel(self._notice_text(), self)
        self._notice.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self._message)
        layout.addWidget(self._notice)
        layout.addWidget(buttons)
        self.setLayout(layout)

        if not availability.credentials:
            self._credential_action.setEnabled(False)

        if user is not None:
            self._populate(user)

    # -- data -----------------------------------------------------------------

    def _notice_text(self) -> str:
        if not self._availability.credentials:
            return (
                "PIN changes are unavailable: "
                + self._availability.reason
                + " Names and privilege can still be saved."
            )
        return (
            "The device's PIN is never displayed. Leaving the PIN action on "
            "“Leave the PIN unchanged” preserves whatever is already stored."
        )

    def _populate(self, user: DeviceUser) -> None:
        self._user_id.setText(user.user_id)
        self._first_name.setText(user.first_name)
        self._last_name.setText(user.last_name)
        index = self._privilege.findData(user.privilege)
        if index >= 0:
            self._privilege.setCurrentIndex(index)
        else:
            # The device holds a privilege this application will not write.
            # Show it, and make the operator choose a verified value.
            self._message.setText(
                f"This user has privilege {user.privilege}, which is not one of the "
                "values verified for this device. Saving will change it to the "
                "privilege selected above."
            )

    def _on_credential_action_changed(self) -> None:
        self._password.setEnabled(self._selected_credential_action() is CredentialAction.SET)
        if not self._password.isEnabled():
            self._password.clear()

    def _selected_credential_action(self) -> CredentialAction:
        return CredentialAction(self._credential_action.currentData())

    def draft(self) -> UserDraft:
        """The draft this form describes."""
        action = self._selected_credential_action()
        password = self._password.text() if action is CredentialAction.SET else None
        return UserDraft(
            user_id=self._user_id.text(),
            first_name=self._first_name.text(),
            last_name=self._last_name.text(),
            privilege=int(self._privilege.currentData()),
            device_uid=None if self._user is None else self._user.device_uid,
            credential_action=action,
            password=password or None,
        ).normalised()

    # -- actions --------------------------------------------------------------

    def _on_accept(self) -> None:
        """Validate with the domain rules; only a valid draft closes the dialog."""
        draft = self.draft()
        problems = draft.validate()
        if problems:
            self._message.setText(" ".join(problems))
            return
        self.accept()
