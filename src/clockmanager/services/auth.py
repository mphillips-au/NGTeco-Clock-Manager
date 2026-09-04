"""Local-account authentication and role enforcement (PHASE 07).

Three roles separate admin/office access (see
:mod:`clockmanager.domain.auth`): ``ADMIN`` holds every control, including
account administration; ``OFFICE_STAFF`` runs the normal office workflows;
``VIEWER`` is read-only.

Passwords are stored only as salted PBKDF2-HMAC-SHA256 hashes
(:mod:`clockmanager.security.passwords`). Plaintext exists for the
duration of one hash or verify call and is never logged, never audited and
never leaves this service: audit details carry usernames and actions only.

This module is PySide6-free so the future headless service authenticates
through exactly the same code as the GUI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.auth import Permission, Role, can, normalise_role, require
from clockmanager.errors import SecurityError
from clockmanager.persistence.database import Database
from clockmanager.persistence.models import AppUserRecord, utc_now
from clockmanager.persistence.repositories import AppUserRepository
from clockmanager.security.passwords import (
    hash_password,
    validate_password,
    validate_username,
    verify_password,
)
from clockmanager.services.audit import AuditAction, AuditOutcome, AuditService

__all__ = ["AuthService", "AuthSession", "AuthenticatedUser"]

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Who is logged in. Carries identity and role, never a credential."""

    username: str
    display_name: str
    role: Role
    active: bool = True
    #: When this account last logged in, if ever. Display metadata only.
    last_login_at: datetime | None = None

    @property
    def label(self) -> str:
        """Display name with a username fallback."""
        return self.display_name.strip() or self.username

    def has(self, permission: Permission) -> bool:
        """Return whether this user holds ``permission``."""
        return can(self.role, permission)

    def require(self, permission: Permission) -> None:
        """Raise :class:`SecurityError` when this user lacks ``permission``."""
        require(self.role, permission)


class AuthSession:
    """Holds the currently logged-in user for one application lifetime.

    ``ApplicationContext`` owns one of these; :class:`AuthService` reads and
    updates it. It is deliberately a tiny mutable object: the context itself
    stays an immutable snapshot of configuration, while the login state it
    carries changes as operators log in and out.
    """

    def __init__(self) -> None:
        self._current: AuthenticatedUser | None = None

    @property
    def current_user(self) -> AuthenticatedUser | None:
        return self._current

    def login(self, user: AuthenticatedUser) -> None:
        self._current = user

    def logout(self) -> AuthenticatedUser | None:
        previous, self._current = self._current, None
        return previous


class AuthService:
    """Creates and verifies local accounts; tracks the login session."""

    def __init__(self, database: Database, audit: AuditService, session: AuthSession) -> None:
        self._database = database
        self._audit = audit
        self._session = session

    # -- session ------------------------------------------------------------

    @property
    def current_user(self) -> AuthenticatedUser | None:
        """The logged-in user, or ``None`` when nobody is logged in."""
        return self._session.current_user

    def needs_setup(self) -> bool:
        """Return whether no account exists yet (first-run bootstrap)."""
        with self._database.session() as session:
            return AppUserRepository(session).count() == 0

    # -- login / logout ------------------------------------------------------

    def authenticate(self, username: str, password: str) -> AuthenticatedUser:
        """Verify credentials, start the session and audit the attempt.

        Failures audit as ``failed`` and raise :class:`SecurityError` with a
        generic message that does not reveal whether the username exists.
        The password is never logged or audited.
        """
        cleaned = username.strip()
        with self._database.session() as session:
            record = AppUserRepository(session).get_by_username(cleaned)

        if record is None or not verify_password(password, record.password_hash):
            self._record_login(cleaned or "unknown", AuditOutcome.FAILED, "Login failed.")
            raise SecurityError("Invalid username or password.")

        user = self._to_user(record)
        if not record.is_active:
            self._record_login(user.username, AuditOutcome.REFUSED, "Account is disabled.")
            raise SecurityError("This account is disabled.")

        with self._database.session() as session:
            stored = session.get(AppUserRecord, record.id)
            if stored is not None:  # pragma: no branch - row cannot vanish mid-login
                stored.last_login_at = utc_now()
                session.flush()

        self._session.login(user)
        self._scoped_audit(user.username).record(
            AuditAction.AUTH_LOGIN,
            AuditOutcome.SUCCEEDED,
            detail=f"User {user.username!r} logged in.",
            target=user.username,
        )
        _logger.info("User logged in", extra={"actor": user.username})
        return user

    def logout(self) -> None:
        """End the session, auditing who logged out. No-op when logged out."""
        user = self._session.logout()
        if user is None:
            return
        self._scoped_audit(user.username).record(
            AuditAction.AUTH_LOGOUT,
            AuditOutcome.SUCCEEDED,
            detail=f"User {user.username!r} logged out.",
            target=user.username,
        )
        _logger.info("User logged out", extra={"actor": user.username})

    def bootstrap_admin(
        self, *, username: str, display_name: str, password: str
    ) -> AuthenticatedUser:
        """Create the first account, which must be an active admin.

        Only usable when no account exists. Afterwards, account creation
        requires an admin (see :meth:`create_user`).
        """
        with self._database.session() as session:
            if AppUserRepository(session).count() != 0:
                raise SecurityError("Setup is already complete: accounts exist.")
        return self._create_row(
            username=username,
            display_name=display_name,
            role=Role.ADMIN,
            password=password,
            active=True,
            actor="setup",
        )

    # -- account administration (admin only) ----------------------------------

    def list_users(self, *, requester: AuthenticatedUser) -> list[AuthenticatedUser]:
        """List every account. Requires ``accounts.manage`` (admin)."""
        requester.require(Permission.MANAGE_ACCOUNTS)
        with self._database.session() as session:
            return [self._to_user(row) for row in AppUserRepository(session).list_all()]

    def create_user(
        self,
        *,
        username: str,
        display_name: str,
        role: Role | str,
        password: str,
        requester: AuthenticatedUser,
    ) -> AuthenticatedUser:
        """Create one account. Requires ``accounts.manage`` (admin)."""
        requester.require(Permission.MANAGE_ACCOUNTS)
        return self._create_row(
            username=username,
            display_name=display_name,
            role=normalise_role(role),
            password=password,
            active=True,
            actor=requester.username,
        )

    def set_role(
        self, *, username: str, role: Role | str, requester: AuthenticatedUser
    ) -> AuthenticatedUser:
        """Change an account's role, auditing the change. Admin only."""
        requester.require(Permission.MANAGE_ACCOUNTS)
        resolved = normalise_role(role)
        with self._database.session() as session:
            repo = AppUserRepository(session)
            record = self._require_row(repo, username)
            old_role = record.role
            if old_role == resolved.value:
                return self._to_user(record)
            if (
                old_role == Role.ADMIN.value
                and resolved != Role.ADMIN
                and repo.count_active_admins() <= 1
                and record.is_active
            ):
                raise SecurityError(
                    "Refusing to demote the last active admin: "
                    "the application would have no administrator left."
                )
            record.role = resolved.value
            session.flush()
            updated = self._to_user(record)
        self._scoped_audit(requester.username).record(
            AuditAction.AUTH_ROLE_CHANGE,
            AuditOutcome.SUCCEEDED,
            detail=f"Changed {updated.username!r} from {old_role!r} to {resolved.value!r}.",
            target=updated.username,
        )
        _logger.info(
            "Changed account role",
            extra={"actor": requester.username, "target": updated.username},
        )
        return updated

    def set_active(
        self, *, username: str, active: bool, requester: AuthenticatedUser
    ) -> AuthenticatedUser:
        """Enable or disable an account, auditing the change. Admin only."""
        requester.require(Permission.MANAGE_ACCOUNTS)
        with self._database.session() as session:
            repo = AppUserRepository(session)
            record = self._require_row(repo, username)
            if record.is_active == active:
                return self._to_user(record)
            if not active and record.role == Role.ADMIN.value and repo.count_active_admins() <= 1:
                raise SecurityError(
                    "Refusing to disable the last active admin: "
                    "the application would have no administrator left."
                )
            record.is_active = active
            session.flush()
            updated = self._to_user(record)
        verb = "Enabled" if active else "Disabled"
        self._scoped_audit(requester.username).record(
            AuditAction.AUTH_SET_ACTIVE,
            AuditOutcome.SUCCEEDED,
            detail=f"{verb} account {updated.username!r}.",
            target=updated.username,
        )
        _logger.info(
            "Changed account active flag",
            extra={"actor": requester.username, "target": updated.username},
        )
        return updated

    def change_password(
        self,
        *,
        username: str,
        new_password: str,
        requester: AuthenticatedUser,
        current_password: str | None = None,
    ) -> None:
        """Change an account's password.

        An operator changing their own password must prove the current one;
        an admin resetting someone else's need not (and never learns it —
        it was never stored). The new password is hashed before storage and
        never logged or audited.
        """
        problems = validate_password(new_password)
        if problems:
            raise SecurityError(" ".join(problems))
        with self._database.session() as session:
            repo = AppUserRepository(session)
            record = self._require_row(repo, username)
            if requester.username.strip().lower() == record.username.lower():
                if current_password is None or not verify_password(
                    current_password, record.password_hash
                ):
                    raise SecurityError("The current password is not correct.")
            else:
                requester.require(Permission.MANAGE_ACCOUNTS)
            record.password_hash = hash_password(new_password)
            session.flush()
        self._scoped_audit(requester.username).record(
            AuditAction.AUTH_PASSWORD_CHANGE,
            AuditOutcome.SUCCEEDED,
            detail=f"Changed the password for {record.username!r}.",
            target=record.username,
        )
        _logger.info(
            "Changed account password",
            extra={"actor": requester.username, "target": record.username},
        )

    # -- internals -------------------------------------------------------------

    def _create_row(
        self,
        *,
        username: str,
        display_name: str,
        role: Role,
        password: str,
        active: bool,
        actor: str,
    ) -> AuthenticatedUser:
        cleaned = username.strip()
        problems = validate_username(cleaned)
        problems.extend(validate_password(password))
        if problems:
            raise SecurityError(" ".join(problems))
        with self._database.session() as session:
            repo = AppUserRepository(session)
            if repo.get_by_username(cleaned) is not None:
                raise SecurityError(f"An account named {cleaned!r} already exists.")
            created = repo.add(
                AppUserRecord(
                    username=cleaned,
                    display_name=display_name.strip(),
                    role=normalise_role(role).value,
                    password_hash=hash_password(password),
                    is_active=active,
                )
            )
            user = self._to_user(created)
        self._scoped_audit(actor).record(
            AuditAction.AUTH_CREATE_USER,
            AuditOutcome.SUCCEEDED,
            detail=f"Created account {user.username!r} with role {user.role.value!r}.",
            target=user.username,
        )
        _logger.info("Created account", extra={"actor": actor, "target": user.username})
        return user

    @staticmethod
    def _require_row(repo: AppUserRepository, username: str) -> AppUserRecord:
        record = repo.get_by_username(username)
        if record is None:
            raise SecurityError(f"Unknown account {username.strip()!r}.")
        return record

    @staticmethod
    def _to_user(record: AppUserRecord) -> AuthenticatedUser:
        return AuthenticatedUser(
            username=record.username,
            display_name=record.display_name,
            role=normalise_role(record.role),
            active=bool(record.is_active),
            last_login_at=record.last_login_at,
        )

    def _scoped_audit(self, actor: str) -> AuditService:
        """An audit service attributing entries to ``actor``.

        The shared service captures its actor at construction (the OS user
        or whoever was logged in then); account events must name the account
        they concern, so a scoped service is built per call.
        """
        return AuditService(self._database, actor=actor)

    def _record_login(self, username: str, outcome: AuditOutcome, detail: str) -> None:
        """Audit a login attempt. The detail carries no credential."""
        self._scoped_audit(username).record(
            AuditAction.AUTH_LOGIN, outcome, detail=detail, target=username
        )
        _logger.info("Login attempt recorded", extra={"actor": username})
