"""Employees view (PHASE 05).

Business-level employee records above device users: list, search, add, edit
and (de)activate. Device mapping (linking one employee to a user ID on
another clock) is shown read-only here; it is managed through the service
and audited. All storage goes through ``EmployeeService`` off the UI thread.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
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

from clockmanager.domain.auth import Permission, Role, normalise_role
from clockmanager.domain.payroll import Employee
from clockmanager.gui.views.common import (
    build_table,
    fill_table,
    role_allows,
    run_off_thread,
    section_label,
)
from clockmanager.services.employees import EmployeeProfile, EmployeeService

__all__ = ["EmployeeFormDialog", "EmployeesView"]

_HEADERS = ("User ID", "Name", "Active", "Department", "Position", "Email")


class EmployeeFormDialog(QDialog):
    """Add/edit one employee. Validates through the domain rules."""

    def __init__(
        self, parent: QWidget | None = None, *, existing: EmployeeProfile | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit employee" if existing is not None else "Add employee")
        self._user_id = QLineEdit(self)
        self._first = QLineEdit(self)
        self._last = QLineEdit(self)
        self._department = QLineEdit(self)
        self._position = QLineEdit(self)
        self._email = QLineEdit(self)
        self._notes = QLineEdit(self)
        self._active = QCheckBox("Active", self)
        self._active.setChecked(True if existing is None else existing.active)
        if existing is not None:
            self._user_id.setText(existing.user_id)
            self._first.setText(existing.first_name)
            self._last.setText(existing.last_name)
            self._department.setText(existing.department)
            self._position.setText(existing.position)
            self._email.setText(existing.email)
            self._notes.setText(existing.notes)

        form = QFormLayout()
        form.addRow("User ID", self._user_id)
        form.addRow("First name", self._first)
        form.addRow("Last name", self._last)
        form.addRow("Department", self._department)
        form.addRow("Position", self._position)
        form.addRow("Email", self._email)
        form.addRow("Notes", self._notes)
        form.addRow("", self._active)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self._employee: Employee | None = None

    @property
    def employee(self) -> Employee | None:
        return self._employee

    def _on_accept(self) -> None:
        try:
            self._employee = Employee(
                user_id=self._user_id.text().strip(),
                first_name=self._first.text().strip(),
                last_name=self._last.text().strip(),
                active=self._active.isChecked(),
                department=self._department.text().strip(),
                position=self._position.text().strip(),
                email=self._email.text().strip(),
                notes=self._notes.text().strip(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid employee", str(exc))
            return
        self.accept()


class EmployeesView(QWidget):
    """Lists employees with add/edit/(de)activate."""

    def __init__(
        self,
        service: EmployeeService,
        parent: QWidget | None = None,
        *,
        role: Role | str | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        #: The logged-in role. Viewers are read-only; ``None`` keeps the
        #: legacy behaviour for tests.
        self._role = normalise_role(role) if role is not None else None
        self._employees: list[EmployeeProfile] = []

        self._table = build_table(_HEADERS, self)
        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText("Filter by user ID, name or department…")
        self._filter.textChanged.connect(self._apply_filter)

        self._add_button = QPushButton("Add", self)
        self._add_button.clicked.connect(self._on_add)
        self._edit_button = QPushButton("Edit", self)
        self._edit_button.clicked.connect(self._on_edit)
        self._toggle_button = QPushButton("Deactivate", self)
        self._toggle_button.clicked.connect(self._on_toggle)
        self._refresh_button = QPushButton("Refresh", self)
        self._refresh_button.clicked.connect(self.load)
        self._table.itemSelectionChanged.connect(self._on_selection)
        self._status = QLabel("Employees are the business layer above device users.", self)
        self._status.setWordWrap(True)

        controls = QHBoxLayout()
        controls.addWidget(self._add_button)
        controls.addWidget(self._edit_button)
        controls.addWidget(self._toggle_button)
        controls.addWidget(self._refresh_button)
        controls.addWidget(self._filter, stretch=1)

        layout = QVBoxLayout()
        layout.addWidget(section_label("Employees", self))
        layout.addLayout(controls)
        layout.addWidget(self._status)
        layout.addWidget(self._table, stretch=1)
        self.setLayout(layout)
        if not role_allows(self._role, Permission.MANAGE_EMPLOYEES):
            label = self._role.label if self._role is not None else "this role"
            self._status.setText(
                f"Your role ({label}) is read-only here. "
                "Only office staff and administrators may change employees."
            )
            self._add_button.setEnabled(False)

    def load(self) -> None:
        run_off_thread(
            self._service.list_employees, on_success=self._on_loaded, on_failure=self._on_failure
        )

    def _selected(self) -> EmployeeProfile | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        user_id = self._table.item(row, 0)
        if user_id is None:
            return None
        return next((e for e in self._employees if e.user_id == user_id.text()), None)

    def _on_selection(self) -> None:
        selected = self._selected()
        has_selection = selected is not None
        permitted = role_allows(self._role, Permission.MANAGE_EMPLOYEES)
        self._edit_button.setEnabled(has_selection and permitted)
        self._toggle_button.setEnabled(has_selection and permitted)
        if selected is not None:
            self._toggle_button.setText("Reactivate" if not selected.active else "Deactivate")

    def _on_add(self) -> None:
        dialog = EmployeeFormDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.employee is None:
            return
        draft = dialog.employee
        run_off_thread(
            lambda: self._service.create(draft, requester_role=self._role),
            on_success=lambda _: self.load(),
            on_failure=self._on_failure,
        )

    def _on_edit(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        dialog = EmployeeFormDialog(self, existing=selected)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.employee is None:
            return
        draft = dialog.employee
        run_off_thread(
            lambda: self._service.update(selected.employee_id, draft, requester_role=self._role),
            on_success=lambda _: self.load(),
            on_failure=self._on_failure,
        )

    def _on_toggle(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        run_off_thread(
            lambda: self._service.set_active(
                selected.employee_id,
                active=not selected.active,
                requester_role=self._role,
            ),
            on_success=lambda _: self.load(),
            on_failure=self._on_failure,
        )

    def _on_loaded(self, employees: Any) -> None:
        if not isinstance(employees, list):  # pragma: no cover - defensive
            return
        self._employees = employees
        actives = sum(1 for e in employees if e.active)
        self._status.setText(f"{len(employees)} employee(s), {actives} active.")
        if not role_allows(self._role, Permission.MANAGE_EMPLOYEES):
            self._status.setText(self._status.text() + " Read-only for your role.")
        self._apply_filter()
        self._on_selection()

    def _apply_filter(self) -> None:
        needle = self._filter.text().strip().lower()
        rows = [
            [
                e.user_id,
                e.display_name,
                "Yes" if e.active else "No",
                e.department,
                e.position,
                e.email,
            ]
            for e in self._employees
            if not needle
            or needle in e.user_id.lower()
            or needle in e.display_name.lower()
            or needle in e.department.lower()
        ]
        fill_table(self._table, rows)

    def _on_failure(self, message: str) -> None:
        self._status.setText(message)
