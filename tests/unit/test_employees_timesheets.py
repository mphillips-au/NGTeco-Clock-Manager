"""PHASE 05: employees, pay schedules and derived timesheets at the service level."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from clockmanager.domain.payroll import Employee, PayScheduleType
from clockmanager.errors import ClockManagerError
from clockmanager.persistence.models import AttendanceEventRecord, DeviceRecord
from clockmanager.services.application import ApplicationContext
from clockmanager.services.timesheets import TimesheetRequest


def _device_id(context: ApplicationContext) -> int:
    """A stored device row every attendance/link row can reference."""
    with context.database.session() as session:
        device = DeviceRecord(name="Test clock")
        session.add(device)
        session.flush()
        return device.id


def test_create_and_list_employee(context: ApplicationContext) -> None:
    profile = context.employees.create(
        Employee(user_id="EMP-001", first_name="Ada", last_name="Lovelace", department="IT")
    )
    assert profile.display_name == "Ada Lovelace"
    listed = context.employees.list_employees()
    assert [p.user_id for p in listed] == ["EMP-001"]


def test_duplicate_user_id_refused(context: ApplicationContext) -> None:
    context.employees.create(Employee(user_id="EMP-001"))
    with pytest.raises(ClockManagerError, match="already exists"):
        context.employees.create(Employee(user_id="EMP-001"))


def test_invalid_email_refused_by_domain(context: ApplicationContext) -> None:
    with pytest.raises(ValueError, match="email"):
        context.employees.create(Employee(user_id="EMP-002", email="not-an-email"))


def test_deactivate_and_reactivate(context: ApplicationContext) -> None:
    profile = context.employees.create(Employee(user_id="EMP-001"))
    off = context.employees.set_active(profile.employee_id, active=False)
    assert off.active is False
    assert context.employees.list_employees(include_inactive=False) == []
    on = context.employees.set_active(profile.employee_id, active=True)
    assert on.active is True


def test_device_link_adds_alias_user_ids(context: ApplicationContext) -> None:
    device_id = _device_id(context)
    profile = context.employees.create(Employee(user_id="EMP-001"))
    linked = context.employees.link_device(
        profile.employee_id, device_id=device_id, user_id="ALIAS-9", device_uid=9
    )
    assert "ALIAS-9" in linked.device_user_ids
    assert set(context.employees.user_ids_for(profile.employee_id)) == {"EMP-001", "ALIAS-9"}
    unlinked = context.employees.unlink_device(profile.employee_id, device_id=device_id)
    assert unlinked is not None and unlinked.device_user_ids == ()


def test_employee_changes_are_audited(context: ApplicationContext) -> None:
    profile = context.employees.create(Employee(user_id="EMP-001"))
    context.employees.set_active(profile.employee_id, active=False)
    actions = {entry.action for entry in context.audit.recent()}
    assert "employee.create" in actions
    assert "employee.deactivate" in actions


def _store_punch(
    context: ApplicationContext,
    device_id: int,
    *,
    user_id: str,
    moment: datetime,
    punch: int,
) -> None:
    with context.database.session() as session:
        session.add(
            AttendanceEventRecord(
                device_id=device_id,
                device_uid=None,
                user_id=user_id,
                occurred_at=moment,  # naive device-local wall time, by design
                punch=punch,
                status=0,
                received_at=moment,
                source="historical",
                event_key=f"{user_id}-{moment.isoformat()}-{punch}",
                employee_name=None,
            )
        )


def test_timesheet_derived_from_stored_punches(context: ApplicationContext) -> None:
    device_id = _device_id(context)
    profile = context.employees.create(Employee(user_id="EMP-001"))
    # Naive moments: device-local wall time is naive by construction (STATUS.md).
    monday = datetime(2026, 9, 7, 9, 0)  # noqa: DTZ001
    evening = datetime(2026, 9, 7, 17, 0)  # noqa: DTZ001
    _store_punch(context, device_id, user_id="EMP-001", moment=monday, punch=0)
    _store_punch(context, device_id, user_id="EMP-001", moment=evening, punch=1)
    # A punch for someone else must not leak into this timesheet.
    _store_punch(context, device_id, user_id="EMP-999", moment=monday, punch=0)

    schedule = context.timesheets.ensure_default_schedule()
    assert schedule.timezone == "UTC"
    sheet = context.timesheets.build(
        TimesheetRequest(
            employee_id=profile.employee_id, start=date(2026, 9, 7), end=date(2026, 9, 13)
        ),
        schedule,
    )
    assert sheet.summary.total_seconds == 8 * 3600
    assert sheet.summary.overtime_seconds == 0


def test_timesheet_uses_linked_alias_ids(context: ApplicationContext) -> None:
    device_id = _device_id(context)
    profile = context.employees.create(Employee(user_id="EMP-001"))
    context.employees.link_device(profile.employee_id, device_id=device_id, user_id="ALIAS-9")
    morning = datetime(2026, 9, 7, 9, 0)  # noqa: DTZ001 - device-local wall time is naive
    noon = datetime(2026, 9, 7, 12, 0)  # noqa: DTZ001 - device-local wall time is naive
    _store_punch(context, device_id, user_id="ALIAS-9", moment=morning, punch=0)
    _store_punch(context, device_id, user_id="ALIAS-9", moment=noon, punch=1)
    sheet = context.timesheets.build(
        TimesheetRequest(
            employee_id=profile.employee_id, start=date(2026, 9, 7), end=date(2026, 9, 13)
        )
    )
    assert sheet.summary.total_seconds == 3 * 3600


def test_timesheet_recalculates_after_new_punches(context: ApplicationContext) -> None:
    """Timesheets are derived: storing more punches changes the next build."""
    device_id = _device_id(context)
    profile = context.employees.create(Employee(user_id="EMP-001"))
    request = TimesheetRequest(
        employee_id=profile.employee_id, start=date(2026, 9, 7), end=date(2026, 9, 13)
    )
    assert context.timesheets.build(request).summary.total_seconds == 0
    morning = datetime(2026, 9, 7, 9, 0)  # noqa: DTZ001 - device-local wall time is naive
    evening = datetime(2026, 9, 7, 17, 0)  # noqa: DTZ001 - device-local wall time is naive
    _store_punch(context, device_id, user_id="EMP-001", moment=morning, punch=0)
    _store_punch(context, device_id, user_id="EMP-001", moment=evening, punch=1)
    assert context.timesheets.build(request).summary.total_seconds == 8 * 3600


def test_timesheet_respects_daily_overtime_schedule(context: ApplicationContext) -> None:
    device_id = _device_id(context)
    profile = context.employees.create(Employee(user_id="EMP-001"))
    schedule = context.timesheets.create_schedule(
        name="Daily OT 8h",
        schedule_type=PayScheduleType.WEEKLY,
        anchor_date=date(2026, 9, 7),
        timezone="UTC",
        daily_overtime_hours=8.0,
        activate=True,
    )
    morning = datetime(2026, 9, 7, 8, 0)  # noqa: DTZ001 - device-local wall time is naive
    evening = datetime(2026, 9, 7, 18, 0)  # noqa: DTZ001 - device-local wall time is naive
    _store_punch(context, device_id, user_id="EMP-001", moment=morning, punch=0)
    _store_punch(context, device_id, user_id="EMP-001", moment=evening, punch=1)
    sheet = context.timesheets.build(
        TimesheetRequest(
            employee_id=profile.employee_id, start=date(2026, 9, 7), end=date(2026, 9, 13)
        ),
        schedule,
    )
    assert sheet.summary.overtime_seconds == 2 * 3600


def test_unknown_employee_rejected(context: ApplicationContext) -> None:
    with pytest.raises(ClockManagerError, match="Unknown employee"):
        context.timesheets.build(
            TimesheetRequest(employee_id=9999, start=date(2026, 9, 7), end=date(2026, 9, 13))
        )


def test_update_employee_changes_fields(context: ApplicationContext) -> None:
    profile = context.employees.create(Employee(user_id="EMP-001", first_name="Ada"))
    updated = context.employees.update(
        profile.employee_id,
        Employee(user_id="EMP-001", first_name="Ada", last_name="Lovelace", department="IT"),
    )
    assert updated.display_name == "Ada Lovelace"
    assert updated.department == "IT"


def test_update_to_taken_user_id_refused(context: ApplicationContext) -> None:
    first = context.employees.create(Employee(user_id="EMP-001"))
    context.employees.create(Employee(user_id="EMP-002"))
    with pytest.raises(ClockManagerError, match="already uses"):
        context.employees.update(first.employee_id, Employee(user_id="EMP-002"))


def test_unknown_employee_operations_rejected(context: ApplicationContext) -> None:
    assert context.employees.get(9999) is None
    with pytest.raises(ClockManagerError, match="Unknown employee"):
        context.employees.user_ids_for(9999)
    with pytest.raises(ClockManagerError, match="Unknown employee"):
        context.employees.update(9999, Employee(user_id="EMP-X"))
    with pytest.raises(ClockManagerError, match="Unknown employee"):
        context.employees.set_active(9999, active=False)
    with pytest.raises(ClockManagerError, match="Unknown employee"):
        context.employees.link_device(9999, device_id=1, user_id="ALIAS-9")
    with pytest.raises(ClockManagerError, match="Unknown employee"):
        context.employees.unlink_device(9999, device_id=1)
    assert context.employees.count() == 0


def test_link_rejects_empty_user_id(context: ApplicationContext) -> None:
    profile = context.employees.create(Employee(user_id="EMP-001"))
    with pytest.raises(ClockManagerError, match="must not be empty"):
        context.employees.link_device(profile.employee_id, device_id=1, user_id="   ")


def test_relink_same_device_updates_mapping(context: ApplicationContext) -> None:
    device_id = _device_id(context)
    profile = context.employees.create(Employee(user_id="EMP-001"))
    context.employees.link_device(profile.employee_id, device_id=device_id, user_id="OLD-1")
    relinked = context.employees.link_device(
        profile.employee_id, device_id=device_id, user_id="NEW-1"
    )
    assert relinked.device_user_ids == ("NEW-1",)
    assert set(context.employees.user_ids_for(profile.employee_id)) == {"EMP-001", "NEW-1"}


def test_schedule_activation_switches_active(context: ApplicationContext) -> None:
    first = context.timesheets.create_schedule(
        name="Weekly",
        schedule_type=PayScheduleType.WEEKLY,
        anchor_date=date(2026, 9, 7),
    )
    second = context.timesheets.create_schedule(
        name="Monthly",
        schedule_type=PayScheduleType.MONTHLY,
        anchor_date=date(2026, 9, 1),
    )
    assert context.timesheets.get_active_schedule() is not None
    activated = context.timesheets.activate_schedule(second.schedule_id)
    assert activated.is_active is True
    assert context.timesheets.get_active_schedule() is not None
    assert context.timesheets.get_active_schedule().schedule_id == second.schedule_id
    assert context.timesheets.get_schedule(first.schedule_id) is not None
    with pytest.raises(ClockManagerError, match="Unknown pay schedule"):
        context.timesheets.activate_schedule(9999)
    with pytest.raises(ClockManagerError, match="must not be empty"):
        context.timesheets.create_schedule(
            name="   ", schedule_type=PayScheduleType.WEEKLY, anchor_date=date(2026, 9, 7)
        )
    periods = context.timesheets.periods_between(
        date(2026, 9, 7), date(2026, 9, 20), context.timesheets.get_schedule(first.schedule_id)
    )
    assert [p.start for p in periods] == [date(2026, 9, 7), date(2026, 9, 14)]
    current = context.timesheets.build_for_current_period(
        context.employees.create(Employee(user_id="EMP-001")).employee_id
    )
    assert current.summary.total_seconds == 0  # no punches stored yet
