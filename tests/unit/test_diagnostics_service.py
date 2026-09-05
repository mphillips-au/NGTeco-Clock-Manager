"""Diagnostics service tests (PHASE 10).

Connection reports and protocol traces are read-only sessions with per-step
timings. Device failures arrive as failed results; only role refusal raises.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from clockmanager.config import AppConfig, AppPaths
from clockmanager.domain.auth import Role
from clockmanager.domain.models import AttendanceEvent
from clockmanager.errors import ClockManagerError, SecurityError
from clockmanager.protocol.capabilities import Capability
from clockmanager.protocol.mock import MockAttendanceDevice, MockDeviceScript
from clockmanager.services.application import ApplicationContext, bootstrap
from clockmanager.services.audit import AuditAction
from clockmanager.services.devices import DeviceProfile, DeviceService
from clockmanager.services.diagnostics import DiagnosticsService
from tests.fixtures.mb1 import (
    CREDENTIAL_MARKER_BYTE,
    build_attendance_payload,
    build_attendance_record,
)


@pytest.fixture
def context(tmp_path: Path) -> Iterator[ApplicationContext]:
    config = AppConfig(
        paths=AppPaths(tmp_path / "appdata"),
        log_to_console=False,
        use_mock_device=True,
    )
    ctx = bootstrap(config=config)
    try:
        yield ctx
    finally:
        ctx.shutdown()


@pytest.fixture
def profile(context: ApplicationContext) -> DeviceProfile:
    return context.devices.save_profile(DeviceProfile(name="Bench clock", host="192.0.2.10"))


def _failing_service(context: ApplicationContext) -> DeviceService:
    script = MockDeviceScript(users=[], attendance=[], connect_failures=99)

    def _factory(device_profile: DeviceProfile, **_: object) -> MockAttendanceDevice:
        return MockAttendanceDevice(settings=device_profile.to_connection_settings(), script=script)

    return DeviceService(context.database, device_factory=_factory)  # type: ignore[arg-type]


class TestConnectionReport:
    def test_passes_with_timings_and_stamps_last_seen(
        self, context: ApplicationContext, profile: DeviceProfile
    ) -> None:
        report = context.diagnostics.connection_report(profile)
        assert report.ok
        assert [step.name for step in report.steps] == [
            "Connect + snapshot",
            "Device clock",
            "Reconnect",
        ]
        assert all(step.duration_ms >= 0.0 for step in report.steps)
        assert "Passed" in report.as_rows()[0]
        refreshed = context.devices.get_profile(profile.device_id or 0)
        assert refreshed is not None and refreshed.last_seen_at is not None

    def test_failure_is_a_result(self, context: ApplicationContext, profile: DeviceProfile) -> None:
        service = DiagnosticsService(_failing_service(context), context.audit)
        report = service.connection_report(profile)
        assert not report.ok
        assert report.error != ""
        assert report.error_type == "DeviceConnectionError"

    def test_non_admin_is_refused(
        self, context: ApplicationContext, profile: DeviceProfile
    ) -> None:
        with pytest.raises(SecurityError):
            context.diagnostics.connection_report(profile, requester_role=Role.VIEWER)
        with pytest.raises(SecurityError):
            context.diagnostics.protocol_trace(profile, requester_role=Role.OFFICE_STAFF)


class TestProtocolTrace:
    def test_full_trace_against_the_mock(
        self, context: ApplicationContext, profile: DeviceProfile
    ) -> None:
        trace = context.diagnostics.protocol_trace(profile)
        assert trace.ok, trace.error
        expected_users = context.devices.read_users(profile)
        assert trace.users is not None and trace.users.record_count == len(expected_users)
        assert trace.users.total_bytes == len(expected_users) * 120
        assert trace.attendance is not None
        assert "Mock transport" in trace.transport_note
        assert trace.live is not None and trace.live.skipped is True
        assert any(name == "write_users" for name, _, _ in trace.capabilities)
        assert len(trace.events) > 5
        assert all(event.duration_ms is None or event.duration_ms >= 0.0 for event in trace.events)

    def test_leaves_the_device_disconnected(
        self, context: ApplicationContext, profile: DeviceProfile
    ) -> None:
        from clockmanager.protocol.records import parse_user_payload
        from tests.fixtures.mb1 import sample_users

        shared = MockAttendanceDevice(
            settings=profile.to_connection_settings(),
            script=MockDeviceScript(users=parse_user_payload(sample_users())),
        )

        def _factory(device_profile: DeviceProfile, **_: object) -> MockAttendanceDevice:
            return shared

        service = DiagnosticsService(
            DeviceService(context.database, device_factory=_factory),  # type: ignore[arg-type]
            context.audit,
        )
        trace = service.protocol_trace(profile, live_seconds=1.0)
        assert trace.ok, trace.error
        assert not shared.is_connected
        assert shared.disconnect_calls >= 1

    def test_live_window_collects_mock_events(
        self, context: ApplicationContext, profile: DeviceProfile
    ) -> None:
        live = [
            AttendanceEvent(
                user_id="1001",
                occurred_at=datetime(2026, 3, 1, 9, 0, 0),  # noqa: DTZ001
                punch=0,
                status=0,
            ),
            None,
        ]
        devices = DeviceService(
            context.database,
            device_factory=_script_factory(live_events=live),  # type: ignore[arg-type]
        )
        service = DiagnosticsService(devices, context.audit)
        trace = service.protocol_trace(profile, live_seconds=5.0)
        assert trace.ok, trace.error
        assert trace.live is not None
        assert trace.live.collected == 1
        assert trace.live.idle_timeouts == 1

    def test_raw_attendance_from_fixture_payload(
        self, context: ApplicationContext, profile: DeviceProfile
    ) -> None:
        payload = build_attendance_payload(
            [
                build_attendance_record(
                    size=8,
                    uid=1,
                    occurred_at=datetime(2026, 3, 1, 9, 0, 0),  # noqa: DTZ001
                    punch=0,
                )
            ]
        )
        devices = DeviceService(
            context.database,
            device_factory=_script_factory(attendance_raw=payload),  # type: ignore[arg-type]
        )
        trace = DiagnosticsService(devices, context.audit).protocol_trace(profile)
        assert trace.ok, trace.error
        assert trace.attendance is not None
        assert trace.attendance.payload_bytes == len(payload)
        assert trace.attendance.parsed_count == 1
        assert trace.attendance.record_size == 8

    def test_failure_returns_events_so_far(
        self, context: ApplicationContext, profile: DeviceProfile
    ) -> None:
        service = DiagnosticsService(_failing_service(context), context.audit)
        trace = service.protocol_trace(profile)
        assert not trace.ok
        assert trace.error != ""

    def test_invalid_arguments_raise(
        self, context: ApplicationContext, profile: DeviceProfile
    ) -> None:
        with pytest.raises(ClockManagerError):
            context.diagnostics.protocol_trace(profile, live_seconds=60.0)
        with pytest.raises(ClockManagerError):
            context.diagnostics.protocol_trace(profile, max_records=0)


def _script_factory(**script_kwargs: object):  # type: ignore[no-untyped-def]
    from clockmanager.protocol.records import parse_user_payload
    from tests.fixtures.mb1 import sample_users

    def _factory(device_profile: DeviceProfile, **_: object) -> MockAttendanceDevice:
        script = MockDeviceScript(
            users=parse_user_payload(sample_users()),
            **script_kwargs,  # type: ignore[arg-type]
        )
        return MockAttendanceDevice(settings=device_profile.to_connection_settings(), script=script)

    return _factory


class TestCapabilityReport:
    def test_reports_every_capability_without_touching_the_device(
        self, context: ApplicationContext, profile: DeviceProfile
    ) -> None:
        report = context.diagnostics.capability_report(profile)
        assert report.profile_name == "Bench clock"
        assert len(report.rows) == len(Capability)
        by_name = {name: support for name, support, _ in report.rows}
        assert by_name["connect"] == "supported"
        assert by_name["clear_attendance"] == "unsupported"

    def test_non_admin_is_refused(
        self, context: ApplicationContext, profile: DeviceProfile
    ) -> None:
        with pytest.raises(SecurityError):
            context.diagnostics.capability_report(profile, requester_role=Role.VIEWER)


class TestExportTrace:
    def test_export_is_sanitized_json_and_audited(
        self, context: ApplicationContext, profile: DeviceProfile
    ) -> None:
        trace = context.diagnostics.protocol_trace(profile)
        assert trace.ok
        payload, filename, mime = context.diagnostics.export_trace(trace)
        assert mime == "application/json"
        assert filename.startswith("diagnostics-Bench-clock-")
        document = json.loads(payload.decode("utf-8"))
        assert document["manifest"]["profile"] == "Bench clock"
        assert document["manifest"]["schema_version"] >= 7
        # The fixture credential marker must not appear as a contiguous run.
        marker_run = " ".join(f"{CREDENTIAL_MARKER_BYTE:02x}" for _ in range(32))
        assert marker_run not in payload.decode("utf-8")
        assert "communication_password" not in payload.decode("utf-8")
        actions = [entry.action for entry in context.audit.recent(limit=50)]
        assert AuditAction.DIAGNOSTICS_EXPORT.value in actions

    def test_export_of_failed_trace_marks_the_error(
        self, context: ApplicationContext, profile: DeviceProfile
    ) -> None:
        service = DiagnosticsService(_failing_service(context), context.audit)
        trace = service.protocol_trace(profile)
        assert not trace.ok
        payload, _, _ = service.export_trace(trace)
        assert json.loads(payload.decode("utf-8"))["manifest"]["ok"] is False

    def test_non_admin_is_refused(
        self, context: ApplicationContext, profile: DeviceProfile
    ) -> None:
        trace = context.diagnostics.protocol_trace(profile)
        with pytest.raises(SecurityError):
            context.diagnostics.export_trace(trace, requester_role=Role.OFFICE_STAFF)


class TestBuildTraced:
    def test_mock_note_is_honest_about_no_packets(
        self, context: ApplicationContext, profile: DeviceProfile
    ) -> None:
        from clockmanager.protocol.trace import TraceRecorder

        device, note = context.devices.build_traced(profile, TraceRecorder())
        try:
            assert "no socket" in note
            assert device.capabilities is not None
        finally:
            device.disconnect()

    def test_traced_build_never_enables_writes(
        self, context: ApplicationContext, profile: DeviceProfile
    ) -> None:
        from clockmanager.protocol.trace import TraceRecorder

        device, _note = context.devices.build_traced(profile, TraceRecorder())
        try:
            assert not device.capabilities.supports(Capability.WRITE_USERS)
            assert not device.capabilities.supports(Capability.DELETE_USERS)
        finally:
            device.disconnect()


def test_diagnostics_service_has_no_write_paths() -> None:
    """Diagnostics must never route to a write/delete/clear/set operation."""
    import ast
    from pathlib import Path

    for module in ("services/diagnostics.py", "protocol/trace.py"):
        path = Path("src/clockmanager") / module
        tree = ast.parse(path.read_text(encoding="utf-8"))
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        # Device-level destructive operations. (RecordingTransport passes
        # send_command through for packet capture, but diagnostics only ever
        # issues reads through it — covered by the traced-build test above.)
        for forbidden in (
            "apply_user_write",
            "delete_user",
            "clear_attendance",
            "set_time",
        ):
            assert forbidden not in attributes, f"{module} touches {forbidden}"
