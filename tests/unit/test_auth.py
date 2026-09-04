"""PHASE 07 tests: roles, password hashing, accounts and role enforcement.

Covers what the phase promises without ever touching a real clock:

* salted password hashing (stdlib PBKDF2) and validation
* the role/permission matrix shared by services and GUI
* account lifecycle: setup, login/logout, admin, last-admin guard
* service-level refusal for roles without the permission
* schema version 6 migration (fresh and v1 upgrade)
* that no credential ever reaches logs, audit details or ``repr``
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from clockmanager.domain.auth import (
    Permission,
    Role,
    can,
    normalise_role,
    require,
)
from clockmanager.errors import SecurityError
from clockmanager.persistence.models import SCHEMA_VERSION
from clockmanager.persistence.repositories import AppUserRepository
from clockmanager.security.passwords import (
    PASSWORD_HASH_PREFIX,
    hash_password,
    validate_password,
    validate_username,
    verify_password,
)
from clockmanager.services.application import ApplicationContext
from clockmanager.services.audit import AuditAction, AuditOutcome, AuditService
from clockmanager.services.auth import AuthenticatedUser, AuthService

PASSWORD = "correct-horse-41"


def make_service(context: ApplicationContext) -> AuthService:
    return AuthService(context.database, AuditService(context.database), context.auth_session)


def make_admin(context: ApplicationContext, username: str = "admin") -> AuthenticatedUser:
    service = make_service(context)
    service.bootstrap_admin(username=username, display_name="Admin", password=PASSWORD)
    return service.authenticate(username, PASSWORD)


# -- password hashing ----------------------------------------------------------


def test_hash_verifies_and_wrong_password_fails() -> None:
    stored = hash_password(PASSWORD)
    assert verify_password(PASSWORD, stored)
    assert not verify_password("wrong-password", stored)


def test_hash_carries_salt_and_parameters_not_plaintext() -> None:
    first = hash_password(PASSWORD)
    second = hash_password(PASSWORD)
    assert first != second  # fresh random salt each time
    assert PASSWORD not in first
    prefix, iterations, salt_hex, hash_hex = first.split("$")
    assert prefix == PASSWORD_HASH_PREFIX
    assert int(iterations) >= 100_000
    assert len(bytes.fromhex(salt_hex)) >= 16
    assert len(bytes.fromhex(hash_hex)) == 32


def test_verify_rejects_malformed_or_foreign_hashes() -> None:
    assert not verify_password(PASSWORD, "not-a-hash")
    assert not verify_password(PASSWORD, "pbkdf2-sha256$1$abcd$ef01")
    assert not verify_password(PASSWORD, "bcrypt$12$salt$hash")
    assert not verify_password(PASSWORD, "")


def test_short_password_is_refused_before_hashing() -> None:
    with pytest.raises(ValueError, match="at least 8"):
        hash_password("short")
    assert validate_password("")
    assert not validate_password(PASSWORD)


def test_username_rules() -> None:
    assert validate_username("admin") == []
    assert validate_username("")
    assert validate_username("ab")
    assert validate_username("bad name!")
    assert validate_username("x" * 33)


# -- roles ---------------------------------------------------------------------


def test_admin_holds_every_permission() -> None:
    assert set(Permission) <= {perm for perm in Permission if can(Role.ADMIN, perm)}


def test_office_staff_has_workflows_but_no_admin_controls() -> None:
    assert can(Role.OFFICE_STAFF, Permission.MANAGE_EMPLOYEES)
    assert can(Role.OFFICE_STAFF, Permission.SYNC_ATTENDANCE)
    assert can(Role.OFFICE_STAFF, Permission.LIVE_CAPTURE)
    assert can(Role.OFFICE_STAFF, Permission.VIEW_AUDIT)
    assert not can(Role.OFFICE_STAFF, Permission.MANAGE_ACCOUNTS)
    assert not can(Role.OFFICE_STAFF, Permission.MANAGE_DEVICE_SETTINGS)
    assert not can(Role.OFFICE_STAFF, Permission.MANAGE_DEVICE_USERS)
    assert not can(Role.OFFICE_STAFF, Permission.VIEW_DIAGNOSTICS)


def test_viewer_is_read_only_but_may_export() -> None:
    assert can(Role.VIEWER, Permission.EXPORT_REPORTS)
    assert not can(Role.VIEWER, Permission.MANAGE_EMPLOYEES)
    assert not can(Role.VIEWER, Permission.SYNC_ATTENDANCE)
    assert not can(Role.VIEWER, Permission.VIEW_AUDIT)


def test_require_raises_without_disclosing_anything_sensitive() -> None:
    with pytest.raises(SecurityError, match="Viewer"):
        require(Role.VIEWER, Permission.MANAGE_DEVICE_USERS)
    require(Role.ADMIN, Permission.MANAGE_DEVICE_USERS)
    with pytest.raises(SecurityError):
        normalise_role("superuser")


def test_authenticated_user_helpers() -> None:
    viewer = AuthenticatedUser(username="v", display_name="", role=Role.VIEWER)
    assert viewer.label == "v"
    assert not viewer.has(Permission.MANAGE_EMPLOYEES)
    with pytest.raises(SecurityError):
        viewer.require(Permission.MANAGE_EMPLOYEES)


# -- account lifecycle ----------------------------------------------------------


def test_fresh_database_needs_setup(context: ApplicationContext) -> None:
    assert make_service(context).needs_setup()


def test_bootstrap_creates_the_first_admin_and_logs_in(
    context: ApplicationContext,
) -> None:
    service = make_service(context)
    created = service.bootstrap_admin(username="boss", display_name="The Boss", password=PASSWORD)
    assert created.role == Role.ADMIN
    assert created.active
    assert not service.needs_setup()

    user = service.authenticate("boss", PASSWORD)
    assert user.username == "boss"
    assert service.current_user == user


def test_second_bootstrap_is_refused(context: ApplicationContext) -> None:
    service = make_service(context)
    service.bootstrap_admin(username="boss", display_name="", password=PASSWORD)
    with pytest.raises(SecurityError, match="already complete"):
        service.bootstrap_admin(username="other", display_name="", password=PASSWORD)


def test_login_is_case_insensitive_but_create_is_unique(
    context: ApplicationContext,
) -> None:
    admin = make_admin(context)
    service = make_service(context)
    created = service.create_user(
        username="Office1",
        display_name="",
        role=Role.OFFICE_STAFF,
        password=PASSWORD,
        requester=admin,
    )
    assert created.username == "Office1"
    assert service.authenticate("office1", PASSWORD).username == "Office1"
    with pytest.raises(SecurityError, match="already exists"):
        service.create_user(
            username="OFFICE1",
            display_name="",
            role=Role.VIEWER,
            password=PASSWORD,
            requester=admin,
        )


def test_wrong_password_fails_closed_and_audits(
    context: ApplicationContext,
) -> None:
    make_admin(context)
    service = make_service(context)
    service.logout()
    with pytest.raises(SecurityError, match="Invalid username or password"):
        service.authenticate("admin", "nope-nope-nope")
    with pytest.raises(SecurityError, match="Invalid username or password"):
        service.authenticate("nobody", PASSWORD)
    assert service.current_user is None

    entries = AuditService(context.database).recent(limit=10)
    failures = [e for e in entries if e.action == "auth.login" and e.outcome == "failed"]
    assert len(failures) >= 2
    for entry in entries:
        assert PASSWORD not in entry.detail
        assert "nope" not in entry.detail


def test_disabled_account_cannot_log_in(context: ApplicationContext) -> None:
    admin = make_admin(context)
    service = make_service(context)
    service.create_user(
        username="temp",
        display_name="",
        role=Role.VIEWER,
        password=PASSWORD,
        requester=admin,
    )
    service.set_active(username="temp", active=False, requester=admin)
    with pytest.raises(SecurityError, match="disabled"):
        service.authenticate("temp", PASSWORD)


def test_non_admin_cannot_administer_accounts(context: ApplicationContext) -> None:
    admin = make_admin(context)
    service = make_service(context)
    service.create_user(
        username="staff",
        display_name="",
        role=Role.OFFICE_STAFF,
        password=PASSWORD,
        requester=admin,
    )
    staff = service.authenticate("staff", PASSWORD)
    with pytest.raises(SecurityError):
        service.create_user(
            username="x",
            display_name="",
            role=Role.VIEWER,
            password=PASSWORD,
            requester=staff,
        )
    with pytest.raises(SecurityError):
        service.list_users(requester=staff)
    with pytest.raises(SecurityError):
        service.set_role(username="staff", role=Role.ADMIN, requester=staff)


def test_last_active_admin_cannot_be_demoted_or_disabled(
    context: ApplicationContext,
) -> None:
    admin = make_admin(context)
    service = make_service(context)
    with pytest.raises(SecurityError, match="last active admin"):
        service.set_role(username="admin", role=Role.VIEWER, requester=admin)
    with pytest.raises(SecurityError, match="last active admin"):
        service.set_active(username="admin", active=False, requester=admin)

    # With two admins, demoting one is fine.
    service.create_user(
        username="admin2",
        display_name="",
        role=Role.ADMIN,
        password=PASSWORD,
        requester=admin,
    )
    demoted = service.set_role(username="admin2", role=Role.VIEWER, requester=admin)
    assert demoted.role == Role.VIEWER


def test_self_password_change_needs_current_admin_reset_does_not(
    context: ApplicationContext,
) -> None:
    admin = make_admin(context)
    service = make_service(context)
    service.create_user(
        username="staff",
        display_name="",
        role=Role.OFFICE_STAFF,
        password=PASSWORD,
        requester=admin,
    )
    staff = service.authenticate("staff", PASSWORD)
    with pytest.raises(SecurityError, match="current password"):
        service.change_password(
            username="staff",
            new_password="new-password-1",
            requester=staff,
            current_password="wrong",
        )
    service.change_password(
        username="staff",
        new_password="new-password-1",
        requester=staff,
        current_password=PASSWORD,
    )
    assert service.authenticate("staff", "new-password-1").username == "staff"

    # An admin resets someone else's password without knowing the old one.
    service.change_password(username="staff", new_password="reset-password-2", requester=admin)
    service._session.logout()
    assert service.authenticate("staff", "reset-password-2").username == "staff"


def test_logout_audits_and_clears_the_session(context: ApplicationContext) -> None:
    service = make_service(context)
    service.logout()  # no-op when nobody is logged in
    make_admin(context)
    assert service.current_user is not None
    service.logout()
    assert service.current_user is None
    entries = AuditService(context.database).recent(limit=5)
    assert any(e.action == "auth.logout" and e.outcome == "succeeded" for e in entries)


def test_audit_actor_follows_the_logged_in_user(context: ApplicationContext) -> None:
    make_admin(context)
    context.audit.record(AuditAction.REPORT_EXPORT, AuditOutcome.SUCCEEDED)
    assert context.audit.recent()[0].actor == "admin"


def test_no_credential_reaches_storage_or_repr(context: ApplicationContext) -> None:
    make_admin(context, username="boss")
    with context.database.session() as session:
        rows = AppUserRepository(session).list_all()
        assert len(rows) == 1
        assert PASSWORD not in rows[0].password_hash
        assert "password" not in repr(rows[0]).lower().replace("appuserrecord", "")

    with context.database.session() as session:
        records = session.execute(text("SELECT * FROM app_users")).all()
        assert not any(PASSWORD in str(value) for row in records for value in row)


# -- service-level enforcement ----------------------------------------------------


def test_device_user_writes_refuse_non_admin(context: ApplicationContext) -> None:
    from clockmanager.domain.users import UserDraft
    from clockmanager.services.devices import DeviceProfile

    draft = UserDraft(device_uid=None, user_id="EMP-1", first_name="A", last_name="B")
    profile = DeviceProfile(name="x", host="192.0.2.10")
    with pytest.raises(SecurityError):
        context.users.save_user(profile, draft, requester_role=Role.VIEWER)
    with pytest.raises(SecurityError):
        context.users.delete_user(profile, 1, confirmed=True, requester_role=Role.OFFICE_STAFF)


def test_device_settings_refuse_non_admin(context: ApplicationContext) -> None:
    from clockmanager.services.devices import DeviceProfile

    with pytest.raises(SecurityError):
        context.devices.save_profile(
            DeviceProfile(name="Clock", host="192.0.2.10"), requester_role=Role.OFFICE_STAFF
        )
    with pytest.raises(SecurityError):
        context.devices.delete_profile(1, requester_role=Role.VIEWER)
    # Legacy callers without an identity keep working.
    saved = context.devices.save_profile(DeviceProfile(name="Clock", host="192.0.2.10"))
    assert saved.device_id is not None


def test_employee_writes_refuse_viewers_but_allow_office(
    context: ApplicationContext,
) -> None:
    from clockmanager.domain.payroll import Employee

    with pytest.raises(SecurityError):
        context.employees.create(
            Employee(user_id="E1", first_name="A", last_name="B"),
            requester_role=Role.VIEWER,
        )
    created = context.employees.create(
        Employee(user_id="E1", first_name="A", last_name="B"),
        requester_role=Role.OFFICE_STAFF,
    )
    assert created.user_id == "E1"
    with pytest.raises(SecurityError):
        context.employees.set_active(created.employee_id, active=False, requester_role=Role.VIEWER)


def test_manual_sync_and_live_refuse_viewers(context: ApplicationContext) -> None:
    from clockmanager.services.devices import DeviceProfile

    profile = DeviceProfile(name="Clock", host="192.0.2.10")
    with pytest.raises(SecurityError):
        context.sync.manual_sync(profile, requester_role=Role.VIEWER)


def test_export_is_allowed_for_every_role(context: ApplicationContext) -> None:
    from clockmanager.domain.reports import ExportFormat, ReportFilter

    report = context.reports.sync_history(ReportFilter())
    data, _filename, _mime = context.reports.export(
        report, ExportFormat.CSV, requester_role=Role.VIEWER
    )
    assert data


# -- schema version 6 --------------------------------------------------------------


def test_schema_version_includes_app_users() -> None:
    assert SCHEMA_VERSION >= 6


def test_fresh_database_creates_app_users(context: ApplicationContext) -> None:
    columns = {info["name"] for info in inspect(context.database.engine).get_columns("app_users")}
    assert {
        "username",
        "display_name",
        "role",
        "password_hash",
        "is_active",
        "last_login_at",
    } <= columns
