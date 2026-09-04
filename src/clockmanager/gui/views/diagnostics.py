"""Diagnostics view.

Covers the checks listed in the PHASE 02 specification: test connection, device
info, users, attendance, time, live capture state and safe logs.

``SECURITY.md`` restricts developer/diagnostic views to administrators. Until
roles arrive in PHASE 07, the raw-detail sections are gated behind
``developer_mode``. The log tail is always safe to show: every handler passes
records through the redacting filter before they reach the file.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clockmanager.diagnostics.logging_setup import get_logger
from clockmanager.gui.views.common import build_table, fill_table, run_off_thread, section_label
from clockmanager.services.application import ApplicationContext
from clockmanager.services.devices import ConnectionTestResult, DeviceProfile, DeviceService

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
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._service = service
        self._live_state = "Not started"

        self._results = build_table(["Item", "Value"], self, sortable=False)
        self._status = QLabel("Select a check to run.", self)
        self._status.setWordWrap(True)

        self._buttons = [
            self._make_button("Test connection", self._test_connection),
            self._make_button("Device info", self._device_info),
            self._make_button("Device time", self._device_time),
            self._make_button("Count users", self._count_users),
            self._make_button("Count attendance", self._count_attendance),
            self._make_button("Capabilities", self._capabilities),
        ]

        checks = QHBoxLayout()
        for button in self._buttons:
            checks.addWidget(button)
        checks.addStretch(1)

        results_layout = QVBoxLayout()
        results_layout.addLayout(checks)
        results_layout.addWidget(self._status)
        results_layout.addWidget(self._results)
        checks_group = QGroupBox("Device checks (read-only)", self)
        checks_group.setLayout(results_layout)

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
        layout.addWidget(section_label("Diagnostics", self))
        layout.addWidget(checks_group, stretch=1)
        layout.addWidget(log_group, stretch=1)

        if not context.config.developer_mode:
            note = QLabel(
                "Developer mode is off. Raw protocol detail is hidden; enable "
                "developer mode to see it.",
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
