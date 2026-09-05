"""Read-only protocol tracing for admin diagnostics (PHASE 10).

This module captures what the wire carries without ever changing it: raw
payload sizes, redacted hex previews, parsed field breakdowns and per-step
timings. The credential region (bytes 3:35 of every 120-byte user record)
is always zeroed before it leaves this module — nothing returned from here
can disclose a PIN, and nothing here can write, delete, clear or reset a
device (pinned by test).

Everything here is synchronous and PySide6-free.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Final

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.models import describe_privilege
from clockmanager.protocol.constants import (
    CMD_ATTLOG_RRQ,
    CMD_DB_RRQ,
    CMD_USERTEMP_RRQ,
    FCT_USER,
    MB1_USER_RECORD_SIZE,
    SIZE_PREFIX_BYTES,
    USER_CREDENTIAL_SLICE,
)
from clockmanager.protocol.errors import DeviceCapabilityError, DeviceError
from clockmanager.protocol.records import (
    parse_attendance_payload,
    parse_user_record,
    split_size_prefixed_payload,
)

__all__ = [
    "MAX_HEX_PREVIEW_BYTES",
    "RecordingTransport",
    "TraceEvent",
    "TraceRecorder",
    "UserRecordView",
    "capture_attendance_snapshot",
    "capture_user_snapshot",
    "format_hex_preview",
    "redact_payload_preview",
    "redact_user_record_bytes",
    "summarise_bytes_arg",
]

_logger = get_logger(__name__)

#: Hex previews never show more than this; a full 120-byte record is 120
#: bytes, so one redacted record is always shown whole.
MAX_HEX_PREVIEW_BYTES: Final = 128


def redact_user_record_bytes(record: bytes) -> bytes:
    """Return one 120-byte record with the credential region zeroed.

    Anything else is refused: redacting a buffer of unknown shape could
    silently pass secret bytes through.
    """
    if len(record) != MB1_USER_RECORD_SIZE:
        raise ValueError(
            f"Cannot redact {len(record)} bytes: not a {MB1_USER_RECORD_SIZE}-byte MB1 record."
        )
    return (
        record[: USER_CREDENTIAL_SLICE.start]
        + bytes(USER_CREDENTIAL_SLICE.stop - USER_CREDENTIAL_SLICE.start)
        + record[USER_CREDENTIAL_SLICE.stop :]
    )


def format_hex_preview(data: bytes, *, limit: int = MAX_HEX_PREVIEW_BYTES) -> str:
    """Render ``data`` as offset hex lines, truncated to ``limit`` bytes."""
    shown = data[:limit]
    lines = [
        f"{offset:04x}: {' '.join(f'{byte:02x}' for byte in shown[offset : offset + 16])}"
        for offset in range(0, len(shown), 16)
    ]
    if len(data) > limit:
        lines.append(f"... ({len(data) - limit} more byte(s) not shown)")
    return "\n".join(lines) if lines else "(empty)"


def summarise_bytes_arg(data: bytes) -> str:
    """Describe a byte argument without disclosing its contents."""
    return f"<{len(data)} byte(s)>"


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One step of a diagnostic run. Contains no credential bytes."""

    seq: int
    stage: str
    direction: str  # "TX", "RX" or "LOCAL"
    label: str
    detail: str = ""
    bytes_count: int | None = None
    duration_ms: float | None = None

    def as_row(self) -> list[str]:
        timing = "" if self.duration_ms is None else f"{self.duration_ms:.1f} ms"
        size = "" if self.bytes_count is None else str(self.bytes_count)
        return [str(self.seq), self.stage, self.direction, self.label, self.detail, size, timing]


class TraceRecorder:
    """Collects trace events in order with per-step timings."""

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    def record(
        self,
        stage: str,
        direction: str,
        label: str,
        *,
        detail: str = "",
        bytes_count: int | None = None,
        duration_ms: float | None = None,
    ) -> TraceEvent:
        event = TraceEvent(
            seq=len(self._events) + 1,
            stage=stage,
            direction=direction,
            label=label,
            detail=detail,
            bytes_count=bytes_count,
            duration_ms=duration_ms,
        )
        self._events.append(event)
        return event

    @contextmanager
    def timed(
        self,
        stage: str,
        direction: str,
        label: str,
        *,
        detail: str = "",
        bytes_count: int | None = None,
    ) -> Iterator[None]:
        """Record ``label`` with the wall-clock time its block took."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record(
                stage,
                direction,
                label,
                detail=detail,
                bytes_count=bytes_count,
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )

    @property
    def events(self) -> list[TraceEvent]:
        return list(self._events)


def _looks_like_user_payload(payload: bytes) -> bool:
    """Whether ``payload`` has the shape of a size-prefixed user-data read."""
    if len(payload) < SIZE_PREFIX_BYTES + MB1_USER_RECORD_SIZE:
        return False
    body = payload[SIZE_PREFIX_BYTES:]
    return len(body) % MB1_USER_RECORD_SIZE == 0


def redact_payload_preview(payload: bytes, *, command: int | None = None) -> str:
    """Hex preview of a received payload with user credentials zeroed.

    The command the payload was read with decides what it is. Only the shape
    is consulted when the command is unknown, and then erring towards
    redaction: over-redacting a diagnostic preview is safe, under-redacting
    leaks a credential.

    Shape alone is not enough to identify user data. A 40-byte attendance
    record is the size the project NG-MB1 actually uses, so every third
    attendance record makes the body an exact multiple of 120 bytes and it
    used to be masked as one user record -- silently blanking a real punch's
    user ID, status, timestamp and direction in the trace an engineer is
    reading to diagnose exactly that.

    ``CMD_DB_RRQ`` is withheld whole. It is the table read, and one of the
    tables it can return is the fingerprint store: its entries are mostly
    biometric template bytes, which must never be logged, exported or
    persisted (``SECURITY.md``). The function selector that would say which
    table it was is not part of the payload, so there is nothing to narrow the
    rule with -- and a size is all a diagnostic needs from it anyway.
    """
    if command == CMD_DB_RRQ:
        return (
            f"<{len(payload)} byte(s) withheld: a table read may carry biometric "
            "templates, which are never previewed>"
        )

    is_user_data = command == CMD_USERTEMP_RRQ or (
        command is None and _looks_like_user_payload(payload)
    )
    if not is_user_data:
        return format_hex_preview(payload)

    if not _looks_like_user_payload(payload):
        # User data of an unrecognised shape: the credential region cannot be
        # located, so no byte of it may be shown. Refuse the preview rather
        # than falling through to the unredacted branch.
        return f"<{len(payload)} byte(s) of user data withheld: not a whole 120-byte record>"

    prefix = payload[:SIZE_PREFIX_BYTES]
    body = payload[SIZE_PREFIX_BYTES:]
    redacted = b"".join(
        redact_user_record_bytes(body[offset : offset + MB1_USER_RECORD_SIZE])
        for offset in range(0, len(body), MB1_USER_RECORD_SIZE)
    )
    return format_hex_preview(prefix + redacted)


class RecordingTransport:
    """Wraps a pyzk transport and records genuine TX/RX traffic.

    Only used with real hardware: the mock device has no socket, so there is
    no packet traffic to record. Unknown attributes and methods delegate to
    the wrapped transport, so the adapter works unchanged.
    """

    def __init__(self, wrapped: Any, recorder: TraceRecorder) -> None:
        self._wrapped = wrapped
        self._recorder = recorder

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def send_command(
        self, command: int, data: bytes = b"", receive_size: int = 1024
    ) -> dict[str, Any]:
        """Record one outgoing command and its acknowledgement."""
        payload_note = summarise_bytes_arg(data) if data else "empty"
        with self._recorder.timed(
            "transport",
            "TX",
            f"CMD {command}",
            detail=f"payload {payload_note}, expecting up to {receive_size} B",
            bytes_count=len(data) or None,
        ):
            response: dict[str, Any] = self._wrapped.send_command(command, data, receive_size)
        self._recorder.record(
            "transport",
            "RX",
            f"ACK {command}",
            detail=f"status={response.get('status')!r} code={response.get('code')!r}",
        )
        return response

    def read_with_buffer(self, command: int, fct: int = 0) -> tuple[bytes, int]:
        """Record one buffered read with a redacted preview of what arrived."""
        with self._recorder.timed(
            "transport", "TX", f"Buffered read CMD {command}", detail=f"function={fct}"
        ):
            payload, size = self._wrapped.read_with_buffer(command, fct)
        self._recorder.record(
            "transport",
            "RX",
            f"Payload CMD {command}",
            detail=redact_payload_preview(payload, command=command),
            bytes_count=len(payload),
        )
        return payload, size


@dataclass(frozen=True, slots=True)
class UserRecordView:
    """One user record, safe to display: redacted hex plus parsed fields."""

    device_uid: int
    user_id: str
    first_name: str
    last_name: str
    privilege: int
    privilege_label: str
    has_credential_data: bool
    redacted_hex: str


@dataclass(frozen=True, slots=True)
class UserSnapshot:
    """Raw + parsed user data, credential-free by construction."""

    record_count: int
    total_bytes: int
    records: tuple[UserRecordView, ...] = ()
    truncated: bool = False


def capture_user_snapshot(device: Any, *, max_records: int = 25) -> UserSnapshot:
    """Read whole user records and return the redacted, parsed snapshot.

    Needs ``read_raw_user_records()`` on the device (both the MB1 adapter
    and the mock provide it). The raw credential bytes are zeroed inside
    this module and never returned.
    """
    read_raw = getattr(device, "read_raw_user_records", None)
    if not callable(read_raw):
        raise ValueError("This device does not expose raw user records for diagnostics.")
    raw_records = read_raw()
    views: list[UserRecordView] = []
    for record in raw_records[:max_records]:
        parsed = parse_user_record(record.raw)
        views.append(
            UserRecordView(
                device_uid=parsed.device_uid,
                user_id=parsed.user_id,
                first_name=parsed.first_name,
                last_name=parsed.last_name,
                privilege=parsed.privilege,
                privilege_label=describe_privilege(parsed.privilege),
                has_credential_data=parsed.has_credential_data,
                redacted_hex=format_hex_preview(redact_user_record_bytes(record.raw)),
            )
        )
    return UserSnapshot(
        record_count=len(raw_records),
        total_bytes=len(raw_records) * MB1_USER_RECORD_SIZE,
        records=tuple(views),
        truncated=len(raw_records) > max_records,
    )


@dataclass(frozen=True, slots=True)
class AttendanceSnapshot:
    """Raw + parsed attendance data."""

    payload_bytes: int | None
    declared_size: int | None
    record_size: int | None
    parsed_count: int
    events: tuple[dict[str, str], ...] = ()
    truncated: bool = False
    note: str = ""
    error: str = ""


def capture_attendance_snapshot(
    device: Any,
    *,
    record_count: int = 0,
    max_events: int = 25,
) -> AttendanceSnapshot:
    """Parse a raw attendance payload with byte-level context.

    Reads ``read_raw_attendance_payload()`` when the device offers it (the
    MB1 adapter does; the mock does when its script carries a fixture
    payload). Attendance bytes hold no credentials, so the preview is shown
    as-is. Falls back to parsed-only with an explanatory note.
    """
    read_raw = getattr(device, "read_raw_attendance_payload", None)
    if not callable(read_raw):
        return _parsed_only_attendance(device, max_events, "Raw payloads are unavailable.")
    try:
        payload, device_count = read_raw()
    except DeviceCapabilityError as exc:
        # The transport has parsed records but no packet bytes (e.g. a mock
        # built without a fixture payload). Show parsed records, honestly
        # labelled, rather than failing the whole snapshot.
        return _parsed_only_attendance(device, max_events, str(exc))
    except DeviceError as exc:
        return AttendanceSnapshot(
            payload_bytes=None,
            declared_size=None,
            record_size=None,
            parsed_count=0,
            note="",
            error=str(exc),
        )
    try:
        declared, body = split_size_prefixed_payload(payload, what="Attendance data")
    except DeviceError as exc:
        return AttendanceSnapshot(
            payload_bytes=len(payload),
            declared_size=None,
            record_size=None,
            parsed_count=0,
            note=format_hex_preview(payload),
            error=str(exc),
        )
    try:
        events = parse_attendance_payload(payload, record_count=device_count or record_count)
    except DeviceError as exc:
        return AttendanceSnapshot(
            payload_bytes=len(payload),
            declared_size=declared,
            record_size=None,
            parsed_count=0,
            note=format_hex_preview(body),
            error=str(exc),
        )
    shown = events[:max_events]
    return AttendanceSnapshot(
        payload_bytes=len(payload),
        declared_size=declared,
        record_size=(len(body) // len(events)) if events else None,
        parsed_count=len(events),
        events=tuple(
            {
                "user_id": event.user_id,
                "occurred_at": event.occurred_at.isoformat(sep=" "),
                "punch": event.direction_label,
                "status": str(event.status),
            }
            for event in shown
        ),
        truncated=len(events) > max_events,
        note=format_hex_preview(body),
    )


def _parsed_only_attendance(device: Any, max_events: int, reason: str) -> AttendanceSnapshot:
    """Parsed attendance without raw bytes (transports without packet capture)."""
    try:
        events = device.get_attendance()
    except DeviceError as exc:
        return AttendanceSnapshot(
            payload_bytes=None,
            declared_size=None,
            record_size=None,
            parsed_count=0,
            note=reason,
            error=str(exc),
        )
    shown = events[:max_events]
    return AttendanceSnapshot(
        payload_bytes=None,
        declared_size=None,
        record_size=None,
        parsed_count=len(events),
        events=tuple(
            {
                "user_id": event.user_id,
                "occurred_at": event.occurred_at.isoformat(sep=" "),
                "punch": event.direction_label,
                "status": str(event.status),
            }
            for event in shown
        ),
        truncated=len(events) > max_events,
        note=f"{reason} Parsed records are shown.",
    )


#: Re-exported so diagnostics summaries can name the read commands honestly.
READ_COMMANDS: Final[dict[str, int]] = {
    "users": CMD_USERTEMP_RRQ,
    "attendance": CMD_ATTLOG_RRQ,
    "user_function": FCT_USER,
}
