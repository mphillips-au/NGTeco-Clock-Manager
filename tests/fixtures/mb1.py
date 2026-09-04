"""Builders for sanitized NG-MB1 protocol payloads.

These construct byte payloads matching the layouts verified in ``PROTOCOL.md``
so parsers can be tested without a device and without any real credential.
"""

from __future__ import annotations

from datetime import datetime
from struct import pack

from clockmanager.protocol.constants import (
    ADMIN_PRIVILEGE,
    EMPLOYEE_PRIVILEGE,
    MB1_USER_RECORD_SIZE,
)

__all__ = [
    "ADMIN_PRIVILEGE",
    "CREDENTIAL_MARKER_BYTE",
    "EMPLOYEE_PRIVILEGE",
    "build_attendance_payload",
    "build_attendance_record",
    "build_live_event",
    "build_user_payload",
    "build_user_record",
    "encode_zk_time",
    "sample_users",
]

#: Filler for the credential region. Deliberately not a plausible PIN — the
#: tests only ever assert that this region is NOT disclosed.
CREDENTIAL_MARKER_BYTE = 0xAB


def _fixed(text: str, size: int, *, encoding: str = "utf-8") -> bytes:
    """Encode ``text`` NUL-padded to exactly ``size`` bytes."""
    raw = text.encode(encoding)
    if len(raw) > size:
        raise ValueError(f"{text!r} does not fit in {size} bytes")
    return raw.ljust(size, b"\x00")


def build_user_record(
    *,
    uid: int,
    user_id: str,
    first_name: str = "",
    last_name: str = "",
    privilege: int = EMPLOYEE_PRIVILEGE,
    with_credential: bool = False,
    encoding: str = "utf-8",
) -> bytes:
    """Build one 120-byte MB1 user record.

    Layout: 0:2 UID, 2 privilege, 3:35 credential region, 35:59 first name,
    59:96 last name, 96:120 user ID.
    """
    credential = bytes([CREDENTIAL_MARKER_BYTE]) * 32 if with_credential else bytes(32)
    record = (
        pack("<H", uid)
        + bytes([privilege])
        + credential
        + _fixed(first_name, 24, encoding=encoding)
        + _fixed(last_name, 37, encoding=encoding)
        + _fixed(user_id, 24, encoding=encoding)
    )
    if len(record) != MB1_USER_RECORD_SIZE:  # pragma: no cover - fixture guard
        raise AssertionError(f"Fixture built {len(record)} bytes, expected 120")
    return record


def build_user_payload(records: list[bytes], *, declared_size: int | None = None) -> bytes:
    """Wrap user records in the 4-byte little-endian total-size prefix."""
    body = b"".join(records)
    size = len(body) if declared_size is None else declared_size
    return pack("<I", size) + body


def encode_zk_time(moment: datetime) -> bytes:
    """Encode a datetime into the ZKTeco packed 4-byte form."""
    value = (
        ((moment.year % 100) * 12 * 31 + ((moment.month - 1) * 31) + moment.day - 1)
        * (24 * 60 * 60)
        + (moment.hour * 60 + moment.minute) * 60
        + moment.second
    )
    return pack("<I", value)


def build_attendance_record(
    *,
    size: int,
    uid: int = 0,
    user_id: str = "",
    occurred_at: datetime,
    punch: int = 0,
    status: int = 0,
    encoding: str = "utf-8",
) -> bytes:
    """Build one attendance record in the 8-, 16- or 40-byte ZKTeco form."""
    packed_time = encode_zk_time(occurred_at)
    if size == 8:
        return pack("<HB4sB", uid, status, packed_time, punch)
    if size == 16:
        return pack("<I4sBB2sI", int(user_id or uid), packed_time, status, punch, b"\x00\x00", 0)
    if size == 40:
        return pack(
            "<H24sB4sB8s",
            uid,
            _fixed(user_id, 24, encoding=encoding),
            status,
            packed_time,
            punch,
            bytes(8),
        )
    raise ValueError(f"Unsupported attendance record size {size}")


def build_attendance_payload(records: list[bytes], *, declared_size: int | None = None) -> bytes:
    """Wrap attendance records in the 4-byte little-endian total-size prefix."""
    body = b"".join(records)
    size = len(body) if declared_size is None else declared_size
    return pack("<I", size) + body


def build_live_event(
    *,
    size: int,
    user_id: str,
    occurred_at: datetime,
    punch: int = 0,
    status: int = 0,
) -> bytes:
    """Build one live-capture event body (12, 32, 36 or 52 bytes)."""
    timehex = pack(
        "6B",
        occurred_at.year - 2000,
        occurred_at.month,
        occurred_at.day,
        occurred_at.hour,
        occurred_at.minute,
        occurred_at.second,
    )
    if size == 12:
        return pack("<IBB6s", int(user_id), status, punch, timehex)
    if size == 32:
        return pack("<24sBB6s", _fixed(user_id, 24), status, punch, timehex)
    if size == 36:
        return pack("<24sBB6s4s", _fixed(user_id, 24), status, punch, timehex, bytes(4))
    if size == 52:
        return pack("<24sBB6s20s", _fixed(user_id, 24), status, punch, timehex, bytes(20))
    raise ValueError(f"Unsupported live event size {size}")


def sample_users() -> bytes:
    """A representative sanitized user payload: an admin and two employees.

    Covers a populated credential region, an empty one, an accented name and a
    user with no last name.
    """
    return build_user_payload(
        [
            build_user_record(
                uid=1,
                user_id="1001",
                first_name="Ada",
                last_name="Lovelace",
                privilege=ADMIN_PRIVILEGE,
                with_credential=True,
            ),
            build_user_record(
                uid=2,
                user_id="1002",
                first_name="Grace",
                last_name="Hopper",
                privilege=EMPLOYEE_PRIVILEGE,
            ),
            build_user_record(
                uid=3,
                user_id="EMP-003",
                first_name="José",
                last_name="",
                privilege=EMPLOYEE_PRIVILEGE,
            ),
        ]
    )
