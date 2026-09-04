"""PHASE 05: pay-period boundaries, DST behaviour and timesheet calculation."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from clockmanager.domain.payroll import (
    PayPeriod,
    PaySchedule,
    PayScheduleType,
    PunchInput,
    TimesheetRules,
    build_timesheet,
    format_hours,
    interpret_naive,
    pay_period_for,
    pay_periods_between,
    summarise_day,
)

UTC = ZoneInfo("UTC")
NY = ZoneInfo("America/New_York")


def _punch(hour: int, minute: int, punch: int, *, day: date = date(2026, 3, 9)) -> PunchInput:
    return PunchInput(
        occurred_at=datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC),
        punch=punch,
    )


# -- pay periods ---------------------------------------------------------------


def test_weekly_period_contains_anchor_week() -> None:
    schedule = PaySchedule(schedule_type=PayScheduleType.WEEKLY, anchor_date=date(2026, 8, 31))
    period = pay_period_for(date(2026, 9, 4), schedule)
    assert (period.start, period.end) == (date(2026, 8, 31), date(2026, 9, 6))


def test_biweekly_period_is_14_days() -> None:
    schedule = PaySchedule(schedule_type=PayScheduleType.BIWEEKLY, anchor_date=date(2026, 8, 31))
    period = pay_period_for(date(2026, 9, 13), schedule)
    assert (period.start, period.end) == (date(2026, 8, 31), date(2026, 9, 13))
    following = pay_period_for(date(2026, 9, 14), schedule)
    assert (following.start, following.end) == (date(2026, 9, 14), date(2026, 9, 27))


def test_semimonthly_splits_on_15th_and_month_end() -> None:
    schedule = PaySchedule(schedule_type=PayScheduleType.SEMIMONTHLY, anchor_date=date(2026, 2, 10))
    assert pay_period_for(date(2026, 2, 15), schedule) == PayPeriod(
        date(2026, 2, 1), date(2026, 2, 15)
    )
    assert pay_period_for(date(2026, 2, 16), schedule) == PayPeriod(
        date(2026, 2, 16), date(2026, 2, 28)
    )
    assert pay_period_for(date(2026, 9, 30), schedule) == PayPeriod(
        date(2026, 9, 16), date(2026, 9, 30)
    )


def test_monthly_covers_february_leap_edge() -> None:
    schedule = PaySchedule(schedule_type=PayScheduleType.MONTHLY, anchor_date=date(2026, 1, 15))
    assert pay_period_for(date(2026, 2, 1), schedule) == PayPeriod(
        date(2026, 2, 1), date(2026, 2, 28)
    )


def test_periods_between_tiles_without_gaps() -> None:
    schedule = PaySchedule(schedule_type=PayScheduleType.WEEKLY, anchor_date=date(2026, 8, 31))
    periods = pay_periods_between(date(2026, 9, 1), date(2026, 9, 15), schedule)
    assert [p.start for p in periods] == [date(2026, 8, 31), date(2026, 9, 7), date(2026, 9, 14)]
    assert periods[0].end + __import__("datetime").timedelta(days=1) == periods[1].start


def test_unknown_timezone_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown timezone"):
        PaySchedule(
            schedule_type=PayScheduleType.WEEKLY,
            anchor_date=date(2026, 8, 31),
            timezone="Mars/Olympus",
        )


# -- daily calculation ----------------------------------------------------------


def test_normal_day_pairs_in_out() -> None:
    rules = TimesheetRules()
    day = summarise_day(
        [_punch(9, 0, 0), _punch(12, 0, 1), _punch(13, 0, 0), _punch(17, 0, 1)],
        date(2026, 3, 9),
        rules,
    )
    assert day.worked_seconds == 7 * 3600
    assert day.first_in is not None and (day.first_in.hour, day.first_in.minute) == (9, 0)
    assert day.last_out is not None and (day.last_out.hour, day.last_out.minute) == (17, 0)
    assert day.missing_punches == 0


def test_trailing_in_counts_as_missing() -> None:
    rules = TimesheetRules()
    day = summarise_day([_punch(9, 0, 0)], date(2026, 3, 9), rules)
    assert day.missing_punches == 1
    assert day.worked_seconds == 0


def test_leading_out_counts_as_missing() -> None:
    rules = TimesheetRules()
    day = summarise_day([_punch(17, 0, 1)], date(2026, 3, 9), rules)
    assert day.missing_punches == 1
    assert day.worked_seconds == 0


def test_duplicates_flagged_and_excluded() -> None:
    rules = TimesheetRules(duplicate_interval_seconds=120)
    day = summarise_day(
        [_punch(9, 0, 0), _punch(9, 1, 0), _punch(17, 0, 1)],
        date(2026, 3, 9),
        rules,
    )
    assert day.duplicate_punches == 1
    assert day.worked_seconds == 8 * 3600


def test_excessive_shift_flagged_but_still_counted() -> None:
    rules = TimesheetRules(max_shift_hours=8.0)
    day = summarise_day([_punch(6, 0, 0), _punch(20, 0, 1)], date(2026, 3, 9), rules)
    assert day.excessive_shifts == 1
    assert day.worked_seconds == 14 * 3600


def test_overnight_pair_attributed_to_in_day() -> None:
    rules = TimesheetRules()
    punches = [
        PunchInput(occurred_at=datetime(2026, 3, 9, 22, 0, tzinfo=UTC), punch=0),
        PunchInput(occurred_at=datetime(2026, 3, 10, 6, 0, tzinfo=UTC), punch=1),
    ]
    schedule = PaySchedule(schedule_type=PayScheduleType.WEEKLY, anchor_date=date(2026, 3, 9))
    sheet = build_timesheet(
        employee_user_id="EMP-001",
        punches=punches,
        period=PayPeriod(date(2026, 3, 9), date(2026, 3, 15)),
        schedule=schedule,
        rules=rules,
    )
    monday = sheet.summary.days[0]
    tuesday = sheet.summary.days[1]
    assert monday.worked_seconds == 8 * 3600
    assert monday.overnight_shifts == 1
    assert tuesday.worked_seconds == 0


def test_unknown_punch_never_paired() -> None:
    rules = TimesheetRules()
    day = summarise_day(
        [_punch(9, 0, 0), _punch(12, 0, 7), _punch(17, 0, 1)],
        date(2026, 3, 9),
        rules,
    )
    # IN->OUT still pairs (8h); the unknown value is preserved as missing.
    assert day.worked_seconds == 8 * 3600
    assert day.missing_punches == 1


def test_daily_overtime_split() -> None:
    rules = TimesheetRules(daily_overtime_hours=8.0)
    day = summarise_day([_punch(8, 0, 0), _punch(18, 0, 1)], date(2026, 3, 9), rules)
    assert day.regular_seconds == 8 * 3600
    assert day.overtime_seconds == 2 * 3600


def test_weekly_overtime_when_no_daily_rule() -> None:
    rules = TimesheetRules(weekly_overtime_hours=40.0)
    schedule = PaySchedule(schedule_type=PayScheduleType.WEEKLY, anchor_date=date(2026, 3, 9))
    punches = [
        PunchInput(
            occurred_at=datetime(2026, 3, d, 8, 0, tzinfo=UTC),
            punch=0,
        )
        for d in (9, 10, 11, 12, 13)
    ] + [
        PunchInput(
            occurred_at=datetime(2026, 3, d, 18, 0, tzinfo=UTC),
            punch=1,
        )
        for d in (9, 10, 11, 12, 13)
    ]
    sheet = build_timesheet(
        employee_user_id="EMP-001",
        punches=punches,
        period=PayPeriod(date(2026, 3, 9), date(2026, 3, 15)),
        schedule=schedule,
        rules=rules,
    )
    assert sheet.summary.total_seconds == 50 * 3600
    assert sheet.summary.overtime_seconds == 10 * 3600


def test_daily_rule_wins_over_weekly_for_period_total() -> None:
    rules = TimesheetRules(daily_overtime_hours=8.0, weekly_overtime_hours=40.0)
    schedule = PaySchedule(schedule_type=PayScheduleType.WEEKLY, anchor_date=date(2026, 3, 9))
    punches = [
        PunchInput(occurred_at=datetime(2026, 3, d, 8, 0, tzinfo=UTC), punch=0) for d in (9, 10)
    ] + [PunchInput(occurred_at=datetime(2026, 3, d, 18, 0, tzinfo=UTC), punch=1) for d in (9, 10)]
    sheet = build_timesheet(
        employee_user_id="EMP-001",
        punches=punches,
        period=PayPeriod(date(2026, 3, 9), date(2026, 3, 15)),
        schedule=schedule,
        rules=rules,
    )
    # 2 x 2h daily overtime; the weekly rule must not add more on top.
    assert sheet.summary.overtime_seconds == 4 * 3600


# -- timezone / DST --------------------------------------------------------------


def test_interpret_naive_attaches_zone_without_utc_labelling() -> None:
    aware = interpret_naive(
        datetime(2026, 1, 15, 9, 0),  # noqa: DTZ001 - naive input is the case under test
        "America/New_York",
    )
    assert aware.utcoffset() is not None
    assert aware.isoformat() == "2026-01-15T09:00:00-05:00"


def test_spring_forward_day_still_totals_wall_time() -> None:
    """2026-03-08: New York springs forward (02:00 -> 03:00).

    A 00:30-03:30 wall-time shift spans only 2 elapsed hours: the 02:00 hour
    never happened. The engine uses real elapsed time (timeline arithmetic
    on aware datetimes); a naive wall-clock subtraction would report 3.
    """
    rules = TimesheetRules()
    day_date = date(2026, 3, 8)
    # Naive inputs: device-local wall time is naive by construction (STATUS.md).
    morning = datetime(2026, 3, 8, 0, 30)  # noqa: DTZ001
    afternoon = datetime(2026, 3, 8, 3, 30)  # noqa: DTZ001
    punches = [
        PunchInput(occurred_at=interpret_naive(morning, "America/New_York"), punch=0),
        PunchInput(occurred_at=interpret_naive(afternoon, "America/New_York"), punch=1),
    ]
    day = summarise_day(punches, day_date, rules)
    assert day.worked_seconds == 2 * 3600


def test_fall_back_day_uses_elapsed_time() -> None:
    """2026-11-01: New York falls back (02:00 -> 01:00).

    A 00:30-03:30 wall-time shift spans 4 elapsed hours: the 01:00 hour
    happened twice. Elapsed-time arithmetic is the documented choice; a
    wall-clock subtraction would report 3.
    """
    rules = TimesheetRules()
    # Naive inputs: device-local wall time is naive by construction (STATUS.md).
    morning = datetime(2026, 11, 1, 0, 30)  # noqa: DTZ001
    afternoon = datetime(2026, 11, 1, 3, 30)  # noqa: DTZ001
    punches = [
        PunchInput(occurred_at=interpret_naive(morning, "America/New_York"), punch=0),
        PunchInput(occurred_at=interpret_naive(afternoon, "America/New_York"), punch=1),
    ]
    day = summarise_day(punches, date(2026, 11, 1), rules)
    assert day.worked_seconds == 4 * 3600


def test_pay_period_boundaries_use_schedule_timezone() -> None:
    """A punch at 23:30 New York wall time on Sep 4 belongs to Sep 4 locally."""
    schedule = PaySchedule(
        schedule_type=PayScheduleType.WEEKLY,
        anchor_date=date(2026, 8, 31),
        timezone="America/New_York",
    )
    rules = TimesheetRules()
    # Naive input: device-local wall time is naive by construction (STATUS.md).
    wall = datetime(2026, 9, 4, 23, 30)  # noqa: DTZ001
    punch = PunchInput(occurred_at=interpret_naive(wall, "America/New_York"), punch=0)
    period = pay_period_for(date(2026, 9, 4), schedule)
    sheet = build_timesheet(
        employee_user_id="EMP-001",
        punches=[punch],
        period=period,
        schedule=schedule,
        rules=rules,
    )
    friday = next(d for d in sheet.summary.days if d.day == date(2026, 9, 4))
    assert friday.missing_punches == 1  # trailing IN lands on Sep 4, not Sep 5


def test_naive_punch_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        PunchInput(
            occurred_at=datetime(2026, 3, 9, 9, 0),  # noqa: DTZ001 - naive input is the case under test
            punch=0,
        )


# -- display ---------------------------------------------------------------------


def test_format_hours_decimal_and_hhmm() -> None:
    assert format_hours(8.5, decimal=True) == "8.50"
    assert format_hours(8.5, decimal=False) == "8:30"
    assert format_hours(7 + 1 / 60, decimal=False) == "7:01"
