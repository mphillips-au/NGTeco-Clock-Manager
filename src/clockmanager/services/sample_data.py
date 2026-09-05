"""Synthetic device data for running the application without hardware.

Used by the mock device factory so the GUI can be developed and demonstrated on
a machine with no clock attached. Everything here is invented: no real name, no
real user ID and no credential of any kind.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from struct import pack

from clockmanager.domain.models import (
    AttendanceEvent,
    DeviceUser,
    FingerprintSlot,
    OperationLogEntry,
)

__all__ = [
    "sample_attendance",
    "sample_fingerprints",
    "sample_operation_log",
    "sample_user_payload",
]

_USER_RECORD_SIZE = 120


def _fixed(text: str, size: int) -> bytes:
    return text.encode("utf-8").ljust(size, b"\x00")


def _user_record(uid: int, user_id: str, first: str, last: str, privilege: int) -> bytes:
    return (
        pack("<H", uid)
        + bytes([privilege])
        + bytes(32)  # credential region left empty in sample data
        + _fixed(first, 24)
        + _fixed(last, 37)
        + _fixed(user_id, 24)
    )


def sample_user_payload() -> bytes:
    """A size-prefixed payload of synthetic 120-byte MB1 user records."""
    records = [
        _user_record(1, "1001", "Ada", "Lovelace", 14),
        _user_record(2, "1002", "Grace", "Hopper", 0),
        _user_record(3, "1003", "Alan", "Turing", 0),
        _user_record(4, "EMP-004", "Katherine", "Johnson", 0),
        _user_record(5, "EMP-005", "Margaret", "Hamilton", 0),
    ]
    for record in records:
        if len(record) != _USER_RECORD_SIZE:  # pragma: no cover - construction guard
            raise AssertionError(f"Sample record is {len(record)} bytes, expected 120")
    body = b"".join(records)
    return pack("<I", len(body)) + body


def sample_attendance(
    users: Sequence[DeviceUser], *, days: int = 5, reference: datetime | None = None
) -> list[AttendanceEvent]:
    """Build a plausible IN/OUT history for ``users``.

    Times are naive, matching what a device reports.

    With no ``reference``, the history ends today: running the application
    against the mock device then shows a populated dashboard and a week of
    trend, the way it looks against a real clock in daily use. Callers that
    need stable timestamps pass ``reference`` explicitly.
    """
    if reference is not None:
        start = reference
    else:
        today = date.today()  # noqa: DTZ011 - device-local business day, not a timestamp
        first_day = today - timedelta(days=days - 1)
        start = datetime(first_day.year, first_day.month, first_day.day, 8, 0, 0)  # noqa: DTZ001
    events: list[AttendanceEvent] = []

    for day in range(days):
        day_start = start + timedelta(days=day)
        for index, user in enumerate(users):
            arrive = day_start + timedelta(minutes=index * 7)
            leave = arrive + timedelta(hours=8, minutes=index * 3)
            events.append(
                AttendanceEvent(
                    user_id=user.user_id,
                    occurred_at=arrive,
                    punch=0,
                    status=0,
                    device_uid=user.device_uid,
                )
            )
            events.append(
                AttendanceEvent(
                    user_id=user.user_id,
                    occurred_at=leave,
                    punch=1,
                    status=0,
                    device_uid=user.device_uid,
                )
            )

    return sorted(events, key=lambda event: event.occurred_at)


def sample_fingerprints() -> list[FingerprintSlot]:
    """Synthetic fingerprint enrolments: metadata only, never a template.

    Two of the five sample users have a finger enrolled and the rest do not,
    so the Users screen shows both states and the "cannot clock in" case is
    visible without hardware. ``template_bytes`` is a plausible length; there
    is no template here, real or invented.
    """
    return [
        FingerprintSlot(device_uid=1, finger_index=6, valid=1, template_bytes=838),
        FingerprintSlot(device_uid=2, finger_index=6, valid=1, template_bytes=828),
        FingerprintSlot(device_uid=2, finger_index=1, valid=1, template_bytes=812),
    ]


def sample_operation_log(reference: datetime | None = None) -> list[OperationLogEntry]:
    """A short synthetic keypad log.

    Operation codes are arbitrary numbers, which is exactly how the real ones
    are shown: their meanings are not established for this model, so the sample
    data must not imply otherwise.
    """
    end = reference if reference is not None else datetime.now()  # noqa: DTZ005
    return [
        OperationLogEntry(
            index=index,
            operation=operation,
            operator_uid=1,
            occurred_at=end - timedelta(hours=hours),
            parameters=(0, 0, 0),
        )
        for index, (operation, hours) in enumerate(((5, 72), (6, 48), (5, 24), (11, 3)))
    ]
