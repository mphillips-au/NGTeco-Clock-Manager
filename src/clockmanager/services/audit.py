"""Audit logging.

``SECURITY.md`` requires every device write to record an audit event. This
service is the only writer of that table, and it records refusals and failures
as well as successes — an audit log containing only successful actions cannot
answer the question it exists to answer.

Nothing here may record a credential. Callers pass a description produced by
:func:`clockmanager.domain.users.describe_changes`, which reports a PIN change
as an action and never as a value, and the description is additionally passed
through :func:`clockmanager.security.redaction.redact_text` before it is
stored.
"""

from __future__ import annotations

import getpass
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.persistence.database import Database
from clockmanager.persistence.models import AuditEventRecord
from clockmanager.persistence.repositories import AuditRepository
from clockmanager.security.redaction import redact_text

__all__ = ["AuditAction", "AuditEntry", "AuditOutcome", "AuditService", "current_actor"]

_logger = get_logger(__name__)

#: Truncation limit matching the ``detail`` column.
_MAX_DETAIL = 2000


class AuditAction(StrEnum):
    """Actions worth auditing. Extended by the phase that introduces one."""

    USER_CREATE = "user.create"
    USER_UPDATE = "user.update"
    USER_DELETE = "user.delete"
    EMPLOYEE_CREATE = "employee.create"
    EMPLOYEE_UPDATE = "employee.update"
    EMPLOYEE_DEACTIVATE = "employee.deactivate"
    EMPLOYEE_REACTIVATE = "employee.reactivate"
    EMPLOYEE_LINK = "employee.link"
    REPORT_EXPORT = "report.export"
    AUTH_LOGIN = "auth.login"
    AUTH_LOGOUT = "auth.logout"
    AUTH_CREATE_USER = "auth.create_user"
    AUTH_ROLE_CHANGE = "auth.role_change"
    AUTH_SET_ACTIVE = "auth.set_active"
    AUTH_PASSWORD_CHANGE = "auth.password_change"
    BACKUP_CREATE = "backup.create"
    BACKUP_RESTORE = "backup.restore"


class AuditOutcome(StrEnum):
    """How an audited action ended."""

    SUCCEEDED = "succeeded"
    #: The action was attempted and the device or a validation rule rejected it.
    FAILED = "failed"
    #: The action was blocked before anything was sent — an unverified
    #: capability, or an operator who did not confirm.
    REFUSED = "refused"


def current_actor() -> str:
    """Identify who performed an action.

    PHASE 07: when someone is logged in, :class:`ApplicationContext.audit`
    reports their username; otherwise the operating-system account is still
    the only identity the application has.
    """
    try:
        return getpass.getuser()
    except (OSError, KeyError):  # pragma: no cover - depends on the environment
        return "unknown"


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One audit row, ready to display. Contains no credential."""

    occurred_at: datetime
    actor: str
    action: str
    outcome: str
    detail: str
    device_name: str | None = None
    target: str | None = None
    target_uid: int | None = None

    @classmethod
    def from_record(cls, record: AuditEventRecord) -> AuditEntry:
        return cls(
            occurred_at=record.occurred_at,
            actor=record.actor,
            action=record.action,
            outcome=record.outcome,
            detail=record.detail,
            device_name=record.device_name,
            target=record.target,
            target_uid=record.target_uid,
        )

    def as_row(self) -> list[str]:
        return [
            self.occurred_at.isoformat(sep=" ", timespec="seconds"),
            self.actor,
            self.action,
            self.outcome,
            self.device_name or "",
            self.target or "",
            self.detail,
        ]


class AuditService:
    """Writes and reads the append-only audit log."""

    def __init__(self, database: Database, *, actor: str | None = None) -> None:
        self._database = database
        self._actor = actor if actor is not None else current_actor()

    @property
    def actor(self) -> str:
        return self._actor

    def record(
        self,
        action: AuditAction,
        outcome: AuditOutcome,
        *,
        detail: str = "",
        device_id: int | None = None,
        device_name: str | None = None,
        target: str | None = None,
        target_uid: int | None = None,
    ) -> AuditEntry:
        """Append one entry. Never raises past the caller's own failure path.

        An audit write that fails must not mask the operation's real result, so
        a storage failure is logged and swallowed rather than replacing the
        exception the caller is already handling.
        """
        safe_detail = redact_text(detail)[:_MAX_DETAIL]
        record = AuditEventRecord(
            actor=self._actor,
            action=action.value,
            outcome=outcome.value,
            device_id=device_id,
            device_name=device_name,
            target=target,
            target_uid=target_uid,
            detail=safe_detail,
        )

        try:
            with self._database.session() as session:
                AuditRepository(session).add(record)
        except Exception:
            # Deliberately broad: no storage problem may swallow or replace the
            # outcome of the device operation being audited.
            _logger.exception(
                "Could not write an audit entry",
                extra={"action": action.value, "outcome": outcome.value},
            )
        else:
            _logger.info(
                "Audit entry recorded",
                extra={
                    "action": action.value,
                    "outcome": outcome.value,
                    "target": target,
                    "device": device_name,
                },
            )

        return AuditEntry(
            occurred_at=record.occurred_at if record.occurred_at is not None else _now(),
            actor=self._actor,
            action=action.value,
            outcome=outcome.value,
            detail=safe_detail,
            device_name=device_name,
            target=target,
            target_uid=target_uid,
        )

    def recent(self, *, limit: int = 200) -> list[AuditEntry]:
        """The most recent entries, newest first."""
        with self._database.session() as session:
            records: Sequence[AuditEventRecord] = AuditRepository(session).recent(limit=limit)
            return [AuditEntry.from_record(record) for record in records]

    def count(self) -> int:
        with self._database.session() as session:
            return AuditRepository(session).count()


def _now() -> datetime:
    from clockmanager.persistence.models import utc_now

    return utc_now()
