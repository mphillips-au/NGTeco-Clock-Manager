"""Protocol diagnostics GUI tests (PHASE 10).

The new report/trace/export controls run off the UI thread, show redacted
detail only, and offer no destructive operation.
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog

from clockmanager.domain.auth import Role
from clockmanager.gui.views.diagnostics import DiagnosticsView
from clockmanager.services.application import ApplicationContext
from tests.gui.conftest import drain

pytestmark = pytest.mark.gui


def test_connection_report_shows_timed_steps(
    qt_app: QApplication, configured_context: ApplicationContext
) -> None:
    view = DiagnosticsView(configured_context, configured_context.devices)
    try:
        drain(qt_app)
        view._connection_report()
        drain(qt_app)
        assert "passed" in view._status.text()
        assert "Reconnect" in view._detail.toPlainText()
    finally:
        view.close()


def test_protocol_trace_shows_redacted_detail(
    qt_app: QApplication, configured_context: ApplicationContext
) -> None:
    view = DiagnosticsView(configured_context, configured_context.devices)
    try:
        drain(qt_app)
        view._live_seconds.setValue(0)
        view._protocol_trace()
        drain(qt_app)
        assert "Trace of" in view._status.text()
        detail = view._detail.toPlainText()
        assert "User records" in detail
        assert "credential region zeroed" in detail
        # The fixture credential marker must never reach the widget.
        assert "ab ab ab" not in detail
        assert view._export_button.isEnabled()
    finally:
        view.close()


def test_protocol_trace_with_live_listen_completes(
    qt_app: QApplication, configured_context: ApplicationContext
) -> None:
    view = DiagnosticsView(configured_context, configured_context.devices)
    try:
        drain(qt_app)
        view._live_seconds.setValue(1)
        view._protocol_trace()
        drain(qt_app)
        assert "Live listen" in view._detail.toPlainText()
    finally:
        view.close()


def test_export_writes_sanitized_json(
    qt_app: QApplication,
    configured_context: ApplicationContext,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "diagnostics.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "*.json"))
    view = DiagnosticsView(configured_context, configured_context.devices)
    try:
        drain(qt_app)
        view._live_seconds.setValue(0)
        view._protocol_trace()
        drain(qt_app)
        view._export_trace()
        drain(qt_app)
        assert target.is_file()
        document = json.loads(target.read_text(encoding="utf-8"))
        assert document["manifest"]["profile"] == "Bench clock"
        assert "ab ab ab" not in target.read_text(encoding="utf-8")
        assert "Exported" in view._status.text()
    finally:
        view.close()


def test_export_without_a_trace_changes_nothing(
    qt_app: QApplication, configured_context: ApplicationContext
) -> None:
    view = DiagnosticsView(configured_context, configured_context.devices)
    try:
        drain(qt_app)
        assert not view._export_button.isEnabled()
        view._export_trace()
        assert "first" in view._status.text()
    finally:
        view.close()


def test_no_destructive_controls_are_offered(
    qt_app: QApplication, configured_context: ApplicationContext
) -> None:
    view = DiagnosticsView(configured_context, configured_context.devices)
    try:
        drain(qt_app)
        labels = [button.text().lower() for button in view._buttons]
        labels.append(view._export_button.text().lower())
        for forbidden in ("write", "delete", "clear", "reset", "set time", "enroll"):
            assert not any(forbidden in label for label in labels), labels
    finally:
        view.close()


def test_non_admin_trace_is_refused_by_the_service(
    qt_app: QApplication, mock_context: ApplicationContext
) -> None:
    from clockmanager.services.devices import DeviceProfile

    mock_context.devices.save_profile(DeviceProfile(name="Bench clock", host="192.0.2.10"))
    view = DiagnosticsView(mock_context, mock_context.devices, role=Role.VIEWER)
    try:
        drain(qt_app)
        view._protocol_trace()
        drain(qt_app)
        assert "not permitted" in view._status.text()
        assert view._trace is None
    finally:
        view.close()
