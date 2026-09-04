"""Business layer above raw attendance: employees, pay periods, timesheets.

PHASE 05. Pure Python: no PySide6, no SQLAlchemy, no sockets.

Design notes:

* Raw attendance rows are immutable evidence. Everything here derives from
  copies of them and is recalculable: deleting a timesheet never deletes a
  punch.
* Device timestamps are naive device-local wall time by construction
  (``STATUS.md``). They carry no timezone, so this module never guesses one:
  callers interpret naive values as wall time in an explicit IANA timezone
  via :func:`interpret_naive` before calculating. All calculation inputs are
  timezone-aware datetimes, which makes DST behaviour explicit and testable.
* An overnight shift (IN on day D, OUT after midnight) is attributed to the
  IN day's date and flagged ``overnight``. Splitting at midnight would invent
  a midnight punch the device never recorded.
* Durations are real elapsed time: differences are taken in UTC, because
  subtracting two aware datetimes that share one ``ZoneInfo`` object compares
  wall time and silently ignores DST transitions (CPython treats identical
  ``tzinfo`` as a fixed offset). A 00:30-03:30 shift on spring-forward Sunday
  is 2 hours worked, not 3; on fall-back Sunday it is 4, not 3.
* A "duplicate" punch is a punch that arrives within
  ``duplicate_interval_seconds`` of the previous punch for the same employee.
  It is flagged and excluded from pairing, never deleted.
* Overtime thresholds are independent: a daily threshold produces daily
  overtime, a weekly (Monday--Sunday) threshold produces weekly overtime. The
  pay-period overtime is the sum of daily overtime when a daily threshold is
  configured, else the sum of weekly overtime when a weekly threshold is
  configured, else zero. The rule is documented here so reports cannot double
  count by accident.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "DailySummary",
    "Employee",
    "PayPeriod",
    "PaySchedule",
    "PayScheduleType",
    "PeriodSummary",
    "PunchInput",
    "Timesheet",
    "TimesheetRules",
    "build_timesheet",
    "format_hours",
    "interpret_naive",
    "pay_period_for",
    "pay_periods_between",
    "summarise_day",
]


class PayScheduleType(StrEnum):
    """Supported pay-period cadences."""

    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    SEMIMONTHLY = "semimonthly"
    MONTHLY = "monthly"


@dataclass(frozen=True, slots=True)
class Employee:
    """Business-level employee.

    ``user_id`` is the canonical device user ID (bytes 96:120 of the MB1
    record) used when no per-device mapping says otherwise. Per-device
    differences are resolved by the service layer, not here.
    """

    user_id: str
    first_name: str = ""
    last_name: str = ""
    active: bool = True
    department: str = ""
    position: str = ""
    email: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("user_id must not be empty")
        if self.email and "@" not in self.email:
            raise ValueError("email must contain '@' or be empty")

    @property
    def display_name(self) -> str:
        full = f"{self.first_name} {self.last_name}".strip()
        return full or self.user_id


@dataclass(frozen=True, slots=True)
class PaySchedule:
    """Which calendar days belong to one pay period.

    ``anchor_date`` is the start of a known period for weekly/biweekly (any
    date inside a semimonthly/monthly period works, only the month matters).
    ``timezone`` is an IANA name (e.g. ``"America/New_York"``): period
    boundaries are calendar days in that zone.
    """

    schedule_type: PayScheduleType
    anchor_date: date
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {self.timezone!r}") from exc

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@dataclass(frozen=True, slots=True)
class TimesheetRules:
    """Tunable calculation parameters (the "cutoff / duplicate / shift" row)."""

    #: Punches within this many seconds of the previous punch are duplicates.
    duplicate_interval_seconds: int = 60
    #: A single IN->OUT pair longer than this is flagged excessive.
    max_shift_hours: float = 16.0
    #: Day-boundary cutoff hour (0 = midnight). A shift starting before the
    #: cutoff belongs to the previous calendar day.
    day_cutoff_hour: int = 0
    #: Daily overtime threshold in hours, or None to disable.
    daily_overtime_hours: float | None = None
    #: Weekly (Mon--Sun) overtime threshold in hours, or None to disable.
    weekly_overtime_hours: float | None = None
    #: Display hours as decimal (True) or HH:MM (False).
    display_decimal: bool = False

    def __post_init__(self) -> None:
        if self.duplicate_interval_seconds < 0:
            raise ValueError("duplicate_interval_seconds must not be negative")
        if self.max_shift_hours <= 0:
            raise ValueError("max_shift_hours must be positive")
        if not 0 <= self.day_cutoff_hour <= 23:
            raise ValueError("day_cutoff_hour must be 0..23")
        for label, value in (
            ("daily_overtime_hours", self.daily_overtime_hours),
            ("weekly_overtime_hours", self.weekly_overtime_hours),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{label} must not be negative")


@dataclass(frozen=True, slots=True)
class PayPeriod:
    """One inclusive calendar-day pay period in the schedule's timezone."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("PayPeriod end must not precede start")

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


@dataclass(frozen=True, slots=True)
class PunchInput:
    """One timezone-aware punch to calculate from."""

    occurred_at: datetime
    punch: int  # 0 IN, 1 OUT; other values preserved, never paired
    status: int = 0

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("PunchInput.occurred_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class DailySummary:
    """Derived totals for one calendar day. Recalculable, never stored."""

    day: date
    first_in: datetime | None
    last_out: datetime | None
    worked_seconds: int = 0
    missing_punches: int = 0
    duplicate_punches: int = 0
    excessive_shifts: int = 0
    overnight_shifts: int = 0
    regular_seconds: int = 0
    overtime_seconds: int = 0

    @property
    def worked_hours(self) -> float:
        return self.worked_seconds / 3600.0


@dataclass(frozen=True, slots=True)
class PeriodSummary:
    """Totals across a pay period, plus weekly subtotals."""

    period: PayPeriod
    days: tuple[DailySummary, ...]
    total_seconds: int
    overtime_seconds: int
    weekly_seconds: dict[str, int] = field(default_factory=dict)

    @property
    def total_hours(self) -> float:
        return self.total_seconds / 3600.0

    @property
    def overtime_hours(self) -> float:
        return self.overtime_seconds / 3600.0

    @property
    def regular_seconds(self) -> int:
        return max(0, self.total_seconds - self.overtime_seconds)


@dataclass(frozen=True, slots=True)
class Timesheet:
    """The derived timesheet for one employee and one pay period."""

    employee_user_id: str
    summary: PeriodSummary


def interpret_naive(moment: datetime, timezone: str) -> datetime:
    """Interpret naive device-local wall time in ``timezone``.

    The MB1 transmits no timezone, so the application must be told which zone
    its wall clock lives in. Ambiguous DST-fold times take ``fold=0`` (the
    first occurrence); nonexistent spring-forward times resolve by the
    platform's ZoneInfo behaviour. Both choices are explicit and covered by
    tests, never silent UTC labelling.
    """
    zone = ZoneInfo(timezone)  # raises ZoneInfoNotFoundError for bad names
    if moment.tzinfo is not None:
        return moment.astimezone(zone)
    return moment.replace(tzinfo=zone)


def format_hours(hours: float, *, decimal: bool) -> str:
    """Format hours as decimal (``8.50``) or HH:MM (``8:30``)."""
    if decimal:
        return f"{hours:.2f}"
    sign = "-" if hours < 0 else ""
    total_minutes = round(abs(hours) * 60)
    return f"{sign}{total_minutes // 60}:{total_minutes % 60:02d}"


# -- pay periods ---------------------------------------------------------------


def pay_period_for(day: date, schedule: PaySchedule) -> PayPeriod:
    """Return the pay period containing ``day``."""
    match schedule.schedule_type:
        case PayScheduleType.WEEKLY:
            delta = (day - schedule.anchor_date).days % 7
            start = day - timedelta(days=delta)
            return PayPeriod(start=start, end=start + timedelta(days=6))
        case PayScheduleType.BIWEEKLY:
            delta = (day - schedule.anchor_date).days % 14
            start = day - timedelta(days=delta)
            return PayPeriod(start=start, end=start + timedelta(days=13))
        case PayScheduleType.SEMIMONTHLY:
            if day.day <= 15:
                return PayPeriod(start=day.replace(day=1), end=day.replace(day=15))
            last = _month_last_day(day.year, day.month)
            return PayPeriod(start=day.replace(day=16), end=day.replace(day=last))
        case PayScheduleType.MONTHLY:
            last = _month_last_day(day.year, day.month)
            return PayPeriod(start=day.replace(day=1), end=day.replace(day=last))


def pay_periods_between(start: date, end: date, schedule: PaySchedule) -> list[PayPeriod]:
    """List consecutive pay periods covering ``[start, end]``."""
    if end < start:
        raise ValueError("end must not precede start")
    periods: list[PayPeriod] = []
    cursor = pay_period_for(start, schedule)
    while cursor.start <= end:
        periods.append(cursor)
        cursor = pay_period_for(cursor.end + timedelta(days=1), schedule)
    return periods


def _month_last_day(year: int, month: int) -> int:
    if month == 12:
        return 31
    first_next = date(year + (1 if month == 12 else 0), month + 1 if month < 12 else 1, 1)
    return (first_next - timedelta(days=1)).day


# -- daily calculation ----------------------------------------------------------


def _business_day(moment: datetime, cutoff_hour: int) -> date:
    """Calendar day a punch belongs to, honouring the day-cutoff hour."""
    local_day = moment.date()
    if moment.hour < cutoff_hour:
        return local_day - timedelta(days=1)
    return local_day


def _elapsed_seconds(later: datetime, earlier: datetime) -> float:
    """Real elapsed seconds between two aware datetimes.

    Both operands normally share one ``ZoneInfo`` object, in which case plain
    subtraction compares wall time and ignores DST transitions. Converting to
    UTC first measures what actually elapsed (and honours ``fold`` on
    ambiguous fall-back times).
    """
    return (later.astimezone(UTC) - earlier.astimezone(UTC)).total_seconds()


@dataclass(frozen=True, slots=True)
class _Shift:
    """One IN->OUT pairing (or an IN missing its OUT)."""

    in_time: datetime
    out_time: datetime | None  # None = missing OUT
    seconds: int = 0
    overnight: bool = False
    excessive: bool = False
    broken: bool = False  # clock moved backwards: counts as missing, no seconds


def _pair_all(
    usable: list[PunchInput], rules: TimesheetRules
) -> tuple[list[_Shift], list[PunchInput], list[PunchInput]]:
    """Pair IN->OUT punches in chronological order.

    Returns ``(shifts, lone_outs, unknown_punches)``. A trailing or superseded
    IN becomes a shift with ``out_time=None`` (missing OUT). An OUT with no
    preceding IN is a lone OUT (missing IN). Unknown punch values are
    preserved, never paired. Pairing is global across days so an overnight
    shift (IN before midnight, OUT after) stays one shift attributed to the
    IN day by the caller.
    """
    shifts: list[_Shift] = []
    lone_outs: list[PunchInput] = []
    unknowns: list[PunchInput] = []
    pending: datetime | None = None

    for punch in usable:
        if punch.punch == 0:  # IN
            if pending is not None:
                shifts.append(_Shift(in_time=pending, out_time=None))  # missing OUT
            pending = punch.occurred_at
        elif punch.punch == 1:  # OUT
            if pending is None:
                lone_outs.append(punch)
                continue
            seconds = int(_elapsed_seconds(punch.occurred_at, pending))
            if seconds < 0:
                # Clock moved backwards or bad data: do not invent negative
                # work; count it as missing instead.
                shifts.append(_Shift(in_time=pending, out_time=punch.occurred_at, broken=True))
                pending = None
                continue
            shifts.append(
                _Shift(
                    in_time=pending,
                    out_time=punch.occurred_at,
                    seconds=seconds,
                    overnight=punch.occurred_at.date() != pending.date(),
                    excessive=seconds > int(rules.max_shift_hours * 3600),
                )
            )
            pending = None
        else:
            unknowns.append(punch)

    if pending is not None:
        shifts.append(_Shift(in_time=pending, out_time=None))  # trailing IN
    return shifts, lone_outs, unknowns


def _assemble_day(
    day: date,
    *,
    shifts: list[_Shift],
    lone_outs: list[PunchInput],
    unknowns: list[PunchInput],
    duplicates: int,
    rules: TimesheetRules,
    cutoff_hour: int,
) -> DailySummary:
    """Build one day's summary from globally paired punches.

    Shifts are attributed to their IN day's business day; lone OUTs, unknown
    punches and duplicates to their own business day.
    """
    day_shifts = [s for s in shifts if _business_day(s.in_time, cutoff_hour) == day]
    day_lone = [p for p in lone_outs if _business_day(p.occurred_at, cutoff_hour) == day]
    day_unknown = [p for p in unknowns if _business_day(p.occurred_at, cutoff_hour) == day]

    ins = [s.in_time for s in day_shifts]
    outs = [s.out_time for s in day_shifts if s.out_time is not None and not s.broken]
    worked = sum(s.seconds for s in day_shifts if not s.broken)
    missing = (
        sum(1 for s in day_shifts if s.out_time is None or s.broken)
        + len(day_lone)
        + len(day_unknown)
    )
    regular, overtime = _split_overtime(worked, rules.daily_overtime_hours)
    return DailySummary(
        day=day,
        first_in=min(ins) if ins else None,
        last_out=max(outs) if outs else None,
        worked_seconds=worked,
        missing_punches=missing,
        duplicate_punches=duplicates,
        excessive_shifts=sum(1 for s in day_shifts if s.excessive),
        overnight_shifts=sum(1 for s in day_shifts if s.overnight),
        regular_seconds=regular,
        overtime_seconds=overtime,
    )


def summarise_day(
    punches: list[PunchInput],
    day: date,
    rules: TimesheetRules,
) -> DailySummary:
    """Pair IN->OUT punches for one business day and total them.

    Pairing is chronological and global: the earliest unpaired IN matches the
    next OUT, even across midnight. A trailing IN (or a leading OUT) counts
    as a missing punch. Unknown punch values (not 0/1) are preserved and
    counted as missing, never paired.
    """
    ordered = sorted(punches, key=lambda p: p.occurred_at)
    duplicate_flags = _flag_duplicates(ordered, rules.duplicate_interval_seconds)
    usable = [p for p, dup in zip(ordered, duplicate_flags, strict=True) if not dup]
    duplicates = sum(
        1
        for p, dup in zip(ordered, duplicate_flags, strict=True)
        if dup and _business_day(p.occurred_at, rules.day_cutoff_hour) == day
    )
    shifts, lone_outs, unknowns = _pair_all(usable, rules)
    return _assemble_day(
        day,
        shifts=shifts,
        lone_outs=lone_outs,
        unknowns=unknowns,
        duplicates=duplicates,
        rules=rules,
        cutoff_hour=rules.day_cutoff_hour,
    )


def _flag_duplicates(ordered: list[PunchInput], interval_seconds: int) -> list[bool]:
    """Mark punches within ``interval_seconds`` of the previous punch."""
    flags = [False] * len(ordered)
    for index in range(1, len(ordered)):
        delta = _elapsed_seconds(ordered[index].occurred_at, ordered[index - 1].occurred_at)
        if 0 <= delta <= interval_seconds:
            flags[index] = True
    return flags


def _split_overtime(worked_seconds: int, threshold_hours: float | None) -> tuple[int, int]:
    if threshold_hours is None:
        return worked_seconds, 0
    threshold = int(threshold_hours * 3600)
    if worked_seconds <= threshold:
        return worked_seconds, 0
    return threshold, worked_seconds - threshold


# -- period calculation ----------------------------------------------------------


def build_timesheet(
    *,
    employee_user_id: str,
    punches: list[PunchInput],
    period: PayPeriod,
    schedule: PaySchedule,
    rules: TimesheetRules,
) -> Timesheet:
    """Build the derived timesheet for one employee and one pay period.

    Punches are normalised to the schedule's timezone, paired globally in
    chronological order (so an overnight IN->OUT pair stays one shift), and
    each shift is attributed to its IN day's business day. A shift starting
    on the last day of the period still counts even when its OUT lands after
    midnight outside it. Days with no punches still appear with zero totals
    so a report row exists for every calendar day in the period.
    """
    zone = schedule.zone
    localised = [
        PunchInput(
            occurred_at=punch.occurred_at.astimezone(zone),
            punch=punch.punch,
            status=punch.status,
        )
        for punch in punches
    ]
    # One day of margin on each side so cross-boundary overnight pairs are
    # paired before attribution filters them.
    margin_start = period.start - timedelta(days=1)
    margin_end = period.end + timedelta(days=1)
    windowed = [
        punch
        for punch in localised
        if margin_start <= _business_day(punch.occurred_at, rules.day_cutoff_hour) <= margin_end
    ]
    ordered = sorted(windowed, key=lambda p: p.occurred_at)
    duplicate_flags = _flag_duplicates(ordered, rules.duplicate_interval_seconds)
    usable = [p for p, dup in zip(ordered, duplicate_flags, strict=True) if not dup]
    duplicates_by_day: dict[date, int] = {}
    for punch, dup in zip(ordered, duplicate_flags, strict=True):
        if dup:
            day = _business_day(punch.occurred_at, rules.day_cutoff_hour)
            duplicates_by_day[day] = duplicates_by_day.get(day, 0) + 1
    shifts, lone_outs, unknowns = _pair_all(usable, rules)

    days: list[DailySummary] = []
    cursor = period.start
    while cursor <= period.end:
        days.append(
            _assemble_day(
                cursor,
                shifts=shifts,
                lone_outs=lone_outs,
                unknowns=unknowns,
                duplicates=duplicates_by_day.get(cursor, 0),
                rules=rules,
                cutoff_hour=rules.day_cutoff_hour,
            )
        )
        cursor += timedelta(days=1)

    # Daily overtime already split per day; weekly overtime needs the weekly
    # totals. Period overtime prefers the daily rule when both are set.
    weekly_seconds = _weekly_totals(days)
    if rules.daily_overtime_hours is not None:
        overtime = sum(d.overtime_seconds for d in days)
    elif rules.weekly_overtime_hours is not None:
        overtime = sum(
            _split_overtime(total, rules.weekly_overtime_hours)[1]
            for total in weekly_seconds.values()
        )
    else:
        overtime = 0

    total = sum(d.worked_seconds for d in days)
    return Timesheet(
        employee_user_id=employee_user_id,
        summary=PeriodSummary(
            period=period,
            days=tuple(days),
            total_seconds=total,
            overtime_seconds=overtime,
            weekly_seconds=weekly_seconds,
        ),
    )


def _weekly_totals(days: list[DailySummary]) -> dict[str, int]:
    """Monday-start week key (``YYYY-Www``) to worked seconds."""
    totals: dict[str, int] = {}
    for daily in days:
        iso_year, iso_week, _ = daily.day.isocalendar()
        key = f"{iso_year}-W{iso_week:02d}"
        totals[key] = totals.get(key, 0) + daily.worked_seconds
    return totals


def day_start(moment: datetime, cutoff_hour: int = 0) -> datetime:
    """Start of the business day containing ``moment`` (timezone-aware)."""
    day = _business_day(moment, cutoff_hour)
    midnight = datetime.combine(day, time(cutoff_hour, 0), tzinfo=moment.tzinfo)
    if cutoff_hour == 0:
        # Combine with midnight keeps fold=0; preserve the source fold so a
        # repeated 01:30 on fall-back Sunday still maps to the right instance.
        return midnight.replace(fold=moment.fold)
    return midnight
