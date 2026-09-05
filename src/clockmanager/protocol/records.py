"""MB1 record parsing.

Pure functions with no I/O, so every layout decision is unit-testable against
sanitized fixtures.

The NG-MB1 user record is 120 bytes. The generic ZKTeco 28/72-byte parsers in
``pyzk`` produce garbage for this device and must never be used here
(``PROTOCOL.md``).

An unrecognised layout raises :class:`DeviceParseError` rather than being
guessed at.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from struct import unpack

from clockmanager.domain.models import AttendanceEvent, DeviceUser, FingerprintSlot
from clockmanager.protocol.constants import (
    ATTENDANCE_RECORD_SIZES,
    FINGERPRINT_ENTRY_HEADER_SIZE,
    MAX_DEVICE_YEAR,
    MB1_USER_RECORD_SIZE,
    MIN_DEVICE_YEAR,
    SIZE_PREFIX_BYTES,
    USER_CREDENTIAL_SLICE,
    USER_FIRST_NAME_SLICE,
    USER_ID_SLICE,
    USER_LAST_NAME_SLICE,
    USER_PRIVILEGE_OFFSET,
    USER_UID_SLICE,
)
from clockmanager.protocol.errors import DeviceParseError

__all__ = [
    "DEFAULT_ENCODING",
    "decode_zk_time",
    "decode_zk_timehex",
    "parse_attendance_payload",
    "parse_fingerprint_payload",
    "parse_live_event",
    "parse_user_payload",
    "parse_user_record",
    "split_size_prefixed_payload",
]

DEFAULT_ENCODING = "utf-8"


# -- helpers ------------------------------------------------------------------


def _decode_text(raw: bytes, *, encoding: str = DEFAULT_ENCODING) -> str:
    """Decode a NUL-terminated fixed-width device string."""
    return raw.split(b"\x00")[0].decode(encoding, errors="replace").strip()


def split_size_prefixed_payload(payload: bytes, *, what: str) -> tuple[int, bytes]:
    """Split a buffered read into its declared total size and its record body.

    Buffered reads are prefixed with a 4-byte little-endian total size.
    """
    if len(payload) < SIZE_PREFIX_BYTES:
        raise DeviceParseError(
            f"{what} payload is {len(payload)} bytes, too short to contain a size prefix."
        )
    declared_size = int(unpack("<I", payload[:SIZE_PREFIX_BYTES])[0])
    return declared_size, payload[SIZE_PREFIX_BYTES:]


# -- time ---------------------------------------------------------------------


def decode_zk_time(raw: bytes) -> datetime:
    """Decode the ZKTeco packed 4-byte timestamp (``zkemsdk.c`` DecodeTime).

    The device reports local device time and transmits no timezone, so the
    result is naive by construction and must not be assumed to be UTC.
    """
    if len(raw) != 4:
        raise DeviceParseError(f"Packed timestamp must be 4 bytes, got {len(raw)}.")

    value = int(unpack("<I", raw)[0])
    second = value % 60
    value //= 60
    minute = value % 60
    value //= 60
    hour = value % 24
    value //= 24
    day = value % 31 + 1
    value //= 31
    month = value % 12 + 1
    value //= 12
    year = value + 2000

    # The packed form has no invalid encodings: every 32-bit value decodes to
    # some calendar date, so a corrupt or truncated packet yields a punch that
    # looks legitimate. 0xffffffff decodes to the year 2133, which would be
    # stored, counted in a pay period and reported as real. The encoding is
    # epoch-2000 and the field cannot express a date before that, so bound the
    # year to the century it can meaningfully describe and refuse the rest.
    if not MIN_DEVICE_YEAR <= year <= MAX_DEVICE_YEAR:
        raise DeviceParseError(
            f"Device sent a timestamp in the year {year}, outside the plausible "
            f"range {MIN_DEVICE_YEAR}-{MAX_DEVICE_YEAR}. Refusing to store a "
            "punch from a corrupt packet."
        )

    try:
        return datetime(year, month, day, hour, minute, second)  # noqa: DTZ001
    except ValueError as exc:
        raise DeviceParseError(f"Device sent an invalid timestamp: {exc}") from exc


def decode_zk_timehex(raw: bytes) -> datetime:
    """Decode the 6-byte per-field timestamp used by live capture events."""
    if len(raw) != 6:
        raise DeviceParseError(f"Live event timestamp must be 6 bytes, got {len(raw)}.")

    year, month, day, hour, minute, second = unpack("6B", raw)
    full_year = year + 2000
    if not MIN_DEVICE_YEAR <= full_year <= MAX_DEVICE_YEAR:
        raise DeviceParseError(
            f"Live event carried the year {full_year}, outside the plausible "
            f"range {MIN_DEVICE_YEAR}-{MAX_DEVICE_YEAR}."
        )
    try:
        return datetime(full_year, month, day, hour, minute, second)  # noqa: DTZ001
    except ValueError as exc:
        raise DeviceParseError(f"Device sent an invalid live timestamp: {exc}") from exc


# -- users --------------------------------------------------------------------


def parse_user_record(record: bytes, *, encoding: str = DEFAULT_ENCODING) -> DeviceUser:
    """Parse one 120-byte MB1 user record.

    Layout verified against the real NG-MB1::

        0:2    UID, little-endian uint16
        2      privilege byte (0 Employee, 14 Admin)
        3:35   credential/PIN region  -- never decoded or returned
        35:59  first name
        59:96  last name
        96:120 user ID
    """
    if len(record) != MB1_USER_RECORD_SIZE:
        raise DeviceParseError(
            f"MB1 user record must be {MB1_USER_RECORD_SIZE} bytes, got {len(record)}."
        )

    uid = int(unpack("<H", record[USER_UID_SLICE])[0])
    privilege = record[USER_PRIVILEGE_OFFSET]
    first_name = _decode_text(record[USER_FIRST_NAME_SLICE], encoding=encoding)
    last_name = _decode_text(record[USER_LAST_NAME_SLICE], encoding=encoding)
    user_id = _decode_text(record[USER_ID_SLICE], encoding=encoding)

    # The credential region is inspected for presence only. Its contents are
    # never decoded, returned or logged (SECURITY.md).
    has_credential_data = any(record[USER_CREDENTIAL_SLICE])

    if not user_id:
        # A record with no user ID cannot be addressed; fall back to the UID
        # rather than inventing an identifier.
        user_id = str(uid)

    return DeviceUser(
        device_uid=uid,
        user_id=user_id,
        first_name=first_name,
        last_name=last_name,
        privilege=privilege,
        has_credential_data=has_credential_data,
    )


def parse_user_payload(payload: bytes, *, encoding: str = DEFAULT_ENCODING) -> list[DeviceUser]:
    """Parse a full buffered user-data read into domain users."""
    if not payload:
        return []

    declared_size, body = split_size_prefixed_payload(payload, what="User data")
    if not body:
        return []

    # Trust the declared size when it is consistent with what arrived; some
    # transports append padding beyond the declared region.
    if 0 < declared_size <= len(body):
        body = body[:declared_size]

    if len(body) % MB1_USER_RECORD_SIZE != 0:
        raise DeviceParseError(
            f"User data is {len(body)} bytes, which is not a multiple of the verified "
            f"MB1 record size of {MB1_USER_RECORD_SIZE}. Refusing to guess at an "
            "unrecognised layout."
        )

    return [
        parse_user_record(body[offset : offset + MB1_USER_RECORD_SIZE], encoding=encoding)
        for offset in range(0, len(body), MB1_USER_RECORD_SIZE)
    ]


# -- attendance ---------------------------------------------------------------


def _resolve_attendance_record_size(body_length: int, record_count: int) -> int:
    """Determine the attendance record size from the payload, never by assumption.

    The device's own record count is authoritative. Without it the length alone
    is often ambiguous -- every multiple of 40 is also a multiple of 8 -- so an
    ambiguous payload is refused rather than parsed into plausible-looking
    nonsense.
    """
    if record_count > 0 and body_length % record_count == 0:
        candidate = body_length // record_count
        if candidate in ATTENDANCE_RECORD_SIZES:
            return candidate

    candidates = [size for size in ATTENDANCE_RECORD_SIZES if body_length % size == 0]
    if len(candidates) == 1:
        return candidates[0]

    if not candidates:
        raise DeviceParseError(
            f"Attendance data of {body_length} bytes does not divide into any known "
            f"ZKTeco record size {ATTENDANCE_RECORD_SIZES}."
        )

    raise DeviceParseError(
        f"Attendance data of {body_length} bytes is ambiguous: it matches record "
        f"sizes {candidates}, and the device reported {record_count} record(s), "
        "which is not consistent with any of them. Refusing to guess."
    )


def parse_attendance_payload(
    payload: bytes,
    *,
    record_count: int = 0,
    users: Sequence[DeviceUser] | None = None,
    encoding: str = DEFAULT_ENCODING,
) -> list[AttendanceEvent]:
    """Parse a full buffered attendance read into domain events.

    ``users`` supplies the UID-to-user-ID mapping needed by the compact 8-byte
    record form. It must come from the application-owned 120-byte parser; the
    generic ``pyzk`` user parser maps MB1 records incorrectly.
    """
    if not payload:
        return []

    _declared_size, body = split_size_prefixed_payload(payload, what="Attendance data")
    if not body:
        return []

    record_size = _resolve_attendance_record_size(len(body), record_count)
    uid_to_user_id: Mapping[int, str] = {user.device_uid: user.user_id for user in (users or ())}

    events: list[AttendanceEvent] = []
    for offset in range(0, len(body) - record_size + 1, record_size):
        events.append(
            _parse_attendance_record(
                body[offset : offset + record_size],
                record_size=record_size,
                uid_to_user_id=uid_to_user_id,
                encoding=encoding,
            )
        )
    return events


def _parse_attendance_record(
    record: bytes,
    *,
    record_size: int,
    uid_to_user_id: Mapping[int, str],
    encoding: str,
) -> AttendanceEvent:
    """Parse one attendance record.

    ``punch`` gives the direction (0 IN, 1 OUT). ``status`` is preserved as raw
    metadata and is never used to infer direction.
    """
    device_uid: int | None
    if record_size == 8:
        uid, status, packed_time, punch = unpack("<HB4sB", record)
        user_id = uid_to_user_id.get(int(uid), str(uid))
        device_uid = int(uid)
    elif record_size == 16:
        numeric_user_id, packed_time, status, punch, _reserved, _workcode = unpack(
            "<I4sBB2sI", record
        )
        user_id = str(numeric_user_id)
        device_uid = None
    elif record_size == 40:
        # Verified on the project NG-MB1 (PHASE 14): the leading uint16 is the
        # attendance record's own index, NOT the user's device UID. Three
        # consecutive punches by user UID 1 carried 1, 2, 3 there. It must not
        # be reported as a device UID, and it must never stand in for a user
        # ID -- doing so invents a user that is not on the device.
        _record_index, raw_user_id, status, packed_time, punch, _space = unpack(
            "<H24sB4sB8s", record
        )
        user_id = _decode_text(raw_user_id, encoding=encoding)
        if not user_id:
            raise DeviceParseError(
                "A 40-byte attendance record carried no user ID. Refusing to "
                "attribute the punch to a guessed identity."
            )
        device_uid = None
    else:  # pragma: no cover - guarded by _resolve_attendance_record_size
        raise DeviceParseError(f"Unsupported attendance record size {record_size}.")

    return AttendanceEvent(
        user_id=user_id,
        occurred_at=decode_zk_time(packed_time),
        punch=int(punch),
        status=int(status),
        device_uid=device_uid,
    )


# -- live capture -------------------------------------------------------------

#: Live event body sizes defined by the ZKTeco protocol.
_LIVE_EVENT_LAYOUTS: dict[int, str] = {
    12: "<IBB6s",
    32: "<24sBB6s",
    36: "<24sBB6s4s",
    52: "<24sBB6s20s",
}


def parse_live_event(data: bytes, *, encoding: str = DEFAULT_ENCODING) -> AttendanceEvent:
    """Parse one live-capture attendance event body."""
    layout = _LIVE_EVENT_LAYOUTS.get(len(data))
    if layout is None:
        raise DeviceParseError(
            f"Live event body of {len(data)} bytes does not match any known layout "
            f"{sorted(_LIVE_EVENT_LAYOUTS)}."
        )

    fields = unpack(layout, data)
    raw_user_id, status, punch, timehex = fields[0], fields[1], fields[2], fields[3]
    if isinstance(raw_user_id, int):
        user_id = str(raw_user_id)
    else:
        user_id = _decode_text(raw_user_id, encoding=encoding)

    if not user_id:
        raise DeviceParseError("Live event contained no user identifier.")

    return AttendanceEvent(
        user_id=user_id,
        occurred_at=decode_zk_timehex(timehex),
        punch=int(punch),
        status=int(status),
    )


# -- fingerprints -------------------------------------------------------------


def parse_fingerprint_payload(payload: bytes) -> list[FingerprintSlot]:
    """Enumerate the fingerprint store without disclosing any template.

    Verified on the project NG-MB1 (PHASE 15). The buffered read of
    ``CMD_DB_RRQ``/``FCT_FINGERTMP`` returns a 4-byte total size followed by
    variable-length entries, each framed::

        0:2  total entry size, little-endian uint16 (header + template)
        2:4  user UID, little-endian uint16
        4    finger index, signed byte
        5    valid flag, signed byte
        6:   template bytes

    The template bytes are deliberately **skipped, not returned**. This
    function is the boundary that keeps biometric data out of the rest of the
    application (``SECURITY.md``): the only thing it reports about a template
    is how long it was.
    """
    if not payload:
        return []

    declared_size, body = split_size_prefixed_payload(payload, what="Fingerprint data")
    if 0 < declared_size <= len(body):
        body = body[:declared_size]

    slots: list[FingerprintSlot] = []
    offset = 0
    while offset + FINGERPRINT_ENTRY_HEADER_SIZE <= len(body):
        entry_size, uid, finger_index, valid = unpack(
            "<HHbb", body[offset : offset + FINGERPRINT_ENTRY_HEADER_SIZE]
        )
        if entry_size < FINGERPRINT_ENTRY_HEADER_SIZE:
            raise DeviceParseError(
                f"Fingerprint entry at offset {offset} declares {entry_size} bytes, "
                f"which is smaller than its {FINGERPRINT_ENTRY_HEADER_SIZE}-byte header."
            )
        if offset + entry_size > len(body):
            raise DeviceParseError(
                f"Fingerprint entry at offset {offset} declares {entry_size} bytes but "
                f"only {len(body) - offset} remain. Refusing to parse a truncated store."
            )
        slots.append(
            FingerprintSlot(
                device_uid=int(uid),
                finger_index=int(finger_index),
                valid=int(valid),
                template_bytes=entry_size - FINGERPRINT_ENTRY_HEADER_SIZE,
            )
        )
        offset += entry_size

    if offset != len(body):
        raise DeviceParseError(
            f"Fingerprint data has {len(body) - offset} trailing byte(s) after the last "
            "entry. Refusing to guess at an unrecognised layout."
        )
    return slots
