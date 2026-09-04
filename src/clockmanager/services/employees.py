"""Employee business service (PHASE 05).

Employees are the business layer above device users: an internal record with
a canonical user ID, names, activity flag and HR fields, plus per-device
mappings for clocks where the same person carries a different user ID.

Raw attendance is never touched here; the timesheet service resolves which
stored punches belong to an employee through these mappings.
"""

from __future__ import annotations

from dataclasses import dataclass

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.auth import Permission, Role, require
from clockmanager.domain.payroll import Employee
from clockmanager.errors import ClockManagerError
from clockmanager.persistence.database import Database
from clockmanager.persistence.models import EmployeeDeviceLinkRecord, EmployeeRecord
from clockmanager.persistence.repositories import EmployeeRepository
from clockmanager.services.audit import AuditAction, AuditOutcome, AuditService

__all__ = ["EmployeeProfile", "EmployeeService"]

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EmployeeProfile:
    """Stored employee plus its device mappings, ready to display."""

    employee_id: int
    user_id: str
    first_name: str
    last_name: str
    active: bool
    department: str
    position: str
    email: str
    notes: str
    device_user_ids: tuple[str, ...]

    @property
    def display_name(self) -> str:
        full = f"{self.first_name} {self.last_name}".strip()
        return full or self.user_id


class EmployeeService:
    """Create, update, (de)activate and map employees."""

    def __init__(self, database: Database, audit: AuditService) -> None:
        self._database = database
        self._audit = audit

    # -- reads ---------------------------------------------------------------

    def list_employees(self, *, include_inactive: bool = True) -> list[EmployeeProfile]:
        with self._database.session() as session:
            repo = EmployeeRepository(session)
            return [
                self._to_profile(repo, row)
                for row in repo.list_all(include_inactive=include_inactive)
            ]

    def get(self, employee_id: int) -> EmployeeProfile | None:
        with self._database.session() as session:
            repo = EmployeeRepository(session)
            row = repo.get(employee_id)
            return None if row is None else self._to_profile(repo, row)

    def count(self) -> int:
        with self._database.session() as session:
            return EmployeeRepository(session).count()

    def user_ids_for(self, employee_id: int) -> list[str]:
        """All device user IDs that resolve to this employee."""
        profile = self.get(employee_id)
        if profile is None:
            raise ClockManagerError(f"Unknown employee id {employee_id}")
        return [profile.user_id, *[u for u in profile.device_user_ids if u != profile.user_id]]

    # -- writes ---------------------------------------------------------------

    def create(
        self, employee: Employee, *, requester_role: Role | str | None = None
    ) -> EmployeeProfile:
        """Create an employee. ``requester_role`` enforces PHASE 07 roles
        (admin or office staff); ``None`` keeps the legacy path for callers
        without an interactive identity."""
        if requester_role is not None:
            require(requester_role, Permission.MANAGE_EMPLOYEES)
        with self._database.session() as session:
            repo = EmployeeRepository(session)
            if repo.get_by_user_id(employee.user_id) is not None:
                raise ClockManagerError(
                    f"An employee with user ID {employee.user_id!r} already exists."
                )
            row = repo.add(
                EmployeeRecord(
                    user_id=employee.user_id,
                    first_name=employee.first_name,
                    last_name=employee.last_name,
                    active=employee.active,
                    department=employee.department,
                    position=employee.position,
                    email=employee.email,
                    notes=employee.notes,
                )
            )
            profile = self._to_profile(repo, row)
        self._audit.record(
            AuditAction.EMPLOYEE_CREATE,
            AuditOutcome.SUCCEEDED,
            target=employee.user_id,
            detail=f"Created employee {employee.display_name}",
        )
        _logger.info("Created employee", extra={"user_id": employee.user_id})
        return profile

    def update(
        self,
        employee_id: int,
        employee: Employee,
        *,
        requester_role: Role | str | None = None,
    ) -> EmployeeProfile:
        if requester_role is not None:
            require(requester_role, Permission.MANAGE_EMPLOYEES)
        with self._database.session() as session:
            repo = EmployeeRepository(session)
            row = repo.get(employee_id)
            if row is None:
                raise ClockManagerError(f"Unknown employee id {employee_id}")
            clash = repo.get_by_user_id(employee.user_id)
            if clash is not None and clash.id != employee_id:
                raise ClockManagerError(
                    f"Another employee already uses user ID {employee.user_id!r}."
                )
            row.user_id = employee.user_id
            row.first_name = employee.first_name
            row.last_name = employee.last_name
            row.active = employee.active
            row.department = employee.department
            row.position = employee.position
            row.email = employee.email
            row.notes = employee.notes
            session.flush()
            profile = self._to_profile(repo, row)
        self._audit.record(
            AuditAction.EMPLOYEE_UPDATE,
            AuditOutcome.SUCCEEDED,
            target=employee.user_id,
            detail=f"Updated employee {employee.display_name}",
        )
        return profile

    def set_active(
        self,
        employee_id: int,
        *,
        active: bool,
        requester_role: Role | str | None = None,
    ) -> EmployeeProfile:
        if requester_role is not None:
            require(requester_role, Permission.MANAGE_EMPLOYEES)
        with self._database.session() as session:
            repo = EmployeeRepository(session)
            row = repo.get(employee_id)
            if row is None:
                raise ClockManagerError(f"Unknown employee id {employee_id}")
            row.active = active
            session.flush()
            profile = self._to_profile(repo, row)
        self._audit.record(
            AuditAction.EMPLOYEE_DEACTIVATE if not active else AuditAction.EMPLOYEE_REACTIVATE,
            AuditOutcome.SUCCEEDED,
            target=profile.user_id,
            detail=f"{'Deactivated' if not active else 'Reactivated'} employee {profile.display_name}",
        )
        return profile

    def link_device(
        self,
        employee_id: int,
        *,
        device_id: int,
        user_id: str,
        device_uid: int | None = None,
        requester_role: Role | str | None = None,
    ) -> EmployeeProfile:
        """Map an employee to a (device, user ID) pair on another clock."""
        if requester_role is not None:
            require(requester_role, Permission.MANAGE_EMPLOYEES)
        if not user_id.strip():
            raise ClockManagerError("Device user ID must not be empty.")
        with self._database.session() as session:
            repo = EmployeeRepository(session)
            row = repo.get(employee_id)
            if row is None:
                raise ClockManagerError(f"Unknown employee id {employee_id}")
            existing = next(
                (link for link in repo.links_for(employee_id) if link.device_id == device_id),
                None,
            )
            if existing is not None:
                existing.user_id = user_id
                existing.device_uid = device_uid
            else:
                repo.add_link(
                    EmployeeDeviceLinkRecord(
                        employee_id=employee_id,
                        device_id=device_id,
                        user_id=user_id,
                        device_uid=device_uid,
                    )
                )
            session.flush()
            profile = self._to_profile(repo, row)
        self._audit.record(
            AuditAction.EMPLOYEE_LINK,
            AuditOutcome.SUCCEEDED,
            target=profile.user_id,
            detail=f"Linked {profile.display_name} to device {device_id} as {user_id!r}",
        )
        return profile

    def unlink_device(
        self,
        employee_id: int,
        *,
        device_id: int,
        requester_role: Role | str | None = None,
    ) -> EmployeeProfile | None:
        if requester_role is not None:
            require(requester_role, Permission.MANAGE_EMPLOYEES)
        with self._database.session() as session:
            repo = EmployeeRepository(session)
            row = repo.get(employee_id)
            if row is None:
                raise ClockManagerError(f"Unknown employee id {employee_id}")
            repo.remove_link(employee_id, device_id)
            session.flush()
            return self._to_profile(repo, row)

    # -- mapping ---------------------------------------------------------------

    @staticmethod
    def _to_profile(repo: EmployeeRepository, row: EmployeeRecord) -> EmployeeProfile:
        links = repo.links_for(row.id)
        return EmployeeProfile(
            employee_id=row.id,
            user_id=row.user_id,
            first_name=row.first_name,
            last_name=row.last_name,
            active=row.active,
            department=row.department,
            position=row.position,
            email=row.email,
            notes=row.notes,
            device_user_ids=tuple(link.user_id for link in links),
        )
