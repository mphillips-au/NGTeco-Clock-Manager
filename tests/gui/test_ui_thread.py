"""Guards for the PHASE 02 rule that all device/network I/O stays off the UI thread.

Rather than trusting review, these record the thread each device call actually
runs on and assert it was not the UI thread.
"""

from __future__ import annotations

import threading

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from clockmanager.domain.models import AttendanceEvent, DeviceInfo, DeviceUser
from clockmanager.gui.views import AttendanceView, DashboardView, DiagnosticsView, UsersView
from clockmanager.services.application import ApplicationContext
from clockmanager.services.audit import AuditService
from clockmanager.services.devices import ConnectionTestResult, DeviceProfile, DeviceService
from clockmanager.services.users import UserService
from tests.gui.conftest import drain

pytestmark = pytest.mark.gui


class ThreadRecordingService(DeviceService):
    """Wraps the real service and records which thread each call ran on."""

    def __init__(self, inner: DeviceService) -> None:
        self._inner = inner
        self.threads: dict[str, int] = {}

    def _record(self, name: str) -> None:
        self.threads[name] = threading.get_ident()

    def list_profiles(self) -> list[DeviceProfile]:
        self._record("list_profiles")
        return self._inner.list_profiles()

    def first_enabled_profile(self) -> DeviceProfile | None:
        self._record("first_enabled_profile")
        return self._inner.first_enabled_profile()

    def save_profile(self, profile: DeviceProfile) -> DeviceProfile:
        self._record("save_profile")
        return self._inner.save_profile(profile)

    def test_connection(self, profile: DeviceProfile) -> ConnectionTestResult:
        self._record("test_connection")
        return self._inner.test_connection(profile)

    def read_device_info(self, profile: DeviceProfile) -> DeviceInfo:
        self._record("read_device_info")
        return self._inner.read_device_info(profile)

    def read_users(self, profile: DeviceProfile) -> list[DeviceUser]:
        self._record("read_users")
        return self._inner.read_users(profile)

    def read_attendance(self, profile: DeviceProfile) -> list[AttendanceEvent]:
        self._record("read_attendance")
        return self._inner.read_attendance(profile)

    def capabilities(self, profile: DeviceProfile):  # type: ignore[no-untyped-def]
        self._record("capabilities")
        return self._inner.capabilities(profile)


@pytest.fixture
def recording(configured_context: ApplicationContext) -> ThreadRecordingService:
    return ThreadRecordingService(configured_context.devices)


def _user_service(devices: ThreadRecordingService) -> UserService:
    """A user service over the recording device service.

    Writes stay switched off, which is what the shipped default is: these
    tests are about which thread the reads happen on.
    """
    return UserService(devices, AuditService(devices._inner._database))


def _ui_thread_id() -> int:
    return threading.get_ident()


def test_users_view_reads_off_the_ui_thread(
    qt_app: QApplication, recording: ThreadRecordingService
) -> None:
    ui_thread = _ui_thread_id()
    view = UsersView(recording, _user_service(recording))
    view.load()
    drain(qt_app)

    assert "read_users" in recording.threads
    assert recording.threads["read_users"] != ui_thread


def test_attendance_view_reads_off_the_ui_thread(
    qt_app: QApplication,
    recording: ThreadRecordingService,
    configured_context: ApplicationContext,
) -> None:
    ui_thread = _ui_thread_id()
    view = AttendanceView(recording, configured_context.sync)
    view.load()
    drain(qt_app)

    assert recording.threads["first_enabled_profile"] != ui_thread


def test_dashboard_reads_off_the_ui_thread(
    qt_app: QApplication, recording: ThreadRecordingService, configured_context: ApplicationContext
) -> None:
    ui_thread = _ui_thread_id()
    view = DashboardView(configured_context, recording)
    drain(qt_app)
    view.check_connection()
    drain(qt_app)

    assert recording.threads["first_enabled_profile"] != ui_thread
    assert recording.threads["test_connection"] != ui_thread


def test_diagnostics_checks_run_off_the_ui_thread(
    qt_app: QApplication, recording: ThreadRecordingService, configured_context: ApplicationContext
) -> None:
    ui_thread = _ui_thread_id()
    view = DiagnosticsView(configured_context, recording)
    drain(qt_app)

    view._test_connection()
    drain(qt_app)
    view._count_users()
    drain(qt_app)
    view._count_attendance()
    drain(qt_app)
    view._capabilities()
    drain(qt_app)

    for call in ("test_connection", "read_users", "read_attendance", "capabilities"):
        assert recording.threads[call] != ui_thread, call


def test_live_capture_runs_on_its_own_thread(
    qt_app: QApplication, configured_context: ApplicationContext
) -> None:
    from clockmanager.gui.workers import LiveCaptureWorker

    profile = configured_context.devices.first_enabled_profile()
    assert profile is not None

    worker = LiveCaptureWorker(configured_context.devices, profile)
    assert isinstance(worker, QThread)

    worker.start()
    worker.stop()
    assert worker.wait(10_000)
    assert not worker.isRunning()


def test_no_view_calls_a_device_read_in_its_constructor(
    qt_app: QApplication, recording: ThreadRecordingService, configured_context: ApplicationContext
) -> None:
    """Constructing a view must not block on the network."""
    UsersView(recording, _user_service(recording))
    AttendanceView(recording, configured_context.sync)
    qt_app.processEvents()

    device_reads = {"read_users", "read_attendance", "test_connection", "read_device_info"}
    assert not device_reads & set(recording.threads)
