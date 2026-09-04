"""Login, first-run setup and account administration (PHASE 07).

Only administrators reach :class:`UserAccountsView`; every other view learns
the logged-in role through its ``role`` constructor argument and disables
what the role may not do (the service layer refuses it regardless).

Passwords are masked on screen, live only in the dialog that collected them,
and are never logged or audited — audit details carry usernames and actions
only. Authentication itself is a fast local SQLite lookup, so these modal
dialogs call :class:`AuthService` directly instead of going off-thread; no
device I/O happens here.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.auth import Role
from clockmanager.errors import SecurityError
from clockmanager.gui.views.common import build_table, fill_table, run_off_thread, section_label
from clockmanager.services.auth import AuthenticatedUser, AuthService

__all__ = [
    "BootstrapAdminDialog",
    "LoginDialog",
    "UserAccountsView",
]

_logger = get_logger(__name__)

_ACCOUNT_HEADERS = ("Username", "Display name", "Role", "Active", "Last login")


def _role_combo(current: Role, parent: QWidget) -> QComboBox:
    box = QComboBox(parent)
    for role in (Role.ADMIN, Role.OFFICE_STAFF, Role.VIEWER):
        box.addItem(role.label, role.value)
    box.setCurrentIndex(list(Role).index(current) if current in list(Role) else 2)
    return box


def _combo_role(box: QComboBox) -> Role:
    return Role(str(box.currentData()))


class LoginDialog(QDialog):
    """Ask for credentials and authenticate. Refuses to close on failure."""

    def __init__(self, auth: AuthService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Log in")
        self._auth = auth
        self._user: AuthenticatedUser | None = None

        self._username = QLineEdit(self)
        self._username.setPlaceholderText("Username")
        self._password = QLineEdit(self)
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setPlaceholderText("Password")
        self._error = QLabel("", self)
        self._error.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Username", self._username)
        form.addRow("Password", self._password)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Log in to NGTeco Clock Manager.", self))
        layout.addLayout(form)
        layout.addWidget(self._error)
        layout.addWidget(buttons)
        self.setLayout(layout)

    @property
    def user(self) -> AuthenticatedUser | None:
        return self._user

    def _on_accept(self) -> None:
        try:
            self._user = self._auth.authenticate(self._username.text(), self._password.text())
        except SecurityError as exc:
            self._error.setText(str(exc))
            self._password.clear()
            return
        self.accept()


class BootstrapAdminDialog(QDialog):
    """First-run setup: create the initial administrator account."""

    def __init__(self, auth: AuthService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create the administrator account")
        self._auth = auth
        self._user: AuthenticatedUser | None = None

        self._username = QLineEdit(self)
        self._username.setPlaceholderText("e.g. admin")
        self._display = QLineEdit(self)
        self._display.setPlaceholderText("Display name (optional)")
        self._password = QLineEdit(self)
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._confirm = QLineEdit(self)
        self._confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self._error = QLabel("", self)
        self._error.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Username", self._username)
        form.addRow("Display name", self._display)
        form.addRow("Password (min 8 characters)", self._password)
        form.addRow("Confirm password", self._confirm)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(
            QLabel(
                "No accounts exist yet. Create the administrator account; "
                "further accounts are managed from User accounts.",
                self,
            )
        )
        layout.addLayout(form)
        layout.addWidget(self._error)
        layout.addWidget(buttons)
        self.setLayout(layout)

    @property
    def user(self) -> AuthenticatedUser | None:
        return self._user

    def _on_accept(self) -> None:
        username = self._username.text()
        password = self._password.text()
        if password != self._confirm.text():
            self._error.setText("The passwords do not match.")
            return
        try:
            self._auth.bootstrap_admin(
                username=username,
                display_name=self._display.text(),
                password=password,
            )
            # Start the session like any other login, so setup is audited
            # as both an account creation and a login.
            self._user = self._auth.authenticate(username, password)
        except SecurityError as exc:
            self._error.setText(str(exc))
            self._password.clear()
            self._confirm.clear()
            return
        self.accept()


class _AddAccountDialog(QDialog):
    """Collect a new account. Returns validated fields, never stores them."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add account")
        self._username = QLineEdit(self)
        self._display = QLineEdit(self)
        self._role = _role_combo(Role.VIEWER, self)
        self._password = QLineEdit(self)
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._confirm = QLineEdit(self)
        self._confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self._error = QLabel("", self)
        self._error.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Username", self._username)
        form.addRow("Display name", self._display)
        form.addRow("Role", self._role)
        form.addRow("Password (min 8 characters)", self._password)
        form.addRow("Confirm password", self._confirm)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self._error)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self._result: tuple[str, str, Role, str] | None = None

    @property
    def account(self) -> tuple[str, str, Role, str] | None:
        """``(username, display_name, role, password)`` when accepted."""
        return self._result

    def _on_accept(self) -> None:
        if self._password.text() != self._confirm.text():
            self._error.setText("The passwords do not match.")
            return
        self._result = (
            self._username.text(),
            self._display.text(),
            _combo_role(self._role),
            self._password.text(),
        )
        self.accept()


class _RoleDialog(QDialog):
    """Pick a new role for an existing account."""

    def __init__(self, current: Role, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Change role")
        self._role = _role_combo(current, self)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout()
        layout.addWidget(self._role)
        layout.addWidget(buttons)
        self.setLayout(layout)

    @property
    def role(self) -> Role:
        return _combo_role(self._role)


class _PasswordDialog(QDialog):
    """Set a new password.

    ``require_current`` is for operators changing their own password: they
    must prove the current one. An admin resetting someone else's never
    learns it — it was never stored.
    """

    def __init__(self, parent: QWidget | None = None, *, require_current: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set password")
        self._current: QLineEdit | None = None
        form = QFormLayout()
        if require_current:
            self._current = QLineEdit(self)
            self._current.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow("Current password", self._current)
        self._password = QLineEdit(self)
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._confirm = QLineEdit(self)
        self._confirm.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("New password (min 8 characters)", self._password)
        form.addRow("Confirm password", self._confirm)
        self._error = QLabel("", self)
        self._error.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self._error)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self._result: tuple[str | None, str] | None = None

    @property
    def passwords(self) -> tuple[str | None, str] | None:
        """``(current_password_or_None, new_password)`` when accepted."""
        return self._result

    def _on_accept(self) -> None:
        if self._password.text() != self._confirm.text():
            self._error.setText("The passwords do not match.")
            return
        current = self._current.text() if self._current is not None else None
        self._result = (current, self._password.text())
        self.accept()


class UserAccountsView(QWidget):
    """Admin-only local account administration."""

    def __init__(
        self,
        auth: AuthService,
        requester: AuthenticatedUser,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._auth = auth
        self._requester = requester
        self._accounts: list[AuthenticatedUser] = []

        self._table = build_table(_ACCOUNT_HEADERS, self)
        self._status = QLabel("Local accounts. Passwords are never shown.", self)
        self._status.setWordWrap(True)

        self._add_button = QPushButton("Add…", self)
        self._add_button.clicked.connect(self._on_add)
        self._role_button = QPushButton("Change role…", self)
        self._role_button.clicked.connect(self._on_role)
        self._toggle_button = QPushButton("Disable", self)
        self._toggle_button.clicked.connect(self._on_toggle)
        self._password_button = QPushButton("Reset password…", self)
        self._password_button.clicked.connect(self._on_password)
        self._refresh_button = QPushButton("Refresh", self)
        self._refresh_button.clicked.connect(self.load)
        self._table.itemSelectionChanged.connect(self._on_selection)

        controls = QHBoxLayout()
        controls.addWidget(self._add_button)
        controls.addWidget(self._role_button)
        controls.addWidget(self._toggle_button)
        controls.addWidget(self._password_button)
        controls.addStretch(1)
        controls.addWidget(self._refresh_button)

        layout = QVBoxLayout()
        layout.addWidget(section_label("User accounts (administrators only)", self))
        layout.addLayout(controls)
        layout.addWidget(self._status)
        layout.addWidget(self._table, stretch=1)
        self.setLayout(layout)

    # -- data ------------------------------------------------------------------

    def load(self) -> None:
        """Reload accounts off the UI thread."""
        run_off_thread(
            lambda: self._auth.list_users(requester=self._requester),
            on_success=self._on_loaded,
            on_failure=self._on_failure,
        )

    def _on_loaded(self, accounts: Any) -> None:
        if not isinstance(accounts, list):  # pragma: no cover - defensive
            return
        self._accounts = accounts
        fill_table(
            self._table,
            [
                [
                    account.username,
                    account.label,
                    account.role.label,
                    "Yes" if account.active else "No",
                    (
                        account.last_login_at.strftime("%Y-%m-%d %H:%M:%S")
                        if account.last_login_at is not None
                        else "Never"
                    ),
                ]
                for account in accounts
            ],
        )
        self._status.setText(f"{len(accounts)} account(s).")
        self._on_selection()

    def _selected(self) -> AuthenticatedUser | None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._accounts):
            return None
        username_item = self._table.item(row, 0)
        if username_item is None:  # pragma: no cover - defensive
            return None
        # Map through the username column, not the row index: sorting the
        # table must not target the wrong account.
        return next((a for a in self._accounts if a.username == username_item.text()), None)

    def _on_selection(self) -> None:
        selected = self._selected()
        has_selection = selected is not None
        self._role_button.setEnabled(has_selection)
        self._toggle_button.setEnabled(has_selection)
        self._password_button.setEnabled(has_selection)
        if selected is not None:
            self._toggle_button.setText("Enable" if not selected.active else "Disable")

    # -- actions -----------------------------------------------------------------

    def _on_add(self) -> None:
        dialog = _AddAccountDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.account is None:
            return
        username, display_name, role, password = dialog.account
        self._run(
            lambda: self._auth.create_user(
                username=username,
                display_name=display_name,
                role=role,
                password=password,
                requester=self._requester,
            ),
            success="Account created.",
        )

    def _on_role(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        dialog = _RoleDialog(selected.role, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        target = selected.username
        self._run(
            lambda: self._auth.set_role(
                username=target, role=dialog.role, requester=self._requester
            ),
            success="Role changed.",
        )

    def _on_toggle(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        verb = "enable" if not selected.active else "disable"
        answer = QMessageBox.question(
            self,
            f"{verb.capitalize()} account",
            f"{verb.capitalize()} the account {selected.username!r}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        target = selected.username
        active = not selected.active
        self._run(
            lambda: self._auth.set_active(
                username=target, active=active, requester=self._requester
            ),
            success="Account updated.",
        )

    def _on_password(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        dialog = _PasswordDialog(
            self,
            require_current=selected.username.lower() == self._requester.username.lower(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.passwords is None:
            return
        current, new_password = dialog.passwords
        target = selected.username
        self._run(
            lambda: self._auth.change_password(
                username=target,
                new_password=new_password,
                requester=self._requester,
                current_password=current,
            ),
            success="Password changed.",
        )

    def _run(self, work: Any, *, success: str) -> None:
        """Account writes are single local-SQLite rows: run them inline.

        Reads go off-thread like every other view; these writes complete in
        microseconds against the local database and report errors inline, so
        a modal result dialog is unnecessary.
        """
        try:
            work()
        except SecurityError as exc:
            self._status.setText(str(exc))
            return
        self._status.setText(success)
        self.load()

    def _on_failure(self, message: str) -> None:
        self._status.setText(message)
