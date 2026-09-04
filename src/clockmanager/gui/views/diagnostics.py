"""Diagnostics view.

Covers the checks listed in the PHASE 02 specification: test connection, device
info, users, attendance, time, live capture state and safe logs. PHASE 10 adds
admin-only protocol diagnostics: timed connection reports, full protocol
traces (TX/RX, raw + parsed records, live window, capabilities) and a
sanitized JSON export.

``SECURITY.md`` restricts developer/diagnostic views to administrators: the
view is hidden from office staff and viewers, and the service refuses
non-admin roles regardless. The log tail is always safe to show: every
handler passes records through the redacting filter before they reach the
file. Trace hex is redacted inside the protocol layer — credential bytes
are zeroed before anything reaches this widget.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.domain.auth import Permission, Role, normalise_role
from clockmanager.gui.views.common import (
    build_table,
    fill_table,
    page_header,
    role_allows,
    run_off_thread,
    set_status,
)
from clockmanager.services.application import ApplicationContext
from clockmanager.services.devices import ConnectionTestResult, DeviceProfile, DeviceService
from clockmanager.services.diagnostics import ProtocolTrace

__all__ = ["DiagnosticsView"]

_logger = get_logger(__name__)

_LOG_TAIL_LINES = 200


class DiagnosticsView(QWidget):
    """Runs read-only device checks and shows the redacted log tail."""

    def __init__(
        self,
        context: ApplicationContext,
        service: DeviceService,
        parent: QWidget | None = None,
        *,
        role: Role | str | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._service = service
        self._diagnostics = context.diagnostics
        #: The logged-in role. Diagnostics is admin-only: navigation hides
        #: this view from other roles, and the service refuses them anyway.
        #: ``None`` keeps the legacy behaviour for tests.
        self._role = normalise_role(role) if role is not None else None
        self._trace: ProtocolTrace | None = None
        self._live_state = "Not started"

        self._results = build_table(["Item", "Value"], self, sortable=False)
        self._status = QLabel("Select a check to run.", self)
        self._status.setWordWrap(True)
        set_status(self._status, "Select a check to run.", "info")

        self._buttons = [
            self._make_button("Test connection", self._test_connection),
            self._make_button("Device info", self._device_info),
            self._make_button("Device time", self._device_time),
            self._make_button("Count users", self._count_users),
            self._make_button("Count attendance", self._count_attendance),
            self._make_button("Capabilities", self._capabilities),
            self._make_button("Connection report", self._connection_report),
            self._make_button("Protocol trace", self._protocol_trace),
        ]

        self._live_seconds = QSpinBox(self)
        self._live_seconds.setRange(0, 30)
        self._live_seconds.setValue(5)
        self._live_seconds.setSuffix(" s live listen")
        self._live_seconds.setToolTip(
            "How long a protocol trace listens for live events. 0 skips the listen."
        )
        self._export_button = QPushButton("Export last trace…", self)
        self._export_button.clicked.connect(self._export_trace)
        self._export_button.setEnabled(False)
        self._export_button.setToolTip(
            "Save the last protocol trace as sanitized JSON. Credential bytes "
            "are zeroed; the export is audited."
        )

        checks = QHBoxLayout()
        for button in self._buttons:
            checks.addWidget(button)
        checks.addStretch(1)

        trace_options = QHBoxLayout()
        trace_options.addWidget(QLabel("Trace options:", self))
        trace_options.addWidget(self._live_seconds)
        trace_options.addWidget(self._export_button)
        trace_options.addStretch(1)

        results_layout = QVBoxLayout()
        results_layout.addLayout(checks)
        results_layout.addLayout(trace_options)
        results_layout.addWidget(self._status)
        results_layout.addWidget(self._results)
        checks_group = QGroupBox("Device checks (read-only)", self)
        checks_group.setLayout(results_layout)

        self._detail = QPlainTextEdit(self)
        self._detail.setReadOnly(True)
        self._detail.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._detail.setFont(mono)
        self._detail.setPlaceholderText(
            "Run a connection report or protocol trace to see timed steps, "
            "TX/RX traffic and redacted raw records here."
        )
        detail_layout = QVBoxLayout()
        detail_layout.addWidget(
            QLabel(
                "Protocol detail. Raw user records are shown with the credential "
                "region zeroed; nothing here can disclose a PIN.",
                self,
            )
        )
        detail_layout.addWidget(self._detail)
        detail_group = QGroupBox("Protocol detail (admin-only, redacted)", self)
        detail_group.setLayout(detail_layout)

        self._log_view = QPlainTextEdit(self)
        self._log_view.setReadOnly(True)
        self._log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        refresh_logs = QPushButton("Refresh log", self)
        refresh_logs.clicked.connect(self.refresh_logs)

        log_layout = QVBoxLayout()
        log_layout.addWidget(
            QLabel(
                "Application log. Sensitive values are redacted before they are "
                "written, so this view cannot disclose a credential.",
                self,
            )
        )
        log_layout.addWidget(refresh_logs)
        log_layout.addWidget(self._log_view)
        log_group = QGroupBox("Safe logs", self)
        log_group.setLayout(log_layout)

        layout = QVBoxLayout()
        layout.addWidget(
            page_header(
                "Diagnostics",
                "Connection timings and protocol traces for troubleshooting. Administrators only; exports are sanitised.",
            )
        )
        layout.addWidget(checks_group, stretch=1)
        layout.addWidget(detail_group, stretch=1)
        layout.addWidget(log_group, stretch=1)

        if not context.config.developer_mode:
            note = QLabel(
                "Developer mode is off. Capability reasons are hidden; enable "
                "developer mode to see them. Protocol traces stay available: "
                "they are admin-only and redact credential bytes.",
                self,
            )
            note.setWordWrap(True)
            layout.addWidget(note)

        self.setLayout(layout)
        self.refresh_logs()

    def _make_button(self, text: str, handler: Any) -> QPushButton:
        button = QPushButton(text, self)
        button.clicked.connect(handler)
        return button

    # -- live capture state ---------------------------------------------------

    def set_live_capture_state(self, state: str) -> None:
        """Record the live-capture state so it can be reported here."""
        self._live_state = state

    # -- checks ---------------------------------------------------------------

    def _profile_or_warn(self) -> DeviceProfile | None:
        profile = self._service.first_enabled_profile()
        if profile is None or not profile.is_configured:
            self._status.setText("No device is configured. Add one in Device settings.")
            fill_table(self._results, [])
            return None
        return profile

    def _run(self, work: Any, *, description: str) -> None:
        profile = self._profile_or_warn()
        if profile is None:
            return
        self._set_busy(True)
        self._status.setText(f"{description}…")
        run_off_thread(
            lambda: work(profile),
            on_success=self._on_result,
            on_failure=self._on_failure,
        )

    def _test_connection(self) -> None:
        self._run(self._service.test_connection, description="Testing connection")

    def _device_info(self) -> None:
        self._run(self._service.read_device_info, description="Reading device info")

    def _device_time(self) -> None:
        def work(profile: DeviceProfile) -> list[tuple[str, str]]:
            info = self._service.read_device_info(profile)
            device_time = info.device_time
            rows = [
                (
                    "Device time",
                    "Not reported" if device_time is None else device_time.isoformat(sep=" "),
                ),
                ("This computer", datetime.now().isoformat(sep=" ", timespec="seconds")),  # noqa: DTZ005
            ]
            if device_time is not None:
                drift = abs((datetime.now() - device_time).total_seconds())  # noqa: DTZ005
                rows.append(("Difference", f"{drift:.0f} seconds"))
                rows.append(
                    (
                        "Note",
                        "The device reports local time with no timezone. Setting "
                        "the device clock is not supported in this build.",
                    )
                )
            return rows

        self._run(work, description="Reading device time")

    def _count_users(self) -> None:
        def work(profile: DeviceProfile) -> list[tuple[str, str]]:
            users = self._service.read_users(profile)
            admins = sum(1 for user in users if user.is_admin)
            unknown = sum(1 for user in users if user.privilege_label.startswith("Unknown"))
            with_credential = sum(1 for user in users if user.has_credential_data)
            return [
                ("Users read", str(len(users))),
                ("Admins", str(admins)),
                ("Unrecognised privilege values", str(unknown)),
                ("Credential region populated", str(with_credential)),
                ("Parser", "Application-owned 120-byte MB1 record parser"),
            ]

        self._run(work, description="Reading users")

    def _count_attendance(self) -> None:
        def work(profile: DeviceProfile) -> list[tuple[str, str]]:
            events = self._service.read_attendance(profile)
            if not events:
                return [("Attendance records", "0")]
            times = [event.occurred_at for event in events]
            return [
                ("Attendance records", str(len(events))),
                ("Earliest", min(times).isoformat(sep=" ")),
                ("Latest", max(times).isoformat(sep=" ")),
                ("IN punches", str(sum(1 for e in events if e.punch == 0))),
                ("OUT punches", str(sum(1 for e in events if e.punch == 1))),
            ]

        self._run(work, description="Reading attendance")

    def _capabilities(self) -> None:
        def work(profile: DeviceProfile) -> list[tuple[str, str]]:
            capabilities = self._service.capabilities(profile)
            rows: list[tuple[str, str]] = [("Live capture state", self._live_state)]
            for name, support, reason in capabilities.as_rows():
                value = support.upper()
                if self._context.config.developer_mode:
                    value = f"{support.upper()} — {reason}"
                rows.append((name, value))
            return rows

        self._run(work, description="Reading capabilities")

    # -- PHASE 10 protocol diagnostics (admin-only, read-only) ------------------

    def _connection_report(self) -> None:
        profile = self._profile_or_warn()
        if profile is None:
            return
        self._set_busy(True)
        self._status.setText("Running connection diagnostics…")
        run_off_thread(
            lambda: self._diagnostics.connection_report(profile, requester_role=self._role),
            on_success=self._on_report,
            on_failure=self._on_failure,
        )

    def _on_report(self, report: Any) -> None:
        from clockmanager.services.diagnostics import ConnectionReport as _Report

        self._set_busy(False)
        if not isinstance(report, _Report):  # pragma: no cover - defensive
            return
        self._status.setText(report.summary)
        fill_table(self._results, [list(row) for row in report.as_rows()])
        self._detail.setPlainText(
            "\n".join(
                f"{step.name} ({step.duration_ms:.1f} ms): {step.detail}" for step in report.steps
            )
            or "No steps recorded."
        )

    def _protocol_trace(self) -> None:
        profile = self._profile_or_warn()
        if profile is None:
            return
        live_seconds = float(self._live_seconds.value())
        self._set_busy(True)
        self._status.setText("Capturing protocol trace…")
        run_off_thread(
            lambda: self._diagnostics.protocol_trace(
                profile, live_seconds=live_seconds, requester_role=self._role
            ),
            on_success=self._on_trace,
            on_failure=self._on_failure,
        )

    def _on_trace(self, trace: Any) -> None:
        self._set_busy(False)
        if not isinstance(trace, ProtocolTrace):  # pragma: no cover - defensive
            return
        self._trace = trace
        self._export_button.setEnabled(trace.ok and self._may_manage())
        self._status.setText(trace.summary)
        fill_table(
            self._results,
            [
                ["Result", "Passed" if trace.ok else "Failed"],
                ["Endpoint", trace.endpoint],
                ["Total time", f"{trace.total_ms:.0f} ms"],
                ["Transport", trace.transport_note],
                [
                    "Users",
                    "unread"
                    if trace.users is None
                    else f"{trace.users.record_count} record(s), "
                    f"{trace.users.total_bytes} bytes"
                    + (" (list truncated)" if trace.users.truncated else ""),
                ],
                [
                    "Attendance",
                    "unread"
                    if trace.attendance is None
                    else f"{trace.attendance.parsed_count} parsed"
                    + (
                        f", {trace.attendance.payload_bytes} bytes"
                        if trace.attendance.payload_bytes is not None
                        else " (no raw payload)"
                    ),
                ],
                [
                    "Live listen",
                    "skipped"
                    if trace.live is None or trace.live.skipped
                    else f"{trace.live.collected} event(s), "
                    f"{trace.live.idle_timeouts} idle timeout(s)",
                ],
            ],
        )
        self._detail.setPlainText(self._render_trace(trace))

    def _render_trace(self, trace: ProtocolTrace) -> str:
        """Plain-text rendering of a trace: events, raw hex, parsed records."""
        lines = [trace.summary, "", trace.transport_note, "", "== Events =="]
        for event in trace.events:
            timing = "" if event.duration_ms is None else f" [{event.duration_ms:.1f} ms]"
            size = "" if event.bytes_count is None else f" ({event.bytes_count} B)"
            lines.append(
                f"{event.seq:3d} {event.stage}/{event.direction} {event.label}{timing}{size}"
            )
            if event.detail:
                for detail_line in event.detail.splitlines():
                    lines.append(f"         {detail_line}")
        if trace.users is not None:
            lines.extend(["", "== User records (credential region zeroed) =="])
            for record in trace.users.records:
                lines.append(
                    f"UID {record.device_uid} {record.user_id} "
                    f"({record.first_name} {record.last_name}) "
                    f"[{record.privilege_label}]"
                    + (" +credential" if record.has_credential_data else "")
                )
                lines.append(record.redacted_hex)
            if trace.users.truncated:
                lines.append(f"(showing {len(trace.users.records)} of {trace.users.record_count})")
        if trace.attendance is not None:
            lines.extend(["", "== Attendance =="])
            if trace.attendance.error:
                lines.append(f"Error: {trace.attendance.error}")
            else:
                lines.append(
                    f"{trace.attendance.parsed_count} event(s)"
                    + (
                        f", {trace.attendance.payload_bytes} bytes, "
                        f"{trace.attendance.record_size}-byte records, "
                        f"declared {trace.attendance.declared_size}"
                        if trace.attendance.payload_bytes is not None
                        else " (parsed only, no raw payload)"
                    )
                )
                for item in trace.attendance.events:
                    lines.append(
                        f"{item['occurred_at']} {item['user_id']} "
                        f"{item['punch']} (status {item['status']})"
                    )
                if trace.attendance.note:
                    lines.append("Payload hex:")
                    lines.append(trace.attendance.note)
        if trace.live is not None and not trace.live.skipped:
            lines.extend(["", "== Live events =="])
            if trace.live.error:
                lines.append(f"Error: {trace.live.error}")
            for item in trace.live.events:
                lines.append(
                    f"{item['occurred_at']} {item['user_id']} "
                    f"{item['punch']} (status {item['status']})"
                )
            if not trace.live.events:
                lines.append("(no live events arrived during the listen)")
        lines.extend(["", "== Capabilities =="])
        for name, support, reason in trace.capabilities:
            lines.append(f"{name}: {support.upper()}")
            if self._context.config.developer_mode:
                lines.append(f"    {reason}")
        if trace.error:
            lines.extend(["", f"Trace error: {trace.error}"])
        return "\n".join(lines)

    def _export_trace(self) -> None:
        trace = self._trace
        if trace is None or not trace.ok:
            self._status.setText("Run a protocol trace first, then export it.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export diagnostics", "diagnostics.json", "*.json"
        )
        if not path:
            return
        self._set_busy(True)
        self._status.setText("Exporting diagnostics…")
        run_off_thread(
            lambda: self._do_export(trace, path),
            on_success=self._on_exported,
            on_failure=self._on_failure,
        )

    def _do_export(self, trace: ProtocolTrace, path: str) -> str:
        from pathlib import Path

        data, filename, _mime = self._diagnostics.export_trace(trace, requester_role=self._role)
        target = Path(path)
        target.write_bytes(data)
        return f"Exported {len(data)} byte(s) to {target.name or filename}."

    def _on_exported(self, message: Any) -> None:
        self._set_busy(False)
        self._status.setText(str(message))

    def _may_manage(self) -> bool:
        return role_allows(self._role, Permission.VIEW_DIAGNOSTICS)

    # -- results --------------------------------------------------------------

    def _on_result(self, result: Any) -> None:
        self._set_busy(False)

        if isinstance(result, ConnectionTestResult):
            self._status.setText(result.summary)
            fill_table(self._results, [list(row) for row in result.as_rows()])
            return

        if hasattr(result, "as_rows"):
            self._status.setText("Done.")
            fill_table(self._results, [list(row) for row in result.as_rows()])
            return

        if isinstance(result, list):
            self._status.setText("Done.")
            fill_table(self._results, [[str(a), str(b)] for a, b in result])
            return

        self._status.setText("Done.")  # pragma: no cover - defensive

    def _on_failure(self, message: str) -> None:
        self._set_busy(False)
        self._status.setText(message)

    def _set_busy(self, busy: bool) -> None:
        for button in self._buttons:
            button.setEnabled(not busy)
        self._export_button.setEnabled(
            not busy and self._trace is not None and self._trace.ok and self._may_manage()
        )

    # -- logs -----------------------------------------------------------------

    def refresh_logs(self) -> None:
        """Load the tail of the redacted application log off the UI thread."""
        run_off_thread(
            self._read_log_tail,
            on_success=self._on_log_loaded,
            on_failure=lambda message: self._log_view.setPlainText(message),
        )

    def _read_log_tail(self) -> str:
        log_file = self._context.log_file
        if not log_file.exists():
            return "No log file yet."
        with log_file.open("r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
        return "".join(lines[-_LOG_TAIL_LINES:])

    def _on_log_loaded(self, text: Any) -> None:
        self._log_view.setPlainText(str(text))
        self._log_view.verticalScrollBar().setValue(self._log_view.verticalScrollBar().maximum())
