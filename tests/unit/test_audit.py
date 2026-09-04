"""Audit log tests.

The audit log is the record of what this application did to a device. These
tests cover the two properties that make it worth having: it records refusals
and failures as well as successes, and it cannot contain a credential.
"""

from __future__ import annotations

from sqlalchemy import text

from clockmanager.persistence.models import SCHEMA_VERSION
from clockmanager.services.application import ApplicationContext
from clockmanager.services.audit import (
    AuditAction,
    AuditOutcome,
    AuditService,
    current_actor,
)


def test_schema_version_includes_the_audit_table() -> None:
    assert SCHEMA_VERSION >= 3


def test_records_and_reads_back_an_entry(context: ApplicationContext) -> None:
    service = AuditService(context.database, actor="tester")
    service.record(
        AuditAction.USER_CREATE,
        AuditOutcome.SUCCEEDED,
        detail="User ID: -> EMP-001",
        device_name="Bench clock",
        target="EMP-001",
        target_uid=7,
    )

    entries = service.recent()
    assert len(entries) == 1
    assert entries[0].actor == "tester"
    assert entries[0].action == "user.create"
    assert entries[0].outcome == "succeeded"
    assert entries[0].target_uid == 7


def test_records_refusals_and_failures_not_only_successes(
    context: ApplicationContext,
) -> None:
    """An audit log of successes alone cannot answer what it exists for."""
    service = AuditService(context.database, actor="tester")
    service.record(AuditAction.USER_DELETE, AuditOutcome.REFUSED, detail="Not confirmed")
    service.record(AuditAction.USER_UPDATE, AuditOutcome.FAILED, detail="Device rejected it")

    outcomes = {entry.outcome for entry in service.recent()}
    assert outcomes == {"refused", "failed"}


def test_entries_are_returned_newest_first(context: ApplicationContext) -> None:
    service = AuditService(context.database, actor="tester")
    for index in range(3):
        service.record(AuditAction.USER_CREATE, AuditOutcome.SUCCEEDED, target=f"EMP-{index}")

    assert next(entry.target for entry in service.recent()) == "EMP-2"


def test_detail_is_redacted_before_it_is_stored(context: ApplicationContext) -> None:
    """SECURITY.md: nothing credential-shaped may reach the table."""
    service = AuditService(context.database, actor="tester")
    service.record(
        AuditAction.USER_UPDATE,
        AuditOutcome.SUCCEEDED,
        detail="changed pin=4711 for the user",
    )

    stored = service.recent()[0].detail
    assert "4711" not in stored
    assert "REDACTED" in stored


def test_detail_is_truncated_to_the_column_width(context: ApplicationContext) -> None:
    service = AuditService(context.database, actor="tester")
    service.record(AuditAction.USER_UPDATE, AuditOutcome.SUCCEEDED, detail="x" * 5000)
    assert len(service.recent()[0].detail) <= 2000


def test_a_storage_failure_does_not_mask_the_operation(
    context: ApplicationContext,
) -> None:
    """An audit write must never replace the result the caller is handling."""
    context.database.dispose()
    service = AuditService(context.database, actor="tester")

    # No exception escapes, even though the database is unusable.
    entry = service.record(AuditAction.USER_DELETE, AuditOutcome.FAILED, detail="boom")
    assert entry.outcome == "failed"


def test_the_repository_offers_no_way_to_edit_or_remove_entries() -> None:
    from clockmanager.persistence.repositories import AuditRepository

    public = {name for name in dir(AuditRepository) if not name.startswith("_")}
    assert public == {"add", "recent", "count", "list_filtered"}


def test_audit_rows_survive_removing_the_device(context: ApplicationContext) -> None:
    """Deleting a device profile must not erase the record of what was done."""
    from clockmanager.services.devices import DeviceProfile

    saved = context.devices.save_profile(DeviceProfile(name="Bench clock", host="192.0.2.10"))
    service = AuditService(context.database, actor="tester")
    service.record(
        AuditAction.USER_DELETE,
        AuditOutcome.SUCCEEDED,
        device_id=saved.device_id,
        device_name=saved.name,
        target="EMP-001",
    )

    assert saved.device_id is not None
    context.devices.delete_profile(saved.device_id)

    entries = service.recent()
    assert len(entries) == 1
    assert entries[0].device_name == "Bench clock"


def test_current_actor_returns_something_identifying() -> None:
    assert current_actor()


def test_audit_table_has_no_credential_shaped_column(context: ApplicationContext) -> None:
    """SECURITY.md: no column may exist that could hold a credential."""
    with context.database.session() as session:
        columns = {
            str(row[1]).lower()
            for row in session.execute(text("PRAGMA table_info(audit_events)")).all()
        }

    for forbidden in ("password", "pin", "card", "template", "secret", "credential"):
        assert not any(forbidden in column for column in columns)


def test_status_reports_the_audit_entry_count(context: ApplicationContext) -> None:
    AuditService(context.database).record(AuditAction.USER_CREATE, AuditOutcome.SUCCEEDED)
    assert context.status().audit_entries == 1
