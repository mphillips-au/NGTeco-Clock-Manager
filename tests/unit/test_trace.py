"""Protocol tracing tests (PHASE 10).

Tracing captures what the wire carries without changing it. These tests pin
the security contract: credential bytes are zeroed inside the protocol
layer, previews are bounded, and no trace helper can write to a device.
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

import pytest

from clockmanager.protocol import trace
from clockmanager.protocol.constants import MB1_USER_RECORD_SIZE
from clockmanager.protocol.mock import MockAttendanceDevice, MockDeviceScript
from clockmanager.protocol.records import parse_user_payload
from clockmanager.protocol.trace import (
    RecordingTransport,
    TraceRecorder,
    capture_attendance_snapshot,
    capture_user_snapshot,
    format_hex_preview,
    redact_payload_preview,
    redact_user_record_bytes,
)
from tests.fixtures.mb1 import (
    CREDENTIAL_MARKER_BYTE,
    build_attendance_payload,
    build_attendance_record,
    build_user_payload,
    build_user_record,
    sample_users,
)

MARKER_HEX_RUN = " ".join(f"{CREDENTIAL_MARKER_BYTE:02x}" for _ in range(32))


class TestRedactUserRecordBytes:
    def test_zeroes_the_credential_region_only(self) -> None:
        record = build_user_record(uid=7, user_id="1001", first_name="Ada", with_credential=True)
        redacted = redact_user_record_bytes(record)
        assert len(redacted) == MB1_USER_RECORD_SIZE
        assert redacted[3:35] == bytes(32)
        assert redacted[:3] == record[:3]
        assert redacted[35:] == record[35:]

    def test_refuses_buffers_of_unknown_shape(self) -> None:
        with pytest.raises(ValueError):
            redact_user_record_bytes(bytes(72))


class TestFormatHexPreview:
    def test_offsets_and_truncation(self) -> None:
        text = format_hex_preview(bytes(range(32)))
        assert "0000:" in text
        assert "0010:" in text

    def test_long_buffers_are_bounded(self) -> None:
        text = format_hex_preview(bytes(300), limit=64)
        assert "more byte(s) not shown" in text

    def test_empty(self) -> None:
        assert format_hex_preview(b"") == "(empty)"


class TestRedactPayloadPreview:
    def test_user_payload_hides_the_marker(self) -> None:
        payload = build_user_payload(
            [build_user_record(uid=1, user_id="1001", with_credential=True)]
        )
        assert MARKER_HEX_RUN not in redact_payload_preview(payload, command=9)

    def test_user_shape_is_detected_without_command(self) -> None:
        payload = build_user_payload(
            [build_user_record(uid=1, user_id="1001", with_credential=True)]
        )
        assert MARKER_HEX_RUN not in redact_payload_preview(payload)

    def test_attendance_payload_is_shown(self) -> None:
        moment = datetime(2026, 3, 1, 9, 0, 0)  # noqa: DTZ001 - device-local wall time
        payload = build_attendance_payload(
            [build_attendance_record(size=8, uid=1, occurred_at=moment, punch=0)]
        )
        assert "0000:" in redact_payload_preview(payload)


class TestTraceRecorder:
    def test_sequences_and_times(self) -> None:
        recorder = TraceRecorder()
        recorder.record("session", "LOCAL", "first")
        with recorder.timed("transport", "TX", "second"):
            pass
        events = recorder.events
        assert [event.seq for event in events] == [1, 2]
        assert events[1].duration_ms is not None
        assert events[1].duration_ms >= 0.0

    def test_events_are_a_copy(self) -> None:
        recorder = TraceRecorder()
        recorder.record("s", "LOCAL", "x")
        recorder.events.clear()
        assert len(recorder.events) == 1


class _FakeTransport:
    """Minimal pyzk-shaped transport for recorder tests (no sockets)."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.sent: list[tuple[int, bytes]] = []

    def send_command(self, command: int, data: bytes, receive_size: int) -> dict[str, object]:
        self.sent.append((command, data))
        return {"status": True, "code": 0}

    def read_with_buffer(self, command: int, fct: int = 0) -> tuple[bytes, int]:
        return self._payload, len(self._payload)

    @property
    def custom_attribute(self) -> str:
        return "delegated"


class TestRecordingTransport:
    def test_send_command_records_tx_and_ack(self) -> None:
        recorder = TraceRecorder()
        transport = RecordingTransport(_FakeTransport(b""), recorder)
        response = transport.send_command(1013, b"", 8)
        assert response == {"status": True, "code": 0}
        kinds = [(event.direction, event.label) for event in recorder.events]
        assert ("TX", "CMD 1013") in kinds
        assert ("RX", "ACK 1013") in kinds

    def test_user_read_is_redacted(self) -> None:
        payload = build_user_payload(
            [build_user_record(uid=1, user_id="1001", with_credential=True)]
        )
        recorder = TraceRecorder()
        transport = RecordingTransport(_FakeTransport(payload), recorder)
        received, _size = transport.read_with_buffer(9, 5)
        assert received == payload
        rx = next(e for e in recorder.events if e.direction == "RX")
        assert rx.bytes_count == len(payload)
        assert MARKER_HEX_RUN not in rx.detail

    def test_unknown_attributes_delegate(self) -> None:
        transport = RecordingTransport(_FakeTransport(b""), TraceRecorder())
        assert transport.custom_attribute == "delegated"


def _mock(users_payload: bytes = sample_users()) -> MockAttendanceDevice:
    from clockmanager.protocol.interface import DeviceConnectionSettings

    device = MockAttendanceDevice(
        settings=DeviceConnectionSettings(name="mock", host="192.0.2.10"),
        script=MockDeviceScript(users=parse_user_payload(users_payload)),
    )
    device.connect()
    return device


class TestCaptureUserSnapshot:
    def test_redacted_and_parsed(self) -> None:
        snapshot = capture_user_snapshot(_mock())
        assert snapshot.record_count == 3
        assert snapshot.total_bytes == 3 * MB1_USER_RECORD_SIZE
        assert not snapshot.truncated
        first = snapshot.records[0]
        assert (first.device_uid, first.user_id) == (1, "1001")
        assert first.privilege_label == "Admin"
        assert first.has_credential_data is True
        assert MARKER_HEX_RUN not in first.redacted_hex

    def test_truncation_caps_detail_not_counts(self) -> None:
        snapshot = capture_user_snapshot(_mock(), max_records=1)
        assert snapshot.record_count == 3
        assert len(snapshot.records) == 1
        assert snapshot.truncated is True

    def test_device_without_raw_access_is_refused(self) -> None:
        with pytest.raises(ValueError, match="raw user records"):
            capture_user_snapshot(object())


class TestCaptureAttendanceSnapshot:
    def _payload(self) -> bytes:
        return build_attendance_payload(
            [
                build_attendance_record(
                    size=8,
                    uid=1,
                    occurred_at=datetime(2026, 3, 1, 9, 0, 0),
                    punch=0,  # noqa: DTZ001
                ),
                build_attendance_record(
                    size=8,
                    uid=2,
                    occurred_at=datetime(2026, 3, 1, 17, 0, 0),
                    punch=1,  # noqa: DTZ001
                ),
            ]
        )

    def test_raw_and_parsed(self) -> None:
        device = _mock()
        device._script.attendance_raw = self._payload()
        device._script.attendance_record_count = 2
        snapshot = capture_attendance_snapshot(device)
        assert snapshot.payload_bytes == len(self._payload())
        assert snapshot.parsed_count == 2
        assert snapshot.record_size == 8
        assert snapshot.error == ""
        assert snapshot.events[0]["punch"] == "IN"
        assert snapshot.events[1]["punch"] == "OUT"

    def test_mock_without_payload_falls_back_to_parsed(self) -> None:
        snapshot = capture_attendance_snapshot(_mock())
        assert snapshot.payload_bytes is None
        assert "parsed" in snapshot.note.lower()

    def test_corrupt_payload_reports_an_error(self) -> None:
        device = _mock()
        device._script.attendance_raw = b"\x05\x00"
        snapshot = capture_attendance_snapshot(device)
        assert snapshot.error != ""


def test_trace_module_has_no_write_capability() -> None:
    """Diagnostics must never gain a write/delete/clear/reset operation."""
    path = Path(trace.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {
        node.name.lower()
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    forbidden = ("write", "delete", "clear", "reset", "set_user", "enroll", "template")
    for name in names:
        assert not any(part in name for part in forbidden), f"trace must not define {name!r}"
    calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            calls.add(node.attr)
    for forbidden_call in ("apply_user_write", "delete_user", "clear_attendance", "set_time"):
        assert forbidden_call not in calls
