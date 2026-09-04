"""Derived timesheet service (PHASE 05).

Timesheets are computed on demand from immutable stored attendance and are
never written back: recalculating the same period from the same punches
always yields the same result. The service resolves an employee's punches
through the canonical user ID plus every linked device user ID, interprets
the naive device-local wall times in the pay schedule's timezone, and runs
the pure domain calculation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.auth import Permission, Role, require
from clockmanager.domain.payroll import (
    PayPeriod,
    PaySchedule,
    PayScheduleType,
    PunchInput,
    Timesheet,
    TimesheetRules,
    build_timesheet,
    interpret_naive,
    pay_period_for,
    pay_periods_between,
)
from clockmanager.errors import ClockManagerError
from clockmanager.persistence.database import Database
from clockmanager.persistence.repositories import (
    AttendanceRepository,
    PayScheduleRepository,
)
from clockmanager.services.employees import EmployeeService

__all__ = [
    "PayScheduleProfile",
    "TimesheetRequest",
    "TimesheetService",
]

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PayScheduleProfile:
    """Stored pay schedule with its rules, ready to display."""

    schedule_id: int
    name: str
    schedule_type: str
    anchor_date: date
    timezone: str
    day_cutoff_hour: int
    duplicate_interval_seconds: int
    max_shift_hours: float
    display_decimal: bool
    daily_overtime_hours: float | None
    weekly_overtime_hours: float | None
    is_active: bool

    def to_schedule(self) -> PaySchedule:
        return PaySchedule(
            schedule_type=PayScheduleType(self.schedule_type),
            anchor_date=self.anchor_date,
            timezone=self.timezone,
        )

    def to_rules(self) -> TimesheetRules:
        return TimesheetRules(
            duplicate_interval_seconds=self.duplicate_interval_seconds,
            max_shift_hours=self.max_shift_hours,
            day_cutoff_hour=self.day_cutoff_hour,
            daily_overtime_hours=self.daily_overtime_hours,
            weekly_overtime_hours=self.weekly_overtime_hours,
            display_decimal=self.display_decimal,
        )


@dataclass(frozen=True, slots=True)
class TimesheetRequest:
    """What to calculate: an employee and an inclusive calendar-day period."""

    employee_id: int
    start: date
    end: date


class TimesheetService:
    """Pay-schedule administration plus derived timesheet calculation."""

    def __init__(self, database: Database, employees: EmployeeService) -> None:
        self._database = database
        self._employees = employees

    # -- pay schedules ----------------------------------------------------------

    def list_schedules(self) -> list[PayScheduleProfile]:
        with self._database.session() as session:
            return [self._to_profile(row) for row in PayScheduleRepository(session).list_all()]

    def get_active_schedule(self) -> PayScheduleProfile | None:
        with self._database.session() as session:
            row = PayScheduleRepository(session).get_active()
            return None if row is None else self._to_profile(row)

    def ensure_default_schedule(self) -> PayScheduleProfile:
        """Return the active schedule, creating a weekly UTC one if none exists."""
        existing = self.get_active_schedule()
        if existing is not None:
            return existing
        monday = datetime.now(UTC).date() - timedelta(days=datetime.now(UTC).weekday())
        return self.create_schedule(
            name="Default weekly",
            schedule_type=PayScheduleType.WEEKLY,
            anchor_date=monday,
            timezone="UTC",
            activate=True,
        )

    def create_schedule(
        self,
        *,
        requester_role: Role | str | None = None,
        name: str,
        schedule_type: PayScheduleType,
        anchor_date: date,
        timezone: str = "UTC",
        day_cutoff_hour: int = 0,
        duplicate_interval_seconds: int = 60,
        max_shift_hours: float = 16.0,
        display_decimal: bool = False,
        daily_overtime_hours: float | None = None,
        weekly_overtime_hours: float | None = None,
        activate: bool = False,
    ) -> PayScheduleProfile:
        if requester_role is not None:
            require(requester_role, Permission.MANAGE_PAYROLL)
        if not name.strip():
            raise ClockManagerError("Pay schedule name must not be empty.")
        # Validate eagerly through the domain types so bad config fails here,
        # not halfway through a timesheet build.
        schedule = PaySchedule(
            schedule_type=schedule_type, anchor_date=anchor_date, timezone=timezone
        )
        rules = TimesheetRules(
            duplicate_interval_seconds=duplicate_interval_seconds,
            max_shift_hours=max_shift_hours,
            day_cutoff_hour=day_cutoff_hour,
            daily_overtime_hours=daily_overtime_hours,
            weekly_overtime_hours=weekly_overtime_hours,
            display_decimal=display_decimal,
        )
        _ = (schedule, rules)
        from clockmanager.persistence.models import PayScheduleRecord

        with self._database.session() as session:
            repo = PayScheduleRepository(session)
            row = repo.add(
                PayScheduleRecord(
                    name=name.strip(),
                    schedule_type=schedule_type.value,
                    anchor_date=anchor_date,
                    timezone=timezone,
                    day_cutoff_hour=day_cutoff_hour,
                    duplicate_interval_seconds=duplicate_interval_seconds,
                    max_shift_hours=max_shift_hours,
                    display_decimal=display_decimal,
                    daily_overtime_hours=daily_overtime_hours,
                    weekly_overtime_hours=weekly_overtime_hours,
                    is_active=False,
                )
            )
            session.flush()
            schedule_id = row.id
            if activate or repo.get_active() is None:
                repo.set_active(schedule_id)
                _logger.info("Activated pay schedule", extra={"schedule_name": name})
            session.expunge(row)
        profile = self.get_schedule(schedule_id)
        assert profile is not None
        return profile

    def get_schedule(self, schedule_id: int) -> PayScheduleProfile | None:
        with self._database.session() as session:
            row = PayScheduleRepository(session).get(schedule_id)
            return None if row is None else self._to_profile(row)

    def activate_schedule(
        self, schedule_id: int, *, requester_role: Role | str | None = None
    ) -> PayScheduleProfile:
        """Make one schedule the active one.

        ``requester_role`` enforces the payroll permission; ``None`` keeps the
        legacy path for callers without an interactive identity, matching
        every other service.
        """
        if requester_role is not None:
            require(requester_role, Permission.MANAGE_PAYROLL)
        with self._database.session() as session:
            row = PayScheduleRepository(session).set_active(schedule_id)
            if row is None:
                raise ClockManagerError(f"Unknown pay schedule id {schedule_id}")
            session.flush()
            return self._to_profile(row)

    def period_for(self, day: date, schedule: PayScheduleProfile | None = None) -> PayPeriod:
        active = schedule or self.ensure_default_schedule()
        return pay_period_for(day, active.to_schedule())

    def periods_between(
        self, start: date, end: date, schedule: PayScheduleProfile | None = None
    ) -> list[PayPeriod]:
        active = schedule or self.ensure_default_schedule()
        return pay_periods_between(start, end, active.to_schedule())

    # -- timesheets --------------------------------------------------------------

    def build(
        self, request: TimesheetRequest, schedule: PayScheduleProfile | None = None
    ) -> Timesheet:
        """Calculate the derived timesheet for one employee and period."""
        if request.end < request.start:
            raise ClockManagerError("Timesheet end must not precede start.")
        active = schedule or self.ensure_default_schedule()
        pay_schedule = active.to_schedule()
        rules = active.to_rules()
        zone = ZoneInfo(pay_schedule.timezone)

        profile = self._employees.get(request.employee_id)
        if profile is None:
            raise ClockManagerError(f"Unknown employee id {request.employee_id}")
        user_ids = [profile.user_id, *[u for u in profile.device_user_ids if u != profile.user_id]]

        # Query a wall-time window wide enough to catch overnight shifts that
        # start inside the period but end after it (plus one day of margin on
        # each side). Attribution to the IN day happens in the domain layer.
        window_start = datetime.combine(request.start - timedelta(days=1), datetime.min.time())
        window_end = datetime.combine(request.end + timedelta(days=1), datetime.max.time())
        window_start = window_start.replace(tzinfo=None)
        window_end = window_end.replace(tzinfo=None)

        with self._database.session() as session:
            rows = AttendanceRepository(session).list_for_users_in_range(
                user_ids=user_ids, start=window_start, end=window_end
            )
            punches = [
                PunchInput(
                    occurred_at=interpret_naive(row.occurred_at, pay_schedule.timezone),
                    punch=row.punch,
                    status=row.status,
                )
                for row in rows
            ]
        _ = zone
        period = PayPeriod(start=request.start, end=request.end)
        timesheet = build_timesheet(
            employee_user_id=profile.user_id,
            punches=punches,
            period=period,
            schedule=pay_schedule,
            rules=rules,
        )
        _logger.info(
            "Built timesheet",
            extra={
                "employee": profile.user_id,
                "start": request.start.isoformat(),
                "end": request.end.isoformat(),
                "total_seconds": timesheet.summary.total_seconds,
            },
        )
        return timesheet

    def build_for_current_period(self, employee_id: int, *, today: date | None = None) -> Timesheet:
        """Convenience: the timesheet for the period containing ``today``."""
        active = self.ensure_default_schedule()
        moment = today or datetime.now(UTC).date()
        period = pay_period_for(moment, active.to_schedule())
        return self.build(
            TimesheetRequest(employee_id=employee_id, start=period.start, end=period.end)
        )

    @staticmethod
    def _to_profile(row: object) -> PayScheduleProfile:
        from clockmanager.persistence.models import PayScheduleRecord as _Row

        assert isinstance(row, _Row)
        return PayScheduleProfile(
            schedule_id=row.id,
            name=row.name,
            schedule_type=row.schedule_type,
            anchor_date=row.anchor_date,
            timezone=row.timezone,
            day_cutoff_hour=row.day_cutoff_hour,
            duplicate_interval_seconds=row.duplicate_interval_seconds,
            max_shift_hours=row.max_shift_hours,
            display_decimal=row.display_decimal,
            daily_overtime_hours=row.daily_overtime_hours,
            weekly_overtime_hours=row.weekly_overtime_hours,
            is_active=row.is_active,
        )
